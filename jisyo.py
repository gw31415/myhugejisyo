#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Collect versioned public lexicons and export RustMigemo dictionaries (stdlib only)."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import html
import io
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
READING_SEPARATORS = str.maketrans('', '', '・、。「」【】')
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


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
        if okuri:
            stats['skk_okuri_entry'] += 1
            continue
        key, sep, rest = line.rstrip('\n').partition(' ')
        if not sep or not rest.startswith('/') or not rest.endswith('/'):
            stats['skk_malformed_entry'] += 1
            continue
        # SKK numbers and okuri keys are templates, not literal readings.
        if '#' in key or re.search('[ぁ-ゖー][a-z]$', key):
            stats['skk_template_entry'] += 1
            continue
        if '/(' in rest or '/[' in rest:
            stats['skk_expression_entry'] += 1
            continue
        for candidate in rest[1:-1].split('/'):
            surface = candidate.split(';', 1)[0]
            if not surface or surface.startswith(('(', '[', ']')) or '#' in surface:
                stats['skk_special_candidate'] += 1
                continue
            yield key, surface, ''


def parse_jmdict(stream, stats):
    events = ET.iterparse(stream, events=('start', 'end'))
    _, root = next(events)
    for event, entry in events:
        if event != 'end' or entry.tag != 'entry':
            continue
        spellings = entry.findall('k_ele/keb')
        forms = [s.text for s in spellings]
        pos = '|'.join(sorted({p.text for p in entry.findall('sense/pos') + entry.findall('trans/name_type') if p.text}))
        for reading in entry.findall('r_ele'):
            key = reading.findtext('reb', '')
            restricted = [r.text for r in reading.findall('re_restr')]
            if reading.find('re_nokanji') is not None or not forms:
                values = [key]
            else:
                values = [s for s in forms if not restricted or s in restricted]
            for surface in values:
                yield key, surface, pos
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
                    yield unescape_sudachi(row[11]), unescape_sudachi(row[4]), '|'.join(row[5:11])
    elif fmt == 'jmdict':
        with gzip.open(path) as f:
            yield from parse_jmdict(f, stats)
    elif fmt == 'reading-tsv':
        with path.open(encoding='utf-8', newline='') as f:
            for row in csv.reader(f, delimiter='\t'):
                if len(row) != 2:
                    raise ValueError('Expected reading TAB surface')
                yield row[0], row[1], 'materialized vocabulary'
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
                    yield row.get(rc, ''), row.get(sc, ''), Path(name).stem
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
      CREATE TABLE entries(reading TEXT, surface TEXT, source TEXT, pos TEXT, original_reading TEXT,
        confidence TEXT NOT NULL DEFAULT 'upstream-provided',
        PRIMARY KEY(reading,surface,source)) WITHOUT ROWID;
    ''')
    report = {}
    try:
        for source in sources:
            stats = Counter()
            con.execute('INSERT INTO sources VALUES (?,?)', (source['id'], json.dumps(source, ensure_ascii=False)))
            batch = []
            before = con.total_changes
            for reading, surface, pos in rows(source, stats):
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
                batch.append((reading, surface, source['id'], pos, original_reading))
                if len(batch) >= 10000:
                    con.executemany('INSERT OR IGNORE INTO entries(reading,surface,source,pos,original_reading) VALUES (?,?,?,?,?)', batch)
                    batch.clear()
            con.executemany('INSERT OR IGNORE INTO entries(reading,surface,source,pos,original_reading) VALUES (?,?,?,?,?)', batch)
            stats['unique_source_pairs'] = con.total_changes - before
            if not stats['unique_source_pairs']:
                raise ValueError(f"{source['id']} produced no vocabulary")
            report[source['id']] = dict(stats)
            con.commit()
            print(f"imported {source['id']}: {dict(stats)}", flush=True)
        report['totals'] = dict(readings=con.execute('SELECT COUNT(DISTINCT reading) FROM entries').fetchone()[0],
                                pairs=con.execute('SELECT COUNT(*) FROM (SELECT reading,surface FROM entries GROUP BY reading,surface)').fetchone()[0])
        con.close()
        tmp.replace(DB)
        write_json(ROOT / 'data/import-report.json', report)
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise


def export(ids, output):
    if not DB.exists():
        raise ValueError('Run ingest first')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    sources = [json.loads(row[0]) for row in con.execute('SELECT metadata FROM sources ORDER BY id')]
    selected = set(ids) if ids else {s['id'] for s in sources}
    unknown = selected - {s['id'] for s in sources}
    if unknown:
        raise ValueError(f'Unknown source IDs: {sorted(unknown)}')
    where = ' WHERE source IN (' + ','.join('?' for _ in selected) + ')'
    query = 'SELECT reading,surface FROM entries' + where + ' GROUP BY reading,surface ORDER BY reading,surface'
    tmp = output.with_suffix(output.suffix + '.tmp')
    count = 0
    with tmp.open('w', encoding='utf-8', newline='') as f:
        for reading, surface in con.execute(query, sorted(selected)):
            f.write(reading + '\t' + surface + '\n')
            count += 1
    con.close()
    if not count:
        raise ValueError('No pairs selected')
    tmp.replace(output)
    manifest = dict(pairs=count, sha256=digest(output), sources=[s for s in sources if s['id'] in selected],
                    transformation='NFKC/lowercase/hiragana readings; Japanese separator punctuation removed from search keys (original readings in SQLite); original surfaces; duplicate reading/surface pairs merged; see import-report.json for exclusions')
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


def export_skk(tsv, output, release_headers=()):
    """UTF-8, okuri-nasi supplemental dictionary; plain candidates only."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + '.tmp')
    stats = Counter()
    previous = None
    candidates = []
    def valid_key(key):
        return (key and not any(c.isspace() or c in '/;#[]' for c in key)
                and not key.startswith(';') and not re.search('[ぁ-ゖー][a-z]$', key))
    def valid_candidate(value):
        return (value and not any(c in value for c in '/;#[]')
                and not value.startswith('('))
    with open(tsv, encoding='utf-8') as source, tmp.open('w', encoding='utf-8', newline='\n') as dest:
        dest.write(';; -*- mode: fundamental; coding: utf-8 -*-\n'
                   ';; myhugejisyo: generated supplementary dictionary; see accompanying notices.\n'
                   + ''.join(';; ' + line + '\n' for line in release_headers)
                   + ';; okuri-ari entries.\n;; okuri-nasi entries.\n')
        def flush():
            if candidates:
                dest.write(previous + ' /' + '/'.join(candidates) + '/\n')
                stats['readings'] += 1
        for line in source:
            key, value = line.rstrip('\n').split('\t')
            stats['input_pairs'] += 1
            if previous is not None and key < previous:
                raise ValueError('SKK input TSV must be sorted by reading')
            if key != previous:
                flush()
                candidates = []
                previous = key
            if not valid_key(key):
                stats['unsupported_key_pairs'] += 1
            elif not valid_candidate(value):
                stats['unsafe_candidate_pairs'] += 1
            else:
                candidates.append(value)
                stats['pairs'] += 1
        flush()
    if not stats['pairs']:
        tmp.unlink(missing_ok=True)
        raise ValueError('SKK export contains no candidates')
    tmp.replace(output)
    manifest = json.loads(Path(str(tsv) + '.manifest.json').read_text())
    manifest.update(format='SKK UTF-8 okuri-nasi supplement', skk=dict(stats),
                    sha256=digest(output), candidate_order='lexical; use after personal/base SKK dictionaries',
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
    skk = sub.add_parser('skk', help='Export UTF-8 SKK supplementary dictionary')
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
        tsv = args.output + '.tsv'
        export(args.sources, tsv)
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
