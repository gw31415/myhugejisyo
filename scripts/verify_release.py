#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Validate actual archive contents without extracting untrusted paths."""
import argparse
import json
from pathlib import Path, PurePosixPath
import tarfile
from release_metadata import LICENSES, stream_digest

REQUIRED = {'LICENSE', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md', 'CHANGES.md',
            'SOURCES.md', 'RELEASE.json', 'BUILD.md', 'CONTENTS.sha256.json'}


def verify_archive(path):
    with tarfile.open(path, 'r:gz') as archive:
        files = {}
        for member in archive:
            name = member.name
            if name.startswith('/') or '..' in PurePosixPath(name).parts or member.issym() or member.islnk():
                raise ValueError(f'Unsafe archive member: {name}')
            if member.isfile():
                if name in files:
                    raise ValueError(f'Duplicate member: {name}')
                stream = archive.extractfile(member)
                files[name] = stream_digest(stream)
        if not REQUIRED <= files.keys():
            raise ValueError(f'Missing release documents: {REQUIRED - files.keys()}')
        expected = json.load(archive.extractfile('CONTENTS.sha256.json'))
        if expected != {k: v for k, v in files.items() if k != 'CONTENTS.sha256.json'}:
            raise ValueError('Archive content checksum mismatch')
        license_manifest = json.load(archive.extractfile('LICENSES/manifest.json'))
        for record in license_manifest['files']:
            if files.get(record['file']) != record['sha256']:
                raise ValueError(f"License/notice checksum mismatch: {record['file']}")
        info = json.load(archive.extractfile('RELEASE.json'))
        if not info['corresponding_source_url'].endswith('/'+info['tag']+'/dictionary-sources.tar.gz'):
            raise ValueError('Corresponding source must be tied to this release tag')
        for profile, record in info['profiles'].items():
            for name, checksum in record['dictionary_sha256'].items():
                if name in files and files[name] != checksum:
                    raise ValueError(f'Output does not match release: {name}')
        if path.name == 'dictionary-sources.tar.gz':
            for required in ['Cargo.lock', '.cargo/config.toml', 'scripts/rebuild.py', 'jisyo.py', 'provenance.tsv']:
                if required not in files:
                    raise ValueError(f'Missing corresponding source: {required}')
            if files.get('LICENSE') != files.get('LICENSES/GPL-3.0.txt'):
                raise ValueError('Source code LICENSE must be GPLv3')
            lock = json.load(archive.extractfile('sources.lock.json'))
            for source in lock['sources']:
                if files.get('data/raw/'+source['id']+'/source') != source['sha256']:
                    raise ValueError('Archived input does not match lock')
            for profile, record in info['profiles'].items():
                if files.get('editable/'+profile+'.tsv') != record['tsv_sha256']:
                    raise ValueError('Missing or mismatched editable vocabulary')
        else:
            profile = path.name.removesuffix('-dictionaries.tar.gz')
            record = info['profiles'][profile]
            license_file = 'LICENSES/' + ('GPL-3.0' if LICENSES[profile] == 'GPL-3.0-only' else LICENSES[profile]) + '.txt'
            if not files.get(license_file) or files.get('LICENSE') != files[license_file]:
                raise ValueError('Profile LICENSE mismatch')
            if files.get(profile+'.tsv') != record['tsv_sha256']:
                raise ValueError('Missing editable vocabulary')
            for name, checksum in record['dictionary_sha256'].items():
                if files.get(name) != checksum:
                    raise ValueError('Missing dictionary')
    print(f'Verified {path.name}: {len(files)} files')


def verify_directory(directory):
    expected = json.loads((directory/'CONTENTS.sha256.json').read_text())
    for name, checksum in expected.items():
        if name.startswith('/') or '..' in PurePosixPath(name).parts:
            raise ValueError('Unsafe checksum path')
        with (directory/name).open('rb') as f:
            if stream_digest(f) != checksum:
                raise ValueError(f'Checksum mismatch: {name}')
    print(f'Verified {len(expected)} source files')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', nargs='*', type=Path)
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args()
    if args.directory:
        verify_directory(args.directory)
    for path in args.archives:
        verify_archive(path)
