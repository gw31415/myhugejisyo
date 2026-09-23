#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build and inspect release assets and complete corresponding sources; never publish."""
import argparse
import copy
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jisyo
from release_metadata import LICENSES, archive_directory, release_identity, skk_headers, write_documents
from verify_release import verify_archive

PROFILES = {
    'core': ['skk-l', 'skk-jinmei', 'skk-geo', 'skk-station', 'skk-propernoun',
             'skk-law', 'skk-hukugougo', 'skk-jis2004', 'sudachi-small', 'sudachi-core', 'sudachi-notcore'],
    'edrdg': ['jmdict', 'jmnedict'],
    'medical': ['jmed'],
}
PROFILES['all'] = PROFILES['core'] + ['jawiki', 'jmdict', 'jmnedict', 'jmed']
DOCUMENTS = ['LICENSES', 'LICENSING.md', 'THIRD_PARTY_NOTICES.md', 'CHANGES.md',
             'SOURCES.md', 'RELEASE.json', 'BUILD.md', 'UPSTREAM_NOTICES', 'third_party']


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def copy_path(source, dest):
    if source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def archive_sources(output, sources, stage, profiles):
    """Archive exact inputs, editable dictionaries, provenance and offline Rust sources."""
    archive_lock = copy.deepcopy(sources)
    for source in archive_lock:
        folder = stage/'data/raw'/source['id']
        folder.mkdir(parents=True)
        if source['id'] == 'jmed':
            # Only accepted vocabulary is licensed here; omit unrelated medical codes.
            jisyo.export(['jmed'], folder/'source')
            source['materialized_from'] = {k: source[k] for k in ('url', 'sha256', 'format', 'bytes', 'fetched_at')}
            source.update(format='reading-tsv', sha256=jisyo.digest(folder/'source'),
                          bytes=(folder/'source').stat().st_size,
                          materialization='Accepted normalized reading/surface pairs only; no external medical codes')
        else:
            copy_path(jisyo.CACHE/source['id']/'source', folder/'source')
        for notice in source['notice_files']:
            copy_path(jisyo.CACHE/source['id']/notice['file'], folder/notice['file'])
        jisyo.write_json(folder/'receipt.json', source)
    jisyo.write_json(stage/'sources.lock.json', dict(schema_version=1, sources=archive_lock))
    for name in ['jisyo.py', 'src', 'tests', 'Cargo.toml', 'Cargo.lock', 'README.md', 'LICENSE', 'scripts', '.github']:
        copy_path(ROOT/name, stage/name)
    copy_path(ROOT/'data/import-report.json', stage/'data/import-report.json')
    for profile in profiles:
        for name in [profile+'.tsv', profile+'.tsv.manifest.json']:
            copy_path(output/name, stage/'editable'/name)
    selected = sorted(s['id'] for s in sources)
    with closing(sqlite3.connect(f'file:{jisyo.DB}?mode=ro', uri=True)) as db, (stage/'provenance.tsv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        writer.writerow(['reading', 'surface', 'source', 'original_reading'])
        query = 'SELECT reading,surface,source,original_reading FROM entries WHERE source IN (' + ','.join('?' for _ in selected) + ') ORDER BY reading,surface,source'
        writer.writerows(db.execute(query, selected))
    vendor = stage/'vendor'
    config = subprocess.check_output(['cargo', 'vendor', '--locked', str(vendor)], cwd=ROOT, text=True)
    # Cargo emits an absolute path; source archive must remain relocatable.
    config = config.replace(str(vendor), 'vendor')
    (stage/'.cargo').mkdir()
    (stage/'.cargo/config.toml').write_text(config)
    archive_directory(stage, output/'dictionary-sources.tar.gz')


def package(output, profiles, repository, tag, modifier):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Release directory must be empty (do not mix old/new assets)')
    info = release_identity(ROOT, repository, tag, modifier, datetime.now(timezone.utc).isoformat())
    output.mkdir(parents=True, exist_ok=True)
    lock = json.loads(jisyo.LOCK.read_text())
    with closing(sqlite3.connect(f'file:{jisyo.DB}?mode=ro', uri=True)) as db:
        metadata = {row[0]: json.loads(row[1]) for row in db.execute('SELECT id,metadata FROM sources')}
    selected = {id for profile in profiles for id in PROFILES[profile]}
    sources = [s for s in lock['sources'] if s['id'] in selected]
    if {s['id'] for s in sources} != selected:
        raise ValueError('Missing release source in lock')
    for source in sources:
        if metadata.get(source['id']) != source:
            raise ValueError('DB/lock metadata mismatch; run ingest again')
        if jisyo.digest(jisyo.CACHE/source['id']/'source') != source['sha256']:
            raise ValueError('Raw source checksum mismatch')
    run('cargo', 'build', '--release', '--locked')
    info['profiles'] = {}
    with tempfile.TemporaryDirectory(prefix='myhugejisyo-release-') as temp:
        stage = Path(temp)/'source'
        stage.mkdir()
        for name in ['LICENSES', 'LICENSING.md', 'third_party']:
            copy_path(ROOT/name, stage/name)
        # Keep the downloaded GPL text at a stable path even when a profile's
        # root LICENSE is CC; the license download manifest must remain valid.
        copy_path(ROOT/'LICENSE', stage/'LICENSES/GPL-3.0.txt')
        license_manifest = json.loads((stage/'LICENSES/manifest.json').read_text())
        for record in license_manifest['files']:
            if record['file'] == 'LICENSE':
                record['file'] = 'LICENSES/GPL-3.0.txt'
        jisyo.write_json(stage/'LICENSES/manifest.json', license_manifest)
        for profile in profiles:
            suffix = '' if profile == 'all' else '-' + profile
            binary = output/('migemo-compact-dict'+suffix)
            skk = output/('SKK-JISYO.myhugejisyo'+suffix+'.utf8')
            tsv = output/(profile+'.tsv')
            jisyo.export(PROFILES[profile], tsv)
            manifest_path = Path(str(tsv)+'.manifest.json')
            manifest = json.loads(manifest_path.read_text())
            manifest.update(release=info['tag'], generated_at=info['generated_at'],
                            modified_by=info['modifier'], license=LICENSES[profile], profile=profile,
                            corresponding_source_url=info['corresponding_source_url'],
                            changes='CHANGES.md', attribution='THIRD_PARTY_NOTICES.md and UPSTREAM_NOTICES/')
            jisyo.write_json(manifest_path, manifest)
            run(str(ROOT/'target/release/myhugejisyo'), 'build', str(tsv), str(binary))
            skk_manifest = jisyo.export_skk(tsv, skk, skk_headers(info, profile))
            binary_manifest = dict(manifest, binary_sha256=jisyo.digest(binary), binary_bytes=binary.stat().st_size,
                                   verification='every exported pair round-tripped through RustMigemo')
            jisyo.write_json(str(binary)+'.manifest.json', binary_manifest)
            copy_path(Path(str(tsv)+'.notices'), stage/'UPSTREAM_NOTICES')
            info['profiles'][profile] = dict(license=LICENSES[profile], source_ids=PROFILES[profile],
                reading_surface_pairs=manifest['pairs'], skk=skk_manifest['skk'],
                tsv_sha256=jisyo.digest(tsv),
                dictionary_sha256={binary.name: jisyo.digest(binary), skk.name: jisyo.digest(skk)})
        write_documents(stage, ROOT, info, sources)
        for profile in profiles:
            with tempfile.TemporaryDirectory(prefix='myhugejisyo-bundle-') as bundle_temp:
                bundle = Path(bundle_temp)
                for name in DOCUMENTS:
                    copy_path(stage/name, bundle/name)
                license_path = ROOT/'LICENSE' if LICENSES[profile] == 'GPL-3.0-only' else ROOT/'LICENSES'/(LICENSES[profile]+'.txt')
                copy_path(license_path, bundle/'LICENSE')
                for name in info['profiles'][profile]['dictionary_sha256']:
                    copy_path(output/name, bundle/name)
                    copy_path(output/(name+'.manifest.json'), bundle/(name+'.manifest.json'))
                for name in [profile+'.tsv', profile+'.tsv.manifest.json']:
                    copy_path(output/name, bundle/name)
                archive_directory(bundle, output/(profile+'-dictionaries.tar.gz'))
        archive_sources(output, sources, stage, profiles)
        for name in ['RELEASE_NOTES.md', 'CHANGES.md', 'SOURCES.md', 'RELEASE.json']:
            copy_path(stage/name, output/name)
    jisyo.write_json(output/'sources.lock.json', dict(schema_version=1, sources=sources))
    jisyo.write_json(output/'release-manifest.json', info)
    for profile in profiles:
        (output/(profile+'.tsv')).unlink()
        (output/(profile+'.tsv.manifest.json')).unlink()
        shutil.rmtree(output/(profile+'.tsv.notices'))
    for archive in sorted(output.glob('*.tar.gz')):
        verify_archive(archive)
    files = sorted(p for p in output.iterdir() if p.is_file())
    for path in files:
        if not path.stat().st_size or path.stat().st_size >= 2*1024**3:
            raise ValueError(f'Invalid GitHub asset size: {path.name}')
    (output/'SHA256SUMS').write_text(''.join(f'{jisyo.digest(p)}  {p.name}\n' for p in files))
    print('Release archives inspected and prepared:', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT/'dist/release'))
    parser.add_argument('--profiles', choices=PROFILES, nargs='+', default=list(PROFILES))
    parser.add_argument('--repository', default='gw31415/myhugejisyo')
    parser.add_argument('--tag', default='local-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--modifier', default='gw31415/myhugejisyo maintainers')
    args = parser.parse_args()
    package(args.output, args.profiles, args.repository, args.tag, args.modifier)
