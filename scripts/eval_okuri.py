#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Measure rule-generated okuri-ari entries against the raw SKK-JISYO.L okuri-ari section (stdlib only).

Reads the locked raw sources (data/raw) through jisyo.rows, so every class that generates a pair is counted.
precision: of the generated (key, candidate) pairs whose key is an SKK-JISYO.L okuri-ari key, the share L also lists,
with the 95% Wilson interval; classes whose upper bound is below 0.9 are marked DROP (jisyo.OKURI_EXCLUDED);
known-stem precision: the same over pairs whose stem reading and stem L lists under some okuri letter (isolates the
letter/split rules from spellings L does not list); recall: share of L's pairs the generated pairs reproduce.
--all-classes also generates the excluded classes, to re-measure them.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import random
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jisyo

THRESHOLD = 0.9


def wilson(hits, total, z=1.96):
    if not total:
        return 0.0, 1.0
    p = hits / total
    centre, spread = p + z * z / (2 * total), z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - spread) / (1 + z * z / total), (centre + spread) / (1 + z * z / total)


def ratio(hits, total):
    return f'{hits / total:.3f} ({hits:,}/{total:,})' if total else 'n/a'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--sources', nargs='+', default=['sudachi-small', 'sudachi-core', 'sudachi-notcore', 'jmdict'])
    p.add_argument('--all-classes', action='store_true', help='also generate the classes in jisyo.OKURI_EXCLUDED')
    p.add_argument('--letters', action='store_true', help='also group by okuri letter')
    p.add_argument('--sample', type=int, default=0, help='print N random generated lines whose key is not in L')
    p.add_argument('--json', help='write the tables as JSON')
    args = p.parse_args()
    if args.all_classes:
        jisyo.OKURI_EXCLUDED = set()
    lock = {s['id']: s for s in json.loads(jisyo.LOCK.read_text())['sources']}
    truth = {(e.reading, e.surface) for e in jisyo.rows(lock['skk-l'], Counter()) if e.kind == 'ari'}
    keys, stems = {k for k, _ in truth}, {(k[:-1], s) for k, s in truth}
    generated = defaultdict(set)
    for id in args.sources:
        for entry in jisyo.rows(lock[id], Counter()):
            if entry.confidence == jisyo.GENERATED:
                generated[entry.reading, entry.surface].add((id, entry.pos.split('|')[1]))
    groupings = [('source', lambda source, cls, key: source), ('class', lambda source, cls, key: cls),
                 ('source/class', lambda source, cls, key: source + ' ' + cls)]
    if args.letters:
        groupings.append(('letter', lambda source, cls, key: key[-1]))
    tables = {}
    for name, group in groupings:
        stats = defaultdict(Counter)
        for pair, origins in generated.items():
            for label in {group(source, cls, pair[0]) for source, cls in origins}:
                s = stats[label]
                s['pairs'] += 1
                hit = pair in truth
                if pair[0] in keys:
                    s['key_in_l'] += 1
                    s['hits'] += hit
                if (pair[0][:-1], pair[1]) in stems:
                    s['stem_known'] += 1
                    s['stem_hits'] += hit
        print(f'== by {name}: generated pairs; precision on keys in L [95% interval]; known-stem precision; recall of L ({len(truth):,} pairs)')
        for label, s in sorted(stats.items(), key=lambda item: -item[1]['pairs']):
            low, high = wilson(s['hits'], s['key_in_l'])
            s['wilson_high'] = round(high, 3)
            print(f"  {label:26} {s['pairs']:7,}  prec {ratio(s['hits'], s['key_in_l']):22} [{low:.2f}-{high:.2f}]"
                  f"{' DROP' if high < THRESHOLD else '     '}  known-stem {ratio(s['stem_hits'], s['stem_known']):22}"
                  f"  recall {ratio(s['hits'], len(truth))}")
        tables[name] = {label: dict(s) for label, s in stats.items()}
    in_l = [pair for pair in generated if pair[0] in keys]
    hits = sum(pair in truth for pair in in_l)
    print(f'all generated: {len(generated):,} pairs; keys in L {len(in_l):,}; precision {ratio(hits, len(in_l))}; '
          f'recall {ratio(hits, len(truth))}')
    if args.sample:
        outside = sorted(pair for pair in generated if pair[0] not in keys)
        for key, surface in random.Random(0).sample(outside, min(args.sample, len(outside))):
            print(f"  {key} /{surface}/  {' '.join(sorted(source + ':' + cls for source, cls in generated[key, surface]))}")
    if args.json:
        Path(args.json).write_text(json.dumps(tables, ensure_ascii=False, indent=1, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
