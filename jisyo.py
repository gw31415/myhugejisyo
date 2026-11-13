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
KANA_CHAR = re.compile('[ぁ-ゖァ-ヺーゝゞヽヾ]')
READING_SEPARATORS = str.maketrans('', '', '・、。「」【】')
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
# kind: SKK okuri-nasi/okuri-ari; scope: all, skk-only or migemo-only; cost: Sudachi; pri: JMdict priority tags;
# inf: JMdict ke_inf/re_inf codes; confidence: 'rule-generated' for okuri-ari entries derived by OKURI rules below.
Entry = namedtuple('Entry', 'reading surface pos kind scope cost pri inf confidence',
                   defaults=('nasi', 'all', None, None, None, 'upstream-provided'))
# Formats that only rank candidates, never in entries/exports as vocabulary: Mozc costs per reading/surface (costs table),
# Wikipedia Clickstream clicks per article title (clicks table; no readings).
RANKING_FORMATS = {'mozc', 'clickstream'}
CLICKSTREAM = 'https://dumps.wikimedia.org/other/clickstream/'
GENERATED = 'rule-generated'
# JMdict form tags: search-only forms stay in RustMigemo only; irregular/rare/outdated forms are demoted in SKK order.
SEARCH_ONLY, IRREGULAR = {'sK', 'sk'}, {'iK', 'ik', 'oK', 'ok', 'rK', 'rk'}
# SKK okuri letter(s) of the first okurigana kana, as measured on SKK-JISYO.L (scripts/eval_okuri.py): L files っ under both
# t and c (かt/かc 買った) and じ under both j and z (かんj/かんz 感じる); small kana and ー cannot start okurigana.
OKURI_LETTERS = {kana: letters for row, letters in zip(
    ['あ', 'い', 'う', 'え', 'お', 'かきくけこ', 'がぎぐげご', 'さしすせそ', 'ざずぜぞ', 'じ', 'たちつてと', 'だぢづでど', 'なにぬねの',
     'はひふへほ', 'ばびぶべぼ', 'ぱぴぷぺぽ', 'まみむめも', 'やゆよ', 'らりるれろ', 'わゐゑを', 'ん', 'っ', 'ゔ'],
    'a i u e o k g s z jz t d n h b p m y r w n tc v'.split()) for kana in row}
# Sudachi 活用型 whose inflected rows yield okuri-ari entries (modern verbs and adjectives; classical 文語 types excluded).
SUDACHI_OKURI = re.compile('(五段|上一段|下一段)-.+行|サ行変格|カ行変格|形容詞')
# JMdict class -> (dictionary-form ending, replacements forming 未然/連用/終止/仮定/命令/意志 forms incl. 音便; a bare
# 一段/カ変 stem is followed by the auxiliaries た/て/な/ま/ら/よ). Classes not listed generate nothing.
JMDICT_OKURI = {
    'v5k': ('く', 'か き く け こ い'), 'v5k-s': ('く', 'か き く け こ っ'), 'v5g': ('ぐ', 'が ぎ ぐ げ ご い'),
    'v5s': ('す', 'さ し す せ そ'), 'v5t': ('つ', 'た ち つ て と っ'), 'v5n': ('ぬ', 'な に ぬ ね の ん'),
    'v5b': ('ぶ', 'ば び ぶ べ ぼ ん'), 'v5m': ('む', 'ま み む め も ん'), 'v5r': ('る', 'ら り る れ ろ っ ん'),
    'v5r-i': ('る', 'ら り る れ ろ っ'), 'v5aru': ('る', 'ら り る れ ろ っ い'), 'v5u': ('う', 'わ い う え お っ'),
    'v5u-s': ('う', 'わ い う え お'), 'v1': ('る', 'る れ ろ よ た て な ま ら'), 'v1-s': ('る', 'る れ ろ よ た て な ま ら'),
    'vz': ('ずる', 'ずる ずれ じ ぜ'), 'vs-s': ('する', 'さ し す せ'), 'vs-c': ('す', 'さ し す せ そ'),
    # 来る: the kanji also covers the stem vowel change く/き/こ.
    'vk': ('くる', 'くる くれ こい こよ きた きて こな きま こら'),
    # 高さ/高そう/高すぎ: L files the stem under s as well.
    'adj-i': ('い', 'い く かっ けれ き かろ さ'), 'adj-ix': ('い', 'い く かっ けれ き かろ さ'),
}
# Classes SKK-JISYO.L contradicts: the 95% Wilson upper bound of their precision on L keys is below 0.9
# (scripts/eval_okuri.py --all-classes): Sudachi サ行変格 0.83 (rare one-kanji 漢語 verbs 乖する, 箝する), 下一段-タ行 0.82,
# 上一段-ハ行 1/6; JMdict vs-c 0.71 (classical 会す, 嚇す).
OKURI_EXCLUDED = {'サ行変格', '下一段-タ行', '上一段-ハ行', 'vs-c'}


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


def clickstream_month(index, listing, tries=3):
    """Newest YYYY-MM in the Clickstream index HTML whose month listing (listing(month) -> HTML) has the jawiki file; a new
    month directory is created before every wiki's file is published, so older months are tried next."""
    for month in sorted(set(re.findall(r'href="(\d{4}-\d{2})/"', index)), reverse=True)[:tries]:
        if f'href="clickstream-jawiki-{month}.tsv.gz"' in listing(month):
            return month
    raise ValueError(f'No jawiki Clickstream file in the newest {tries} months of {CLICKSTREAM}')


def remote_text(url):
    with request(url) as r:
        return r.read().decode()


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
    month = clickstream_month(remote_text(CLICKSTREAM), lambda m: remote_text(f'{CLICKSTREAM}{m}/'))
    add('clickstream', 'clickstream', f'{CLICKSTREAM}{month}/clickstream-jawiki-{month}.tsv.gz', month,
        'CC0-1.0 (Wikimedia Analytics datasets); article title click counts used for candidate order only',
        'https://meta.wikimedia.org/wiki/Research:Wikipedia_clickstream', [CLICKSTREAM + 'readme.html', 'https://dumps.wikimedia.org/legal.html'])
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


class Prefixed:
    """Byte stream that replays an already read prefix."""
    def __init__(self, prefix, stream):
        self.prefix, self.stream = prefix, stream

    def read(self, size=-1):
        if self.prefix:
            data, self.prefix = self.prefix, b''
            return data
        return self.stream.read(size)


def parse_jmdict(stream, stats):
    # The DTD maps entity names (v5k, sK) to the texts ElementTree expands them to; keep the codes for classes and tags.
    prefix = b''
    while b']>' not in prefix and (line := stream.readline()):
        prefix += line
    codes = {}
    for name, text in re.findall(r'<!ENTITY\s+(\S+)\s+"([^"]*)">', prefix.decode('utf-8', 'replace')):
        codes.setdefault(html.unescape(text), name)
    events = ET.iterparse(Prefixed(prefix, stream), events=('start', 'end'))
    _, root = next(events)
    for event, entry in events:
        if event != 'end' or entry.tag != 'entry':
            continue
        kanji = {k.findtext('keb'): {p.text for p in k.findall('ke_pri')} for k in entry.findall('k_ele')}
        kanji_inf = {k.findtext('keb'): {codes.get(i.text, i.text) for i in k.findall('ke_inf')} for k in entry.findall('k_ele')}
        forms = list(kanji)
        pos = '|'.join(sorted({p.text for p in entry.findall('sense/pos') + entry.findall('trans/name_type') if p.text}))
        codes_pos = {codes.get(p.text) for p in entry.findall('sense/pos')}
        # Expressions (気が付く, 人当たりがいい) are phrases, not words SKK files under okuri-ari keys.
        classes = [] if 'exp' in codes_pos else sorted(codes_pos & JMDICT_OKURI.keys())
        for reading in entry.findall('r_ele'):
            key = reading.findtext('reb', '')
            restricted = [r.text for r in reading.findall('re_restr')]
            if reading.find('re_nokanji') is not None or not forms:
                values = [key]
            else:
                values = [s for s in forms if not restricted or s in restricted]
            tags = {p.text for p in reading.findall('re_pri')}
            reading_inf = {codes.get(i.text, i.text) for i in reading.findall('re_inf')}
            for surface in values:
                # A kanji form gets the tags its ke_pri shares with this reading's re_pri: 客 is news1 as きゃく, not as かく.
                # A katakana reading's re_pri stays; candidate_rows moves it to that loanword spelling.
                own = kanji.get(surface, set())
                pri = '|'.join(sorted(own | tags if surface not in kanji or KATAKANA.fullmatch(key) else own & tags)) or None
                inf = reading_inf | kanji_inf.get(surface, set())
                # Search-only forms (sK kanji, sk kana) help RustMigemo but are not SKK candidates.
                search_only = bool(inf & SEARCH_ONLY)
                stats['jmdict_search_only_pair'] += search_only
                yield Entry(key, surface, pos, scope='migemo' if search_only else 'all', pri=pri, inf='|'.join(sorted(inf)) or None)
                # Okuri-ari entries only from the headword spelling or a spelling with its own priority tags, never from
                # forms tagged irregular/rare/outdated: other variants are mostly spellings SKK-JISYO.L does not use.
                if search_only or inf & IRREGULAR or not forms or surface != forms[0] and not own:
                    continue
                for code in classes:
                    for okuri, stem in jmdict_okuri(surface, key, code):
                        stats['okuri_generated'] += 1
                        yield Entry(okuri, stem, f'okuri-rule|{code}|{key}|{surface}', 'ari', 'skk', pri=pri,
                                    inf='|'.join(sorted(inf)) or None, confidence=GENERATED)
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


def hiragana(text):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in text)


def okuri_split(surface, reading):
    """(stem reading, stem, okurigana) of an inflected form: the stem ends with its last non-kana character (normally
    kanji) and the trailing kana must end the reading; None without okurigana, for all-kana words or misaligned kana."""
    i = len(surface)
    while i and KANA_CHAR.fullmatch(surface[i - 1]):
        i -= 1
    stem, okurigana, reading = surface[:i], hiragana(surface[i:]), hiragana(reading)
    if (not stem or not okurigana or unicodedata.category(stem[-1]) not in ('Lo', 'Lm') or KANA_CHAR.fullmatch(stem[0])
            or not reading.endswith(okurigana) or not re.fullmatch('[ぁ-ゖー]+', reading[:-len(okurigana)])):
        return None
    stem_reading = reading[:-len(okurigana)]
    # Kana inside the stem (取り扱) must appear in its reading: kanji runs match one or more kana.
    parts = re.split('([ぁ-ゖァ-ヺーゝゞヽヾ]+)', stem)
    if len(parts) > 1 and not re.fullmatch(''.join(re.escape(hiragana(p)) if i % 2 else '.+' for i, p in enumerate(parts) if p),
                                           stem_reading):
        return None
    return stem_reading, stem, okurigana


def okuri_letters(okurigana):
    """SKK okuri letters: those of the first kana; a sokuon takes the next kana's (酸っぱ -> p), a final one (買っ+た) t and c."""
    if okurigana[0] == 'っ' and len(okurigana) > 1:
        return OKURI_LETTERS.get(okurigana[1], '')
    return OKURI_LETTERS.get(okurigana[0], '')


def okuri_keys(surface, reading):
    """SKK okuri-ari (key, candidate) pairs of one inflected form: 書か/かか -> [('かk', '書')], 買っ/かっ -> かt, かc."""
    split = okuri_split(surface, reading)
    return [(split[0] + letter, split[1]) for letter in okuri_letters(split[2])] if split else []


def jmdict_okuri(surface, reading, code):
    """Okuri-ari pairs of a JMdict dictionary form, inflected by its JMDICT_OKURI class table."""
    if code not in JMDICT_OKURI or code in OKURI_EXCLUDED:
        return []
    ending, replacements = JMDICT_OKURI[code]
    if code == 'adj-ix' and reading.endswith('いい'):
        replacements = 'い'
    elif code == 'v5k-s' and not reading.endswith('いく'):
        replacements = replacements.replace('っ', '')  # 行った is いった, never ゆった
    if not reading.endswith(ending):
        return []
    # Trailing kana of the ending written in the surface; the rest (く of 来る) is covered by the kanji.
    written = next(n for n in range(len(ending), -1, -1) if surface.endswith(ending[len(ending) - n:]))
    covered = len(ending) - written
    pairs = []
    for replacement in replacements.split():
        for pair in okuri_keys(surface[:len(surface) - written] + replacement[covered:], reading[:-len(ending)] + replacement):
            if pair not in pairs:
                pairs.append(pair)
    return pairs


def sudachi_okuri(lemmas, stats):
    """Okuri-ari entries from Sudachi verb/adjective rows grouped by lemma (column 13, dictionary form id):
    {id: [(活用型, 活用形, normalized form, surface, reading, cost, (stem reading, stem, okurigana))]}. A lemma counts only with
    its canonical dictionary form (終止形-一般 row spelled as the normalized form, column 12), and every form must keep that
    form's stem and stem reading (カ変 excepted for the reading), so okurigana-omitted or variant spellings (乏い, 取越す,
    観る, 止どめる) and colloquial stems (大っきい) are skipped."""
    best = {}
    for lemma, rows in sorted(lemmas.items()):
        bases = sorted((cost, split, search_key(reading), surface, conjugation) for conjugation, form, normalized, surface, reading, cost, split in rows
                       if form == '終止形-一般' and surface == normalized)
        if not bases:
            stats['okuri_skipped_lemma'] += 1
            continue
        cost, (base_reading, stem, _), reading, surface, conjugation = bases[0]
        for _, form, _, _, _, _, split in rows:
            if split[1] != stem or split[0] != base_reading and conjugation != 'カ行変格':
                stats['okuri_skipped_form'] += 1
                continue
            # Classical/polite adjective ウ音便 (高う, 早う) is not filed under u in SKK-JISYO.L.
            if form == '連用形-ウ音便' and conjugation == '形容詞':
                continue
            for letter in okuri_letters(split[2]):
                value = (cost, reading, surface, conjugation)
                key = (split[0] + letter, stem)
                best[key] = min(best.get(key, value), value)
    for (key, stem), (cost, reading, surface, conjugation) in sorted(best.items()):
        stats['okuri_generated'] += 1
        yield Entry(key, stem, f'okuri-rule|{conjugation}|{reading}|{surface}', 'ari', 'skk', cost, confidence=GENERATED)


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
            lemmas = {}
            with archive.open(members[0]) as raw:
                for row in csv.reader(io.TextIOWrapper(raw, encoding='utf-8')):
                    if len(row) < 18:
                        raise ValueError('Unexpected Sudachi CSV layout')
                    # Column 4 is original output surface; 0 is normalized trie key.
                    # Inflected forms (column 10) help Migemo but are not SKK okuri-nasi words.
                    inflected = row[10] != '*' and not row[10].startswith('終止形')
                    reading, surface = unescape_sudachi(row[11]), unescape_sudachi(row[4])
                    yield Entry(reading, surface, '|'.join(row[5:11]), scope='migemo' if inflected else 'all', cost=int(row[3]))
                    # Each inflected verb/adjective row (column 9: 活用型) is also split into an okuri-ari key. Rows with
                    # connection id -1 (columns 1-2, always cost 0) are not lattice words, only split-target variants
                    # (散る アカル, 出る イデル, 剥ぐ パグ, 会う アウ ウ音便): they never yield okuri-ari keys.
                    split = (row[1] != '-1' and row[5] in ('動詞', '形容詞') and row[13] != '*' and SUDACHI_OKURI.fullmatch(row[9])
                             and row[9] not in OKURI_EXCLUDED and okuri_split(surface, reading))
                    if split:
                        lemmas.setdefault(row[13], []).append((row[9], row[10], unescape_sudachi(row[12]), surface, reading, int(row[3]), split))
            yield from sudachi_okuri(lemmas, stats)
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
    elif fmt == 'clickstream':
        # prev, curr, type (link/external/other), n: monthly jawiki (referrer, article) pairs with n >= 10. Every row counts
        # toward its article (curr) title, spaces written as _; readings do not exist.
        with gzip.open(path, 'rt', encoding='utf-8', newline='') as f:
            for line in f:
                row = line.rstrip('\n').split('\t')
                if len(row) != 4 or row[2] not in ('link', 'external', 'other') or not row[3].isdigit():
                    raise ValueError('Unexpected Clickstream layout')
                yield Entry('', row[1].replace('_', ' '), '', cost=int(row[3]))
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
    con.create_function('common_tags', 2, lambda a, b: a and b and '|'.join(sorted(set(a.split('|')) & set(b.split('|')))) or None,
                        deterministic=True)
    con.executescript('''
      PRAGMA journal_mode=OFF;
      PRAGMA cache_size=-131072;
      CREATE TABLE sources(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
      CREATE TABLE entries(reading TEXT, surface TEXT, source TEXT, kind TEXT NOT NULL CHECK(kind IN ('nasi','ari')),
        scope TEXT NOT NULL CHECK(scope IN ('all','skk','migemo')), rank INTEGER NOT NULL, cost INTEGER, pri TEXT,
        pos TEXT, original_reading TEXT, confidence TEXT NOT NULL DEFAULT 'upstream-provided', inf TEXT,
        PRIMARY KEY(reading,surface,source,kind)) WITHOUT ROWID;
      CREATE TABLE costs(reading TEXT, surface TEXT, source TEXT, cost INTEGER NOT NULL,
        PRIMARY KEY(reading,surface,source)) WITHOUT ROWID;
      CREATE TABLE clicks(surface TEXT, source TEXT, clicks INTEGER NOT NULL, PRIMARY KEY(surface,source)) WITHOUT ROWID;
    ''')
    # Repeated keys within a source keep the first rank/pos/reading, the lowest cost and the widest scope; JMdict form tags
    # (inf) keep only the tags every occurrence has, so a pair is demoted only if all its JMdict rows are irregular.
    insert = '''INSERT INTO entries(reading,surface,source,kind,scope,rank,cost,pri,pos,original_reading,inf,confidence)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(reading,surface,source,kind) DO UPDATE SET rank=min(rank,excluded.rank),
        scope=CASE WHEN scope=excluded.scope THEN scope ELSE 'all' END,
        cost=coalesce(min(cost,excluded.cost),cost,excluded.cost), pri=coalesce(pri,excluded.pri),
        inf=common_tags(inf,excluded.inf)'''
    # Ranking-only sources (Mozc): lowest cost per normalized reading/surface, e.g. over Mozc's POS ids.
    insert_cost = '''INSERT INTO costs VALUES (?,?,?,?) ON CONFLICT(reading,surface,source) DO UPDATE SET cost=min(cost,excluded.cost)'''
    # Clickstream: clicks summed per article title.
    insert_clicks = '''INSERT INTO clicks VALUES (?,?,?) ON CONFLICT(surface,source) DO UPDATE SET clicks=clicks+excluded.clicks'''
    report = {}
    try:
        for source in sources:
            stats = Counter()
            con.execute('INSERT INTO sources VALUES (?,?)', (source['id'], json.dumps(source, ensure_ascii=False)))
            ranking, titles = source.get('format') in RANKING_FORMATS, source.get('format') == 'clickstream'
            table, statement = ('clicks', insert_clicks) if titles else ('costs', insert_cost) if ranking else ('entries', insert)
            batch = []
            for rank, (reading, surface, pos, kind, scope, cost, pri, inf, confidence) in enumerate(rows(source, stats)):
                stats['parsed_pairs'] += 1
                if not titles:
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
                batch.append((surface, source['id'], cost) if titles else (reading, surface, source['id'], cost) if ranking else
                             (reading, surface, source['id'], kind, scope, rank, cost, pri, pos, original_reading, inf, confidence))
                if len(batch) >= 10000:
                    con.executemany(statement, batch)
                    batch.clear()
            con.executemany(statement, batch)
            stats['unique_source_pairs'] = con.execute(f'SELECT COUNT(*) FROM {table} WHERE source=?', (source['id'],)).fetchone()[0]
            generated = con.execute('SELECT COUNT(*) FROM entries WHERE source=? AND confidence=?', (source['id'], GENERATED)).fetchone()[0]
            if generated:
                stats['okuri_generated_unique_pairs'] = generated
            if not stats['unique_source_pairs']:
                raise ValueError(f"{source['id']} produced no vocabulary")
            report[source['id']] = dict(stats)
            con.commit()
            print(f"imported {source['id']}: {dict(stats)}", flush=True)
        report['totals'] = dict(readings=con.execute("SELECT COUNT(DISTINCT reading) FROM entries WHERE kind='nasi'").fetchone()[0],
                                pairs=con.execute("SELECT COUNT(*) FROM (SELECT reading,surface FROM entries WHERE kind='nasi' GROUP BY reading,surface)").fetchone()[0],
                                okuri_ari_pairs=con.execute("SELECT COUNT(*) FROM (SELECT reading,surface FROM entries WHERE kind='ari' GROUP BY reading,surface)").fetchone()[0],
                                okuri_ari_generated_only_pairs=con.execute("SELECT COUNT(*) FROM (SELECT reading,surface FROM entries WHERE kind='ari' "
                                                                           f"GROUP BY reading,surface HAVING max(confidence)='{GENERATED}')").fetchone()[0],
                                ranking_pairs=con.execute('SELECT COUNT(*) FROM (SELECT reading,surface FROM costs GROUP BY reading,surface)').fetchone()[0],
                                ranking_titles=con.execute('SELECT COUNT(DISTINCT surface) FROM clicks').fetchone()[0])
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


def write_export(output, records, sources, selected, transformation, extra=None):
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
                    transformation=transformation, **(extra or {}))
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
# Forms JMdict tags only as irregular/rare/outdated (IRREGULAR) go after every source default (incl. jmed), before OTHER_COST.
IRREGULAR_COST = 17500
# Monthly Wikipedia Clickstream clicks (all referrer types summed) of the article titled exactly like a candidate -> sudachi-small
# scale: clicks quantiles 1,2,5,10,15,..,95,98,99% matched with the 99,98,95,..,1% quantiles of mozc_cost over the 133,226
# normalized pairs having both (plausible Mozc cost; kana-only and ASCII surfaces excluded), first knot of each run of equal
# click values kept, linear in between, clamped outside. Derived once from Clickstream jawiki 2026-10 and Mozc a4541df.
CLICK_KNOTS = ((10, 11258), (11, 11149), (15, 10903), (24, 10680), (36, 10549), (52, 10421), (71, 10178), (94, 9882), (122, 9796),
               (156, 9772), (197, 9578), (245, 9384), (308, 9236), (387, 9046), (490, 8459), (622, 8101), (806, 7832), (1067, 7624),
               (1483, 7404), (2239, 7332), (4100, 6559), (8155, 6337), (13357, 5895))
# Rows of ranking-only sources attached to candidates by candidate_rows (no vocabulary of their own).
RANKING_ROWS = {'mozc', 'clickstream'}


def pri_cost(pri):
    tags = pri.split('|')
    nf = [int(t[2:]) for t in tags if t.startswith('nf') and t[2:].isdigit()]
    if nf:
        return NF_COST[min(max(min(nf) - 1, 0) // 6, len(NF_COST) - 1)]
    return PRI_COST['1' if any(t.endswith('1') for t in tags) else '2']


def interpolate(knots, x):
    """Piecewise-linear over (x, y) knots with ascending x, clamped outside."""
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if x <= x1:
            return round(y0 + (y1 - y0) * (max(x, x0) - x0) / (x1 - x0))
    return knots[-1][1]


def mozc_cost(cost):
    """Mozc word cost on the sudachi-small scale."""
    return interpolate(MOZC_KNOTS, cost)


def click_cost(clicks):
    """Monthly Clickstream clicks of a title on the sudachi-small scale (more clicks, lower cost)."""
    return interpolate(CLICK_KNOTS, clicks)


def signal_cost(surface, rows):
    """Pseudo-cost: Mozc cost when present, else sudachi-small cost or JMdict priority; None without such a signal."""
    mozc = [mozc_cost(cost) for source, _, cost, *_ in rows if source == 'mozc' and plausible_mozc(surface, cost)]
    if mozc:
        return min(mozc)
    signals = [pri_cost(pri) for source, _, _, pri, *_ in rows if source == 'jmdict' and pri]
    # Cost 0 marks irregular variants (ゐざる, たゝく), not frequent words; untagged katakana is shifted.
    shift = KATAKANA_SHIFT if not signals and KATAKANA.fullmatch(surface) else 0
    signals += [cost + shift for source, _, cost, *_ in rows if source == 'sudachi-small' and cost]
    # Selected Mozc lacks the word (rows ('mozc', 0, None, None)): rarer than its other signals suggest.
    absent = MOZC_ABSENT_SHIFT if any(row[0] == 'mozc' for row in rows) else 0
    return min(signals) + absent if signals else None


def plausible_mozc(surface, cost):
    return cost is not None and (cost >= MOZC_KANA_FLOOR or not KANA.fullmatch(surface))


def irregular(rows):
    """Every JMdict row of the candidate is tagged irregular/rare/outdated (iK, oK, rK, ik, ok, rk)."""
    flags = [row[4] for row in rows if row[0] == 'jmdict' and len(row) > 4]
    return bool(flags) and all(flags)


def candidate_order(key, surface, rows, skk_sources=()):
    """Sort key of one candidate; rows: (source, rank, cost, pri[, irregular]) of every source providing it."""
    ranks = [rank for source, rank, *_ in rows if source == 'skk-l']
    if ranks:
        return (0, min(ranks))
    cost, tie = signal_cost(surface, rows), (0, '', 0)
    if cost is None:
        cost, source, rank = min((DEFAULT_COST.get(source, OTHER_COST), source, rank) for source, rank, *_ in rows if source not in RANKING_ROWS)
        # A Wikipedia article title without other signal: its clicks may move it before its source default, never after. Like
        # the other signals it is shifted when a selected Mozc lacks the word; hold-out vs SKK-JISYO.L (scripts/eval_order.py):
        # shift 0 0.6817 adjacent, 1500 0.6823, 3000 0.6825, 4500 0.6826, without Clickstream 0.6827; ordering of candidates
        # without other signal against held-out Mozc order: defaults only 0.5366, shift 0 0.5573, 3000 0.5531, 4500 0.5512.
        clicks = next((n for source, _, n, *_ in rows if source == 'clickstream'), 0)
        if clicks:
            cost = min(cost, click_cost(clicks) + (MOZC_ABSENT_SHIFT if any(row[0] == 'mozc' for row in rows) else 0))
        tie = (-clicks, source, rank if source in skk_sources else 0)
    if irregular(rows):
        cost = max(cost, IRREGULAR_COST)
    if surface.isascii() and not key.isascii():
        cost = max(cost, ASCII_COST)
    elif transliteration(key, surface, rows):
        cost = max(cost, TRANSLITERATION_COST)
    # The kana spelling identical to the key goes last: SKK confirms it without conversion.
    # Ties: more source families (skk, sudachi, jmdict, ...) first, then more clicks (no other signal), SKK source order, code points.
    families = len({row[0].split('-')[0] for row in rows if row[0] not in RANKING_ROWS})
    return (1 + (surface == key and not key.isascii()), cost, -families, *tie, surface)


def transliteration(key, surface, rows):
    """Katakana spelling of the key (トクズ for とくず) without JMdict or plausible Mozc evidence of being a word."""
    return bool(KATAKANA.fullmatch(surface)) and search_key(surface) == key and not any(
        source == 'jmdict' or source == 'mozc' and plausible_mozc(surface, cost) for source, _, cost, *_ in rows)


def candidate_rows(rows, mozc=None, clicks=None, key=None, elsewhere=None):
    """{surface: [(source, rank, cost, pri, irregular)]} from rows (surface, source, rank, cost, pri[, original_reading[, inf]]);
    mozc: {surface: cost} of the key, attached as ('mozc', 0, cost, None, False) to existing candidates only; clicks: {title:
    clicks}, attached as ('clickstream', 0, clicks, None, False) to existing candidates whose surface is a title, unless
    elsewhere ({title: its only signal reading, or None for several}) shows a signal under a reading other than key."""
    rows = list(rows)
    by_surface = {row[0]: [] for row in rows}
    for surface, source, rank, cost, pri, *extra in rows:
        original, inf = (extra + [None, None])[:2]
        # A JMdict katakana reading marks a loanword: its priority belongs to that spelling, not to ateji like 珈琲.
        if (source == 'jmdict' and pri and original and original != surface and original in by_surface
                and KATAKANA.fullmatch(original)):
            by_surface[original].append((source, rank, None, pri, False))
            pri = None
        by_surface[surface].append((source, rank, cost, pri, bool(inf and IRREGULAR & set(inf.split('|')))))
    if mozc is not None:
        for surface, values in by_surface.items():
            values.append(('mozc', 0, mozc.get(surface), None, False))
    if clicks:
        for surface, values in by_surface.items():
            if surface in clicks and (not elsewhere or elsewhere.get(surface, key) == key):
                values.append(('clickstream', 0, clicks[surface], None, False))
    return by_surface


def nasi_candidates(key, rows, skk_sources=(), mozc=None, clicks=None, elsewhere=None):
    """Candidates of one okuri-nasi key in output order; rows, mozc, clicks and elsewhere as for candidate_rows."""
    by_surface = candidate_rows(rows, mozc, clicks, key, elsewhere)
    return sorted(by_surface, key=lambda s: candidate_order(key, s, by_surface[s], skk_sources))


def ari_candidates(key, rows, order, mozc=None):
    """Candidates of one okuri-ari key in output order; rows: (surface, source, rank, cost, pri, inf, confidence, base), base the
    dictionary form (reading, surface) of a generated row; order: {source: catalog position, skk-l first}; mozc: {base: cost},
    None when no Mozc is selected. Upstream SKK candidates keep source then source order (skk-l = SKK-JISYO.L first);
    rule-generated ones follow by the okuri-nasi cost of their dictionary forms."""
    upstream, generated = {}, {}
    for surface, source, rank, cost, pri, inf, confidence, base in rows:
        if confidence != GENERATED:
            upstream[surface] = min(upstream.get(surface, (order[source], rank)), (order[source], rank))
            continue
        values = generated.setdefault(surface, [])
        values.append((source, rank, cost, pri, bool(inf and IRREGULAR & set(inf.split('|')))))
        if mozc is not None:
            values.append(('mozc', 0, mozc.get(base), None, False))
    ranked = sorted(upstream, key=upstream.get)
    return ranked + sorted(set(generated) - set(upstream), key=lambda s: candidate_order(key, s, generated[s]))


def okuri_base(pos):
    """Dictionary form (reading, surface) recorded in the pos of a rule-generated okuri-ari row."""
    _, _, reading, surface = pos.split('|', 3)
    return search_key(reading), surface


def ranking_costs(con, sources, selected):
    """Stream (reading, {surface: lowest cost}) of the selected Mozc sources, in reading order."""
    ids = ranking_ids(sources, selected)
    if not ids:
        return iter(())
    query = ('SELECT reading,surface,min(cost) FROM costs WHERE source IN (' + ','.join('?' * len(ids))
             + ') GROUP BY reading,surface ORDER BY reading,surface')
    return ((key, {surface: cost for _, surface, cost in rows})
            for key, rows in itertools.groupby(con.execute(query, ids), key=lambda row: row[0]))


def ranking_ids(sources, selected, format='mozc'):
    return [s['id'] for s in sources if s['id'] in selected and s.get('format') == format]


def click_counts(con, sources, selected):
    """({article title: clicks} summed over the selected Clickstream sources, {title: its only signal reading, None for several}),
    both held in memory; (None, None) when no Clickstream is selected. Signal readings are those where the selected sources give
    the title's surface a plausible Mozc cost, a sudachi-small cost, JMdict priority tags or an skk-l candidate: the article's
    clicks then most likely belong to that word (月 is read つき, not ひかる), so other keys ignore them. With this guard the
    hold-out vs SKK-JISYO.L is 0.6827 adjacent / 0.7300 first-two (without Clickstream 0.6827 / 0.7300, unguarded 0.6825 / 0.7297)."""
    ids = ranking_ids(sources, selected, 'clickstream')
    if not ids:
        return None, None
    marks = lambda values: ','.join('?' * len(values))
    counts = dict(con.execute(f'SELECT surface,sum(clicks) FROM clicks WHERE source IN ({marks(ids)}) GROUP BY surface', ids))
    query = (f"SELECT reading,surface FROM entries WHERE source IN ({marks(selected)}) AND kind='nasi' AND scope IN ('all','skk') "
             "AND (source='skk-l' OR source='sudachi-small' AND cost!=0 OR source='jmdict' AND pri IS NOT NULL)")
    mozc = ranking_ids(sources, selected)
    if mozc:
        con.create_function('plausible_mozc', 2, plausible_mozc, deterministic=True)
        query += (f' UNION SELECT reading,surface FROM costs WHERE source IN ({marks(mozc)}) GROUP BY reading,surface'
                  ' HAVING plausible_mozc(surface,min(cost))')
    elsewhere = {}
    for reading, surface in con.execute(query, [*selected, *mozc]):
        if surface in counts:
            elsewhere[surface] = reading if elsewhere.get(surface, reading) == reading else None
    return counts, elsewhere


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
    # okuri-ari: keys descending; upstream candidates by source (skk-l, then catalog order), then rank in source;
    # rule-generated candidates after them by estimated cost of their dictionary form.
    order = {id: (id != 'skk-l', i) for i, (id,) in enumerate(con.execute('SELECT id FROM sources ORDER BY rowid'))}
    ari = {}
    for row in con.execute('SELECT reading,surface,source,rank,cost,pri,inf,confidence,pos FROM entries' + where
                           + " AND kind='ari' AND scope IN ('all','skk') ORDER BY reading,surface,source", selected):
        ari.setdefault(row[0], []).append(row[1:-1] + (okuri_base(row[-1]) if row[-2] == GENERATED else None,))
    mozc, ranking = None, ranking_ids(sources, selected)
    if ranking:
        lookup = ('SELECT min(cost) FROM costs WHERE reading=? AND surface=? AND source IN (' + ','.join('?' * len(ranking)) + ')')
        bases = sorted({row[-1] for rows in ari.values() for row in rows if row[-1]})
        mozc = {base: con.execute(lookup, (*base, *ranking)).fetchone()[0] for base in bases}
    # Candidate origins for the manifest: upstream SKK sources, or generated only (by Sudachi, JMdict or both).
    origin = Counter()
    for rows in ari.values():
        kinds = {}
        for surface, source, *_, confidence, _ in rows:
            kinds.setdefault(surface, set()).add('skk' if confidence != GENERATED else source.split('-')[0])
        origin['keys'] += 1
        origin['keys_with_upstream'] += any('skk' in k for k in kinds.values())
        for k in kinds.values():
            origin['candidates_' + ('skk' if 'skk' in k else '+'.join(sorted(k)))] += 1
    ari = {key: ari_candidates(key, rows, order, mozc) for key, rows in ari.items()}
    # okuri-nasi: keys ascending, streamed in primary-key order one key at a time.
    skk_sources = {s['id'] for s in sources if s.get('format') == 'skk'}
    query = ('SELECT reading,surface,source,rank,cost,pri,original_reading,inf FROM entries' + where
             + " AND kind='nasi' AND scope IN ('all','skk') ORDER BY reading,surface,source")
    groups = itertools.groupby(con.execute(query, selected), key=lambda row: row[0])
    # A second cursor streams Mozc costs alongside; candidates still come only from entries.
    clicks, elsewhere = click_counts(con, sources, selected)
    nasi = (('nasi', key, surface) for key, rows, mozc in join_costs(groups, ranking_costs(con, sources, selected))
            for surface in nasi_candidates(key, (row[1:] for row in rows), skk_sources, mozc, clicks, elsewhere))
    records = itertools.chain((('ari', key, surface) for key in sorted(ari, reverse=True) for surface in ari[key]), nasi)
    try:
        return write_export(output, records, sources, selected,
                            'kind (ari/nasi) TAB key TAB candidate in SKK output order; okuri-ari keys descending with upstream SKK candidates by source then source order, '
                            'then rule-generated ones (Sudachi inflected forms / JMdict conjugation tables split into stem+okuri letter) by the estimated cost of their dictionary form; '
                            'okuri-nasi keys ascending with skk-l candidates first in SKK-JISYO.L order, then by estimated cost (Mozc OSS cost when selected and present, '
                            'else sudachi-small cost/JMdict priority, else the Wikipedia Clickstream clicks of an article titled like the candidate when selected, before the source default '
                            'and no such signal or skk-l candidate gives that surface another reading, '
                            'else source defaults; JMdict irregular/rare/outdated-only forms after source defaults; JMdict search-only forms excluded); '
                            'Mozc and Clickstream rank only and add no candidates; normalization as the RustMigemo TSV', dict(okuri_ari=dict(sorted(origin.items()))))
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
                    sha256=digest(output), candidate_order='okuri-ari: upstream SKK candidates first (skk-l in SKK-JISYO.L order, then other SKK sources in catalog '
                                    'order, each in source order), then rule-generated candidates (stem+okuri letter split from Sudachi inflected forms '
                                    'or JMdict conjugation tables; no generative AI) by the okuri-nasi cost of their dictionary form; okuri-nasi: as in the input TSV '
                                    '(jisyo.py: skk-l first in SKK-JISYO.L order, then ascending estimated cost on the sudachi-small scale: '
                                    'Mozc OSS word cost (ranking-only source, quantile-mapped) when selected and present, otherwise sudachi-small cost '
                                    'or JMdict priority tags (+ a shift when missing from a selected Mozc); without those, the monthly Wikipedia Clickstream clicks '
                                    '(ranking-only source, CC0) of the article titled exactly like the candidate, quantile-mapped and shifted like the other '
                                    'signals, when that comes before the per-source default and the surface has no such signal or skk-l candidate under '
                                    'another reading; then per-source defaults; JMdict forms tagged only '
                                    'irregular/rare/outdated after the defaults; katakana spellings of the key without JMdict/Mozc evidence after other words, '
                                    'ASCII after those, kana-identical candidates last); not measured usage frequency; use after personal/base SKK dictionaries',
                    exclusions='unrepresentable plain SKK keys/candidates omitted; no Lisp expressions emitted; JMdict search-only forms (sK/sk) omitted')
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
