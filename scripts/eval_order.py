#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Evaluate okuri-nasi candidate order against the raw SKK-JISYO.L (stdlib only).

(a) Hold-out: jisyo.py's pseudo-cost (with Mozc costs and Clickstream clicks when selected) computed WITHOUT skk-l rows, compared with SKK-JISYO.L's
    candidate order (adjacent pairs and first two candidates), next to the lexical baseline.
(b) Keys whose first okuri-nasi candidate is pure hiragana, katakana or ASCII, lexical vs. actual order.
(c) First candidates of fixed probe keys; with --db also by the pseudo-cost alone (skk-l rows ignored).
(b) and (c) read --skk when given, otherwise the order jisyo.py computes from --db.
"""
import argparse
from collections import Counter
import itertools
import json
from pathlib import Path
import re
import sqlite3
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jisyo

PROBES = ('かんじ こうせい きかん しよう かく はし あめ こうえん せいかく たなか とうきょう こーひー ばんでん とくず てれび あるごりずむ いく '
          'さとう すずき やまだ おおさか しんじゅく ふじさん みやざきはやお なつめそうせき あきら ひかる さくら みなと はやし').split()
HIRAGANA, KATAKANA = re.compile('[ぁ-ゖーゝゞ]+'), re.compile('[ァ-ヺー・ヽヾ]+')


def skk_l_order(path, encoding):
    order = {}
    with open(path, encoding=encoding) as f:
        for entry in jisyo.parse_skk(f, Counter()):
            if entry.kind == 'nasi':
                candidates = order.setdefault(jisyo.search_key(entry.reading), [])
                if entry.surface not in candidates:
                    candidates.append(entry.surface)
    return {key: c for key, c in order.items() if len(c) >= 2}


def db_keys(db, sources):
    """Yield (key, rows, selected SKK source IDs, Mozc costs, Clickstream clicks, their other readings) in key order."""
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    metadata = [json.loads(row[0]) for row in con.execute('SELECT metadata FROM sources')]
    meta = {s['id']: s['format'] for s in metadata}
    selected = sorted(sources or meta)
    skk_sources = {i for i in selected if meta[i] == 'skk'}
    query = ('SELECT reading,surface,source,rank,cost,pri,original_reading,inf FROM entries WHERE source IN (' + ','.join('?' * len(selected))
             + ") AND kind='nasi' AND scope IN ('all','skk') ORDER BY reading,surface,source")
    clicks, elsewhere = jisyo.click_counts(con, metadata, selected)
    groups = itertools.groupby(con.execute(query, selected), key=lambda row: row[0])
    try:
        for key, rows, mozc in jisyo.join_costs(groups, jisyo.ranking_costs(con, metadata, selected)):
            yield key, [row[1:] for row in rows], skk_sources, mozc, clicks, elsewhere
    finally:
        con.close()


def skk_file_keys(path):
    nasi = False
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith(';'):
                nasi = nasi or line.startswith(';; okuri-nasi entries.')
                continue
            if nasi:
                key, _, rest = line.rstrip('\n').partition(' ')
                yield key, rest[1:-1].split('/')


def first_kind(candidate):
    return ('ascii' if candidate.isascii() else 'hiragana' if HIRAGANA.fullmatch(candidate)
            else 'katakana' if KATAKANA.fullmatch(candidate) else None)


def ratio(hits, total):
    return f'{hits / total:.4f} ({hits:,}/{total:,})' if total else 'n/a'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--skk-l', required=True, help='raw SKK-JISYO.L (e.g. data/raw/skk-l/source)')
    p.add_argument('--encoding', default='euc_jp')
    p.add_argument('--db', help='SQLite master (data/lexicon.sqlite3)')
    p.add_argument('--sources', nargs='+', help='source IDs as for jisyo.py skk; omitted = all')
    p.add_argument('--skk', help='generated UTF-8 SKK dictionary for (b) and (c)')
    args = p.parse_args()
    if not args.db and not args.skk:
        p.error('give --db and/or --skk')
    truth = skk_l_order(args.skk_l, args.encoding)
    first = Counter()
    probes, held_probes = {}, {}

    def count_first(key, candidates):
        if len(candidates) >= 2:
            first['keys'] += 1
            for label, value in (('lexical', min(candidates)), ('ordered', candidates[0])):
                kind = first_kind(value) if not key.isascii() else None
                if kind:
                    first[label, kind] += 1
        if key in PROBES:
            probes[key] = candidates[:8]

    if args.db:
        stats = Counter()
        for key, rows, skk_sources, mozc, clicks, elsewhere in db_keys(args.db, args.sources):
            if not args.skk:
                count_first(key, jisyo.nasi_candidates(key, rows, skk_sources, mozc, clicks, elsewhere))
            if key not in truth and key not in PROBES:
                continue
            # Ignore rule 1: pseudo-cost from every selected source except skk-l itself.
            # skk-l still marks other readings of a title (elsewhere); this key's skk-l rows are ignored.
            held = jisyo.candidate_rows((row for row in rows if row[1] != 'skk-l'), mozc, clicks, key, elsewhere)
            if key in PROBES:
                held_probes[key] = sorted(held, key=lambda s: jisyo.candidate_order(key, s, held[s], skk_sources))[:8]
            if key not in truth:
                continue
            def order(s):
                rows = held.get(s)
                return jisyo.candidate_order(key, s, rows, skk_sources) if rows else (1, jisyo.OTHER_COST + 1, 0, '', 0, s)
            candidates = truth[key]
            for i, (a, b) in enumerate(zip(candidates, candidates[1:])):
                signal = all(held.get(s) and jisyo.signal_cost(s, held[s]) is not None for s in (a, b))
                agree, lexical = order(a) < order(b), a < b
                for scope in ('adjacent', 'first-two') if i == 0 else ('adjacent',):
                    stats[scope] += 1
                    stats[scope, 'agree'] += agree
                    stats[scope, 'lexical'] += lexical
                    stats[scope, 'signal'] += bool(signal)
                    stats[scope, 'signal-agree'] += bool(signal) and agree
        print(f"(a) hold-out vs SKK-JISYO.L ({len(truth):,} keys with >=2 candidates; selection: {' '.join(args.sources) if args.sources else 'all'})")
        for scope in ('adjacent', 'first-two'):
            n = stats[scope]
            print(f"  {scope:9} pseudo-cost {ratio(stats[scope, 'agree'], n)}  lexical {ratio(stats[scope, 'lexical'], n)}"
                  f"  coverage {ratio(stats[scope, 'signal'], n)}  agreement with signal {ratio(stats[scope, 'signal-agree'], stats[scope, 'signal'])}")
    if args.skk:
        for key, candidates in skk_file_keys(args.skk):
            count_first(key, candidates)
    print(f"(b) first candidate of {first['keys']:,} kana keys with >=2 candidates")
    for kind in ('hiragana', 'katakana', 'ascii'):
        print(f"  {kind:8} lexical {first['lexical', kind]:,}  ordered {first['ordered', kind]:,}")
    print('(c) probes')
    for key in PROBES:
        print(f"  {key} /{'/'.join(probes.get(key, []))}/")
    if held_probes:
        print('(c) probes by pseudo-cost alone (skk-l rows ignored)')
        for key in PROBES:
            print(f"  {key} /{'/'.join(held_probes.get(key, []))}/")


if __name__ == '__main__':
    main()
