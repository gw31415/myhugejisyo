#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Rebuild corresponding dictionaries offline from the editable source snapshot."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jisyo
from release_metadata import skk_headers

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-changes', action='store_true')
    args = parser.parse_args()
    info = json.loads((ROOT/'RELEASE.json').read_text())
    subprocess.run(['cargo', 'build', '--release', '--locked', '--offline'], cwd=ROOT, check=True)
    output = ROOT/'rebuilt'
    output.mkdir(exist_ok=True)
    for profile, record in info['profiles'].items():
        tsv = ROOT/'editable'/(profile+'.tsv')
        for name, expected in record['dictionary_sha256'].items():
            path = output/name
            if name.startswith('migemo-'):
                subprocess.run([str(ROOT/'target/release/myhugejisyo'), 'build', str(tsv), str(path)], check=True)
            else:
                jisyo.export_skk(tsv, path, skk_headers(info, profile))
            if not args.allow_changes and jisyo.digest(path) != expected:
                raise ValueError(f'Rebuild differs: {name}')
    print('Rebuilt all profiles; original hashes matched' if not args.allow_changes else 'Rebuilt edited dictionaries')
