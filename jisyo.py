#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Collect versioned public lexicons and export RustMigemo dictionaries (stdlib only)."""
import argparse
from collections import Counter, namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import html
import io
import itertools
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'data/raw'
LOCK = ROOT / 'sources.lock.json'
DB = ROOT / 'data/lexicon.sqlite3'
KATAKANA, KANA = re.compile('[ァ-ヺー・ヽヾ]+'), re.compile('[ぁ-ゖァ-ヺー・ゝゞヽヾ]+')
READING_SEPARATORS = str.maketrans('', '', '・、。「」【】')
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
# kind: SKK okuri-nasi/okuri-ari; scope: all, skk-only or migemo-only; cost: Sudachi; pri: JMdict priority tags.
Entry = namedtuple('Entry', 'reading surface pos kind scope cost pri', defaults=('nasi', 'all', None, None))
# Formats that only rank candidates: stored in the costs table, never in entries/exports as vocabulary.
RANKING_FORMATS = {'mozc'}


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'myhugejisyo/0.1'}), timeout=90)


def remote_json(url):
    with request(url) as r:
        return json.load(r)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def catalog():
    """Resolve moving upstream references once. Downloads are pinned by SHA-256."""
    gh = 'https://api.github.com/repos/'
    skk_rev = remote_json(gh + 'skk-dev/dict/commits/master')['sha']
    mozc_rev = remote_json(gh + 'google/mozc/commits/master')['sha']
    sudachi = remote_json(gh + 'WorksApplications/SudachiDict/releases/latest')['tag_name']
    if not re.fullmatch(r'v\d{8}', sudachi):
        raise ValueError(f'Unknown Sudachi version/CSV layout: {sudachi}; review before importing')
    wiki = remote_json(gh + 'tokuhirom/jawiki-kana-kanji-dict/releases/latest')
    sources = []

    def add(id, format, url, version, license, homepage, notices=(), **extra):
        sources.append(dict(id=id, format=format, url=url, version=version,
                            license=license, homepage=homepage, notices=list(notices), **extra))

    for name in ['L', 'jinmei', 'geo', 'station', 'propernoun', 'law', 'hukugougo', 'JIS2004']:
        add('skk-' + name.lower(), 'skk', f'https://raw.githubusercontent.com/skk-dev/dict/{skk_rev}/SKK-JISYO.{name}', skk_rev,
            'GPL-2.0-or-later; see source header', 'https://github.com/skk-dev/dict', encoding='euc_jis_2004' if name == 'JIS2004' else 'euc_jp')
    sudachi_tree = remote_json(gh + 'WorksApplications/SudachiDict/git/trees/' + sudachi)['tree']
    additional_notices = [entry['path'] for entry in sudachi_tree
                          if entry['type'] == 'blob' and entry['path'].upper().split('.')[0] == 'NOTICE']
    for part in ['small', 'core', 'notcore']:
        base = f'https://raw.githubusercontent.com/WorksApplications/SudachiDict/{sudachi}/'
        add('sudachi-' + part, 'sudachi', f'https://sudachi.s3.ap-northeast-1.amazonaws.com/sudachidict-raw/{sudachi[1:]}/{part}_lex.zip', sudachi,
            'Apache-2.0 AND bundled upstream notices', 'https://github.com/WorksApplications/SudachiDict',
            [base + 'LICENSE-2.0.txt', base + 'LEGAL'] + [base + name for name in additional_notices])
    mozc = f'https://raw.githubusercontent.com/google/mozc/{mozc_rev}/'
    for i in range(10):
        add(f'mozc-{i:02}', 'mozc', mozc + f'src/data/dictionary_oss/dictionary{i:02}.txt', mozc_rev,
            'BSD-3-Clause AND IPAdic (NAIST/ICOT) terms AND public-domain Okinawa dictionary; costs used for candidate order only',
            'https://github.com/google/mozc', [mozc + 'LICENSE', mozc + 'src/data/dictionary_oss/README.txt'])
    asset = next(a for a in wiki['assets'] if a['name'] == 'SKK-JISYO.jawiki')
    wiki_base = 'https://raw.githubusercontent.com/tokuhirom/jawiki-kana-kanji-dict/' + wiki['tag_name'] + '/'
    add('jawiki', 'skk', asset['browser_download_url'], wiki['tag_name'],
        'MIT declared by upstream; Wikipedia extraction rationale in README', 'https://github.com/tokuhirom/jawiki-kana-kanji-dict',
        [wiki_base + 'README.md'], encoding='utf-8')
    for id, name in [('jmdict', 'JMdict_e.gz'), ('jmnedict', 'JMnedict.xml.gz')]:
        add(id, 'jmdict', 'https://www.edrdg.org/pub/Nihongo/' + name, datetime.now(timezone.utc).strftime('%Y-%m-%d') + ' daily snapshot',
            'CC-BY-SA-4.0', 'https://www.edrdg.org/', ['https://www.edrdg.org/edrdg/licence.html'],
            attribution='JMdict/JMnedict: Copyright James William BREEN and the Electronic Dictionary Research and Development Group')
    page = 'https://sociocom.naist.jp/download/jmed-dict-mini/'
    with request(page) as r:
        page_text = r.read().decode()
    match = re.search(r'data-downloadurl="([^"]+)"', page_text)
    if not match:
        raise ValueError('JMED official download link changed')
    add('jmed', 'jmed', html.unescape(match[1]), 'version recorded in ZIP member names', 'CC-BY-4.0 (reading/surface only)',
        page, [page], attribution='NAIST Social Computing Lab., JMED-DICT working group')
    return sources


def download(source, refresh=False):
    folder = CACHE / source['id']
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'source'
    receipt = folder / 'receipt.json'
    if not refresh and not source.get('sha256') and receipt.exists():
        cached = json.loads(receipt.read_text())
        if all(cached.get(k) == source.get(k) for k in ('url', 'version', 'notices')):
            source = cached
    if source.get('sha256') and path.exists() and digest(path) == source['sha256']:
        pass
    else:
        tmp = folder / 'source.part'
        for attempt in range(3):
            try:
                with request(source['url']) as r, tmp.open('wb') as f:
                    shutil.copyfileobj(r, f)
                    modified = r.headers.get('Last-Modified')
                checksum = digest(tmp)
                if source.get('sha256') and checksum != source['sha256']:
                    raise ValueError(f"{source['id']}: upstream changed; run fetch --update explicitly")
                tmp.replace(path)
                source.update(sha256=checksum, bytes=path.stat().st_size,
                              fetched_at=datetime.now(timezone.utc).isoformat(), last_modified=modified)
                break
            except Exception:
                tmp.unlink(missing_ok=True)
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    notice_records = source.get('notice_files', [])
    if not notice_records:
        notice_records = [dict(url=url, file=f'notice-{i}.txt') for i, url in enumerate(source['notices'])]
    for notice in notice_records:
        dest = folder / notice['file']
        if dest.exists() and notice.get('sha256') == digest(dest):
            continue
        with request(notice['url']) as r:
            content = r.read()
        sha = hashlib.sha256(content).hexdigest()
        if notice.get('sha256') and notice['sha256'] != sha:
            raise ValueError(f"{source['id']}: notice changed; run fetch --update")
        dest.write_bytes(content)
        notice['sha256'] = sha
    source['notice_files'] = notice_records
    write_json(receipt, source)
    print(f"fetched {source['id']}: {source['bytes']:,} bytes", flush=True)
    return source


def normalize(reading):
    reading = unicodedata.normalize('NFKC', reading.strip()).lower()
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in reading)


def search_key(reading):
    return normalize(reading).translate(READING_SEPARATORS)


def supported(reading):
    return bool(reading) and all(' ' <= c <= '~' or 'ぁ' <= c <= 'ゖ' or c == 'ー' for c in reading)


def parse_skk(stream, stats):
    okuri = False
    for line in stream:
        if line.startswith(';; okuri-ari entries.'):
            okuri = True
        elif line.startswith(';; okuri-nasi entries.'):
            okuri = False
        if line.startswith(';') or not line.strip():
            continue
        key, sep, rest = line.rstrip('\n').partition(' ')
        if not sep or not rest.startswith('/') or not rest.endswith('/'):
            stats['skk_malformed_entry'] += 1
            continue
        # SKK numbers are templates, not literal readings; okuri keys belong to the okuri-ari section.
        if '#' in key or not okuri and re.search('[ぁ-ゖー][a-z]$', key):
            stats['skk_template_entry'] += 1
            continue
        if okuri and not re.search('[ぁ-ゖー][a-z]$', key):
            stats['skk_malformed_entry'] += 1
            continue
        if okuri:
            stats['skk_okuri_entry'] += 1
        # Lines with Lisp/okuri-block candidates keep their plain candidates for SKK only.
        rescued = '/(' in rest or '/[' in rest
        if rescued:
            stats['skk_expression_entry'] += 1
        depth, block = 0, False
        for candidate in rest[1:-1].split('/'):
            if depth or candidate.startswith('('):
                depth = max(depth + candidate.count('(') - candidate.count(')'), 0)
                stats['skk_expression_candidate'] += 1
                continue
            if block or candidate.startswith('['):
                block = candidate != ']'
                stats['skk_expression_candidate'] += 1
                continue
            surface = candidate.split(';', 1)[0]
            if not surface or surface.startswith(('(', '[', ']')) or '#' in surface:
                stats['skk_special_candidate'] += 1
                continue
            yield Entry(key, surface, '', 'ari' if okuri else 'nasi', 'skk' if okuri or rescued else 'all')


def parse_jmdict(stream, stats):
    events = ET.iterparse(stream, events=('start', 'end'))
    _, root = next(events)
    for event, entry in events:
        if event != 'end' or entry.tag != 'entry':
            continue
        kanji = {k.findtext('keb'): {p.text for p in k.findall('ke_pri')} for k in entry.findall('k_ele')}
        forms = list(kanji)
        pos = '|'.join(sorted({p.text for p in entry.findall('sense/pos') + entry.findall('trans/name_type') if p.text}))
        for reading in entry.findall('r_ele'):
            key = reading.findtext('reb', '')
            restricted = [r.text for r in reading.findall('re_restr')]
            if reading.find('re_nokanji') is not None or not forms:
                values = [key]
            else:
                values = [s for s in forms if not restricted or s in restricted]
            tags = {p.text for p in reading.findall('re_pri')}
            for surface in values:
                # A kanji form gets the tags its ke_pri shares with this reading's re_pri: 客 is news1 as きゃく, not as かく.
                # A katakana reading's re_pri stays; candidate_rows moves it to that loanword spelling.
                own = kanji.get(surface, set())
                pri = own | tags if surface not in kanji or KATAKANA.fullmatch(key) else own & tags
                yield Entry(key, surface, pos, pri='|'.join(sorted(pri)) or None)
        root.clear()


def xlsx_rows(data):
    # Read only cells; never execute formulas, macros or external links.
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        shared = []
        if 'xl/sharedStrings.xml' in book.namelist():
            root = ET.fromstring(book.read('xl/sharedStrings.xml'))
            shared = [''.join(si.itertext()) for si in root]
        with book.open('xl/worksheets/sheet1.xml') as sheet:
            events = ET.iterparse(sheet, events=('start', 'end'))
            _, root = next(events)
            sheet_data = None
            for event, row in events:
                if event == 'start' and row.tag == '{' + NS['m'] + '}sheetData':
                    sheet_data = row
                if event != 'end' or row.tag != '{' + NS['m'] + '}row':
                    continue
                values = {}
                for cell in row:
                    column = re.sub(r'\d', '', cell.get('r', ''))
                    value = cell.findtext('m:v', '', NS)
                    if cell.get('t') == 's':
                        value = shared[int(value)]
                    elif cell.get('t') == 'inlineStr':
                        inline = cell.find('m:is', NS)
                        value = ''.join(inline.itertext()) if inline is not None else ''
                    values[column] = value
                yield values
                row.clear()
                if sheet_data is not None:
                    sheet_data.clear()


def unescape_sudachi(text):
    # Same Unicode escape grammar as upstream CsvLexicon.unescape.
    return re.sub(r'\\u([0-9a-fA-F]{4}|\{[0-9a-fA-F]+\})',
                  lambda m: chr(int(m[1].strip('{}'), 16)), text)


def rows(source, stats):
    path = CACHE / source['id'] / 'source'
    if digest(path) != source['sha256']:
        raise ValueError(f"Checksum mismatch: {path}")
    fmt = source['format']
    if fmt == 'skk':
        with path.open(encoding=source['encoding']) as f:
            yield from parse_skk(f, stats)
    elif fmt == 'sudachi':
        with zipfile.ZipFile(path) as archive:
            members = [n for n in archive.namelist() if n.endswith('_lex.csv')]
            if len(members) != 1:
                raise ValueError('Unexpected Sudachi archive members')
            with archive.open(members[0]) as raw:
                for row in csv.reader(io.TextIOWrapper(raw, encoding='utf-8')):
                    if len(row) < 18:
                        raise ValueError('Unexpected Sudachi CSV layout')
                    # Column 4 is original output surface; 0 is normalized trie key.
                    # Inflected forms (column 10) help Migemo but are not SKK okuri-nasi words.
                    inflected = row[10] != '*' and not row[10].startswith('終止形')
                    yield Entry(unescape_sudachi(row[11]), unescape_sudachi(row[4]), '|'.join(row[5:11]),
                                scope='migemo' if inflected else 'all', cost=int(row[3]))
    elif fmt == 'mozc':
        # reading, left id, right id, cost, surface[, flag]; flagged rows (SPELLING_CORRECTION) have misspelled readings.
        with path.open(encoding='utf-8', newline='') as f:
            for line in f:
                row = line.rstrip('\n').split('\t')
                if len(row) not in (5, 6) or not row[3].isdigit():
                    raise ValueError('Unexpected Mozc dictionary layout')
                if len(row) == 6:
                    stats['mozc_flagged_row'] += 1
                    continue
                yield Entry(row[0], row[4], '', cost=int(row[3]))
    elif fmt == 'jmdict':
        with gzip.open(path) as f:
            yield from parse_jmdict(f, stats)
    elif fmt == 'reading-tsv':
        with path.open(encoding='utf-8', newline='') as f:
            for row in csv.reader(f, delimiter='\t'):
                if len(row) != 2:
                    raise ValueError('Expected reading TAB surface')
                yield Entry(row[0], row[1], 'materialized vocabulary')
    elif fmt == 'jmed':
        with zipfile.ZipFile(path) as archive:
            members = [n for n in archive.namelist() if n.endswith('_mini.xlsx')]
            if not members:
                raise ValueError('JMED ZIP contains no mini workbooks')
            for name in members:
                iterator = iter(xlsx_rows(archive.read(name)))
                header = next(iterator)
                columns = {v.strip(): k for k, v in header.items()}
                rc, sc = columns['出現形よみ'], columns['出現形']
                for row in iterator:
                    yield Entry(row.get(rc, ''), row.get(sc, ''), Path(name).stem)
    else:
        raise ValueError(f'Unknown format: {fmt}')


def ingest(sources):
    DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix('.tmp')
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript('''
      PRAGMA journal_mode=OFF;
      PRAGMA cache_size=-131072;
      CREATE TABLE sources(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
      CREATE TABLE entries(reading TEXT, surface TEXT, source TEXT, kind TEXT NOT NULL CHECK(kind IN ('nasi','ari')),
        scope TEXT NOT NULL CHECK(scope IN ('all','skk','migemo')), rank INTEGER NOT NULL, cost INTEGER, pri TEXT,
        pos TEXT, original_reading TEXT, confidence TEXT NOT NULL DEFAULT 'upstream-provided',
        PRIMARY KEY(reading,surface,source,kind)) WITHOUT ROWID;
      CREATE TABLE costs(reading TEXT, surface TEXT, source TEXT, cost INTEGER NOT NULL,
        PRIMARY KEY(reading,surface,source)) WITHOUT ROWID;
    ''')
    # Repeated keys within a source keep the first rank/pos/reading, the lowest cost and the widest scope.
    insert = '''INSERT INTO entries(reading,surface,source,kind,scope,rank,cost,pri,pos,original_reading) VALUES (?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(reading,surface,source,kind) DO UPDATE SET rank=min(rank,excluded.rank),
        scope=CASE WHEN scope=excluded.scope THEN scope ELSE 'all' END,
        cost=coalesce(min(cost,excluded.cost),cost,excluded.cost), pri=coalesce(pri,excluded.pri)'''
    # Ranking-only sources (Mozc): lowest cost per normalized reading/surface, e.g. over Mozc's POS ids.
    insert_cost = '''INSERT INTO costs VALUES (?,?,?,?) ON CONFLICT(reading,surface,source) DO UPDATE SET cost=min(cost,excluded.cost)'''
    report = {}
    try:
        for source in sources:
            stats = Counter()
            con.execute('INSERT INTO sources VALUES (?,?)', (source['id'], json.dumps(source, ensure_ascii=False)))
            ranking = source.get('format') in RANKING_FORMATS
            table, statement = ('costs', insert_cost) if ranking else ('entries', insert)
            batch = []
            for rank, (reading, surface, pos, kind, scope, cost, pri) in enumerate(rows(source, stats)):
                stats['parsed_pairs'] += 1
                original_reading = reading
                normalized = normalize(reading)
                reading = search_key(reading)
                if reading != normalized:
                    stats['separator_joined_pairs'] += 1
                if reading == '*' or not supported(reading):
                    stats['unsupported_reading'] += 1
                    continue
                if not surface or surface == '*' or any(unicodedata.category(c) in ('Cc', 'Cs') for c in surface):
                    stats['invalid_surface'] += 1
                    continue
                batch.append((reading, surface, source['id'], cost) if ranking else
                             (reading, surface, source['id'], kind, scope, rank, cost, pri, pos, original_reading))
                if len(batch) >= 10000:
                    con.executemany(statement, batch)
                    batch.clear()
            con.executemany(statement, batch)
            stats['unique_source_pairs'] = con.execute(f'SELECT COUNT(*) FROM {table} WHERE source=?', (source['id'],)).fetchone()[0]
            if not stats['unique_source_pairs']:
                raise ValueError(f"{source['id']} produced no vocabulary")
            report[source['id']] = dict(stats)
            con.commit()
            print(f"imported {source['id']}: {dict(stats)}", flush=True)
        report['totals'] = dict(readings=con.execute("SELECT COUNT(DISTINCT reading) FROM entries WHERE kind='nasi'").fetchone()[0],
                                pairs=con.execute("SELECT COUNT(*) FROM (SELECT reading,surface FROM entries WHERE kind='nasi' GROUP BY reading,surface)").fetchone()[0],
                                okuri_ari_pairs=con.execute("SELECT COUNT(*) FROM (SELECT reading,surface FROM entries WHERE kind='ari' GROUP BY reading,surface)").fetchone()[0],
                                ranking_pairs=con.execute('SELECT COUNT(*) FROM (SELECT reading,surface FROM costs GROUP BY reading,surface)').fetchone()[0])
        con.close()
        tmp.replace(DB)
        write_json(ROOT / 'data/import-report.json', report)
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise


def selection(ids):
    if not DB.exists():
        raise ValueError('Run ingest first')
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    sources = [json.loads(row[0]) for row in con.execute('SELECT metadata FROM sources ORDER BY id')]
    selected = set(ids) if ids else {s['id'] for s in sources}
    unknown = selected - {s['id'] for s in sources}
    if unknown:
        con.close()
        raise ValueError(f'Unknown source IDs: {sorted(unknown)}')
    return con, sources, sorted(selected), ' WHERE source IN (' + ','.join('?' for _ in selected) + ')'


def write_export(output, records, sources, selected, transformation):
    """Write TAB-separated records atomically, then the manifest and upstream notices."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + '.tmp')
    count = 0
    with tmp.open('w', encoding='utf-8', newline='') as f:
        for record in records:
            f.write('\t'.join(record) + '\n')
            count += 1
    if not count:
        tmp.unlink(missing_ok=True)
        raise ValueError('No pairs selected')
    tmp.replace(output)
    manifest = dict(pairs=count, sha256=digest(output), sources=[s for s in sources if s['id'] in selected],
                    transformation=transformation)
    write_json(str(output) + '.manifest.json', manifest)
    notices = output.parent / (output.name + '.notices')
    notices.mkdir(exist_ok=True)
    for source in manifest['sources']:
        folder = notices / source['id']
        folder.mkdir(exist_ok=True)
        for notice in source['notice_files']:
            path = CACHE / source['id'] / notice['file']
            if digest(path) != notice['sha256']:
                raise ValueError(f'Notice checksum mismatch: {path}')
            shutil.copy2(path, folder / notice['file'])
        if source['format'] == 'skk':
            with (CACHE / source['id'] / 'source').open(encoding=source['encoding']) as f:
                header = []
                for line in f:
                    if not line.startswith(';'):
                        break
                    header.append(line)
            (folder / 'source-header.txt').write_text(''.join(header))
        if source['format'] == 'jmed':
            with zipfile.ZipFile(CACHE / source['id'] / 'source') as z:
                for name in z.namelist():
                    if name.endswith('README.pdf'):
                        (folder / 'README.pdf').write_bytes(z.read(name))
    print(f'exported {count:,} pairs: {output}', flush=True)
    return manifest


def export(ids, output):
    """RustMigemo input: reading TAB surface, okuri-nasi entries not restricted to SKK."""
    con, sources, selected, where = selection(ids)
    query = ('SELECT reading,surface FROM entries' + where + " AND kind='nasi' AND scope IN ('all','migemo')"
             ' GROUP BY reading,surface ORDER BY reading,surface')
    try:
        return write_export(output, con.execute(query, selected), sources, selected,
                            'NFKC/lowercase/hiragana readings; Japanese separator punctuation removed from search keys (original readings in SQLite); original surfaces; duplicate reading/surface pairs merged; see import-report.json for exclusions')
    finally:
        con.close()


# Okuri-nasi order: skk-l candidates in SKK-JISYO.L order, then a pseudo-cost on the Sudachi small_lex cost scale
# (lower first), estimated from dictionary signals (Mozc OSS costs are ranking-only); scripts/eval_order.py measures it.
# JMdict nf01-06,07-12,..,43-48, other *1, other *2: median sudachi-small cost of the pairs having both, by best tag
# (gai1 pooled with *1), made monotone by pooling adjacent violators.
NF_COST = (6517, 7212, 7360, 7401, 7404, 7404, 7488, 7488)
PRI_COST = {'1': 7736, '2': 7906}
# sudachi-small costs katakana low for segmentation: JMdict pairs without priority, median non-katakana 7790 - katakana 2148.
KATAKANA_SHIFT = 5642
# Mozc OSS cost (lowest over its POS ids) -> sudachi-small cost: quantiles 1,2,5,10,15,..,95,98,99% of both costs over the
# 117,750 normalized pairs having both (sudachi cost != 0; kana-only and ASCII surfaces excluded), first knot of each run
# of equal Sudachi values kept (strictly increasing), linear in between, clamped outside. Derived once from Mozc a4541df.
MOZC_KNOTS = ((2708, 5090), (3294, 5848), (4052, 6517), (4628, 6569), (4991, 7288), (5274, 7401), (5514, 7404), (5908, 7725),
              (6082, 7864), (6417, 8302), (6585, 8995), (6759, 9219), (6945, 9428), (7139, 9770), (7370, 9813), (7614, 10376),
              (7928, 10617), (9104, 11137), (10545, 11676), (10824, 11862))
# Kana-only Mozc rows under this cost are function-word/affix POS artifacts (シマウ 0, ヲ 0, ツモリ 0), not usage.
MOZC_KANA_FLOOR = 1000
# Words missing from a selected Mozc are rarer than their Sudachi/JMdict signal: best hold-out shift (0-6000 tried).
MOZC_ABSENT_SHIFT = 3000
# Pairs without signal follow ~96% of sudachi-small costs, by source class; ASCII under kana keys after those.
DEFAULT_COST = dict.fromkeys(['jmdict', 'skk-hukugougo', 'skk-law', 'skk-jis2004'], 12000) | dict.fromkeys(
    ['sudachi-small', 'sudachi-core', 'sudachi-notcore'], 13000) | dict.fromkeys(['skk-jinmei', 'skk-geo', 'skk-station', 'skk-propernoun'], 14000) | {
    'jmnedict': 15000, 'jawiki': 16000, 'jmed': 17000}
# Unevidenced katakana transliterations of the key go after every other non-ASCII candidate, before ASCII.
OTHER_COST, TRANSLITERATION_COST, ASCII_COST = 18000, 19000, 20000


def pri_cost(pri):
    tags = pri.split('|')
    nf = [int(t[2:]) for t in tags if t.startswith('nf') and t[2:].isdigit()]
    if nf:
        return NF_COST[min(max(min(nf) - 1, 0) // 6, len(NF_COST) - 1)]
    return PRI_COST['1' if any(t.endswith('1') for t in tags) else '2']


def mozc_cost(cost):
    """Mozc word cost on the sudachi-small scale: piecewise-linear over MOZC_KNOTS."""
    for (x0, y0), (x1, y1) in zip(MOZC_KNOTS, MOZC_KNOTS[1:]):
        if cost <= x1:
            return round(y0 + (y1 - y0) * (max(cost, x0) - x0) / (x1 - x0))
    return MOZC_KNOTS[-1][1]


def signal_cost(surface, rows):
    """Pseudo-cost: Mozc cost when present, else sudachi-small cost or JMdict priority; None without such a signal."""
    mozc = [mozc_cost(cost) for source, _, cost, _ in rows if source == 'mozc' and plausible_mozc(surface, cost)]
    if mozc:
        return min(mozc)
    signals = [pri_cost(pri) for source, _, _, pri in rows if source == 'jmdict' and pri]
    # Cost 0 marks irregular variants (ゐざる, たゝく), not frequent words; untagged katakana is shifted.
    shift = KATAKANA_SHIFT if not signals and KATAKANA.fullmatch(surface) else 0
    signals += [cost + shift for source, _, cost, _ in rows if source == 'sudachi-small' and cost]
    # Selected Mozc lacks the word (rows ('mozc', 0, None, None)): rarer than its other signals suggest.
    absent = MOZC_ABSENT_SHIFT if any(source == 'mozc' for source, _, _, _ in rows) else 0
    return min(signals) + absent if signals else None


def plausible_mozc(surface, cost):
    return cost is not None and (cost >= MOZC_KANA_FLOOR or not KANA.fullmatch(surface))


def candidate_order(key, surface, rows, skk_sources=()):
    """Sort key of one okuri-nasi candidate; rows: (source, rank, cost, pri) of every source providing it."""
    ranks = [rank for source, rank, _, _ in rows if source == 'skk-l']
    if ranks:
        return (0, min(ranks))
    cost, tie = signal_cost(surface, rows), ('', 0)
    if cost is None:
        cost, source, rank = min((DEFAULT_COST.get(source, OTHER_COST), source, rank) for source, rank, _, _ in rows if source != 'mozc')
        tie = (source, rank if source in skk_sources else 0)
    if surface.isascii() and not key.isascii():
        cost = max(cost, ASCII_COST)
    elif transliteration(key, surface, rows):
        cost = max(cost, TRANSLITERATION_COST)
    # The kana spelling identical to the key goes last: SKK confirms it without conversion.
    # Ties: more source families (skk, sudachi, jmdict, ...) first, then SKK source order, then code points.
    families = len({source.split('-')[0] for source, _, _, _ in rows if source != 'mozc'})
    return (1 + (surface == key and not key.isascii()), cost, -families, *tie, surface)


def transliteration(key, surface, rows):
    """Katakana spelling of the key (トクズ for とくず) without JMdict or plausible Mozc evidence of being a word."""
    return bool(KATAKANA.fullmatch(surface)) and search_key(surface) == key and not any(
        source == 'jmdict' or source == 'mozc' and plausible_mozc(surface, cost) for source, _, cost, _ in rows)


def candidate_rows(rows, mozc=None):
    """{surface: [(source, rank, cost, pri)]} from rows (surface, source, rank, cost, pri[, original_reading]);
    mozc: {surface: cost} of the key, attached as ('mozc', 0, cost, None) to existing candidates only."""
    rows = list(rows)
    by_surface = {row[0]: [] for row in rows}
    for surface, source, rank, cost, pri, *original in rows:
        # A JMdict katakana reading marks a loanword: its priority belongs to that spelling, not to ateji like 珈琲.
        if (source == 'jmdict' and pri and original and original[0] != surface and original[0] in by_surface
                and KATAKANA.fullmatch(original[0])):
            by_surface[original[0]].append((source, rank, None, pri))
            pri = None
        by_surface[surface].append((source, rank, cost, pri))
    if mozc is not None:
        for surface, values in by_surface.items():
            values.append(('mozc', 0, mozc.get(surface), None))
    return by_surface


def nasi_candidates(key, rows, skk_sources=(), mozc=None):
    """Candidates of one okuri-nasi key in output order; rows and mozc as for candidate_rows."""
    by_surface = candidate_rows(rows, mozc)
    return sorted(by_surface, key=lambda s: candidate_order(key, s, by_surface[s], skk_sources))


def ranking_costs(con, sources, selected):
    """Stream (reading, {surface: lowest cost}) of the selected ranking-only sources, in reading order."""
    ids = [s['id'] for s in sources if s['id'] in selected and s.get('format') in RANKING_FORMATS]
    if not ids:
        return iter(())
    query = ('SELECT reading,surface,min(cost) FROM costs WHERE source IN (' + ','.join('?' * len(ids))
             + ') GROUP BY reading,surface ORDER BY reading,surface')
    return ((key, {surface: cost for _, surface, cost in rows})
            for key, rows in itertools.groupby(con.execute(query, ids), key=lambda row: row[0]))


def join_costs(groups, costs):
    """Merge (key, rows) groups with (key, {surface: cost}); both ascend by key (SQLite BINARY = code point order)."""
    costs = iter(costs)
    current = next(costs, None)
    for key, rows in groups:
        while current and current[0] < key:
            current = next(costs, None)
        yield key, rows, current[1] if current and current[0] == key else {}


def export_skk_tsv(ids, output):
    """SKK input: kind TAB key TAB surface, already in final SKK line and candidate order."""
    con, sources, selected, where = selection(ids)
    # okuri-ari: keys descending; candidates by source (skk-l, then catalog order), then rank in source.
    order = {id: (id != 'skk-l', i) for i, (id,) in enumerate(con.execute('SELECT id FROM sources ORDER BY rowid'))}
    ari = {}
    for key, surface, source, rank in con.execute('SELECT reading,surface,source,rank FROM entries' + where
                                                  + " AND kind='ari' AND scope IN ('all','skk')", selected):
        best = ari.setdefault(key, {})
        best[surface] = min(best.get(surface, (order[source], rank)), (order[source], rank))
    # okuri-nasi: keys ascending, streamed in primary-key order one key at a time.
    skk_sources = {s['id'] for s in sources if s.get('format') == 'skk'}
    query = ('SELECT reading,surface,source,rank,cost,pri,original_reading FROM entries' + where
             + " AND kind='nasi' AND scope IN ('all','skk') ORDER BY reading,surface,source")
    groups = itertools.groupby(con.execute(query, selected), key=lambda row: row[0])
    # A second cursor streams Mozc costs alongside; candidates still come only from entries.
    nasi = (('nasi', key, surface) for key, rows, mozc in join_costs(groups, ranking_costs(con, sources, selected))
            for surface in nasi_candidates(key, (row[1:] for row in rows), skk_sources, mozc))
    records = itertools.chain((('ari', key, surface) for key in sorted(ari, reverse=True)
                               for surface in sorted(ari[key], key=ari[key].get)), nasi)
    try:
        return write_export(output, records, sources, selected,
                            'kind (ari/nasi) TAB key TAB candidate in SKK output order; okuri-ari keys descending with candidates by source then source order; '
                            'okuri-nasi keys ascending with skk-l candidates first in SKK-JISYO.L order, then by estimated cost (Mozc OSS cost when selected and present, '
                            'else sudachi-small cost/JMdict priority, source defaults); Mozc ranks only and adds no candidates; normalization as the RustMigemo TSV')
    finally:
        con.close()


def export_skk(tsv, output, release_headers=()):
    """UTF-8 SKK dictionary from export_skk_tsv order; plain candidates only."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + '.tmp')
    stats = Counter()
    previous = None
    candidates = []
    def valid_key(kind, key):
        return (key and not any(c.isspace() or c in '/;#[]' for c in key) and not key.startswith(';')
                and bool(re.search('[ぁ-ゖー][a-z]$', key)) == (kind == 'ari'))
    def valid_candidate(value):
        return (value and not any(c in value for c in '/;#[]')
                and not value.startswith('('))
    try:
        with open(tsv, encoding='utf-8') as source, tmp.open('w', encoding='utf-8', newline='\n') as dest:
            dest.write(';; -*- mode: fundamental; coding: utf-8 -*-\n'
                       ';; myhugejisyo: generated supplementary dictionary; see accompanying notices.\n'
                       + ''.join(';; ' + line + '\n' for line in release_headers)
                       + ';; okuri-ari entries.\n')
            def flush():
                if candidates:
                    dest.write(previous[1] + ' /' + '/'.join(candidates) + '/\n')
                    stats[previous[0] + '_readings'] += 1
            for line in source:
                kind, key, value = line.rstrip('\n').split('\t')
                stats['input_pairs'] += 1
                if (kind, key) != previous:
                    # okuri-ari keys descend, then okuri-nasi keys ascend (standard SKK layout).
                    if kind not in ('ari', 'nasi') or previous and (
                            (previous[0], kind) == ('nasi', 'ari') or kind == previous[0] and (key > previous[1]) == (kind == 'ari')):
                        raise ValueError('SKK input TSV must be in SKK output order')
                    flush()
                    if kind == 'nasi' and (not previous or previous[0] == 'ari'):
                        dest.write(';; okuri-nasi entries.\n')
                    candidates = []
                    previous = kind, key
                if not valid_key(kind, key):
                    stats['unsupported_key_pairs'] += 1
                elif not valid_candidate(value):
                    stats['unsafe_candidate_pairs'] += 1
                elif value in candidates:
                    stats['duplicate_pairs'] += 1
                else:
                    candidates.append(value)
                    stats[kind + '_pairs'] += 1
            flush()
            if not previous or previous[0] == 'ari':
                dest.write(';; okuri-nasi entries.\n')
        stats['pairs'] = stats['ari_pairs'] + stats['nasi_pairs']
        stats['readings'] = stats['ari_readings'] + stats['nasi_readings']
        if not stats['pairs']:
            raise ValueError('SKK export contains no candidates')
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(output)
    manifest = json.loads(Path(str(tsv) + '.manifest.json').read_text())
    manifest.update(format='SKK UTF-8 supplement with okuri-ari and okuri-nasi sections', skk=dict(stats),
                    sha256=digest(output), candidate_order='okuri-ari: skk-l first, then other sources in catalog order, each in source order; okuri-nasi: as in the input TSV '
                                    '(jisyo.py: skk-l first in SKK-JISYO.L order, then ascending estimated cost on the sudachi-small scale: '
                                    'Mozc OSS word cost (ranking-only source, quantile-mapped) when selected and present, otherwise sudachi-small cost '
                                    'or JMdict priority tags (+ a shift when missing from a selected Mozc), then per-source defaults; katakana spellings '
                                    'of the key without JMdict/Mozc evidence after other words, ASCII after those, kana-identical candidates last); '
                                    'not measured usage frequency; use after personal/base SKK dictionaries',
                    exclusions='unrepresentable plain SKK keys/candidates omitted; no Lisp expressions emitted')
    write_json(str(output) + '.manifest.json', manifest)
    print(f"exported SKK: {dict(stats)}", flush=True)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch', help='Download official sources, record versions and SHA-256')
    fetch.add_argument('--update', action='store_true', help='Resolve current upstream releases and replace lock')
    sub.add_parser('ingest', help='Rebuild source-aware SQLite master')
    exp = sub.add_parser('export', help='Export normalized UTF-8 reading TAB surface pairs')
    exp.add_argument('--sources', nargs='+', help='Source IDs, omitted = all')
    exp.add_argument('--output', default=str(ROOT / 'dist/all.tsv'))
    build = sub.add_parser('build', help='Export and build RustMigemo compact dictionary')
    build.add_argument('--sources', nargs='+')
    build.add_argument('--output', default=str(ROOT / 'dist/migemo-compact-dict'))
    skk = sub.add_parser('skk', help='Export the SKK input TSV and UTF-8 SKK supplementary dictionary')
    skk.add_argument('--sources', nargs='+')
    skk.add_argument('--output', default=str(ROOT / 'dist/SKK-JISYO.myhugejisyo.utf8'))
    sub.add_parser('list', help='List locked sources')
    args = p.parse_args()
    if args.command == 'fetch':
        sources = catalog() if args.update or not LOCK.exists() else json.loads(LOCK.read_text())['sources']
        with ThreadPoolExecutor(max_workers=4) as executor:
            sources = list(executor.map(lambda source: download(source, refresh=args.update), sources))
        write_json(LOCK, dict(schema_version=1, sources=sources))
        return
    sources = json.loads(LOCK.read_text())['sources']
    if args.command == 'ingest':
        ingest(sources)
    elif args.command == 'list':
        for s in sources:
            print(s['id'], s['version'], s['license'], sep='\t')
    elif args.command == 'export':
        export(args.sources, args.output)
    elif args.command == 'skk':
        tsv = args.output + '.skk.tsv'
        export_skk_tsv(args.sources, tsv)
        export_skk(tsv, args.output)
    elif args.command == 'build':
        tsv = args.output + '.tsv'
        export(args.sources, tsv)
        subprocess.run(['cargo', 'run', '--release', '--locked', '--', 'build', tsv, args.output], cwd=ROOT, check=True)
        manifest = json.loads(Path(tsv + '.manifest.json').read_text())
        manifest.update(binary_sha256=digest(args.output), binary_bytes=Path(args.output).stat().st_size,
                        builder_revision='121101dfcfd9465c998ba53fabe43773b14bf868', verification='all exported pairs round-tripped through CompactDictionary.search')
        write_json(args.output + '.manifest.json', manifest)

if __name__ == '__main__':
    main()
