# SPDX-License-Identifier: GPL-3.0-only
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from release_metadata import archive_directory, release_identity, skk_headers
from verify_release import REQUIRED, verify_archive
import jisyo


class ReleaseTests(unittest.TestCase):
    def make_bundle(self, stage):
        for name in REQUIRED - {'CONTENTS.sha256.json'}:
            (stage/name).write_text('notice\n')
        (stage/'LICENSES').mkdir()
        (stage/'LICENSES/manifest.json').write_text('{"files": []}')
        (stage/'LICENSES/GPL-3.0.txt').write_text('notice\n')
        (stage/'all.tsv').write_text('あ\t亜\n')
        (stage/'migemo-compact-dict').write_bytes(b'dictionary')
        info = dict(tag='test-1', corresponding_source_url='https://github.com/a/b/releases/download/test-1/dictionary-sources.tar.gz',
                    profiles={'all': dict(tsv_sha256=jisyo.digest(stage/'all.tsv'), dictionary_sha256={'migemo-compact-dict': jisyo.digest(stage/'migemo-compact-dict')})})
        (stage/'RELEASE.json').write_text(json.dumps(info))

    def test_archive_detects_missing_notice_and_inconsistent_dictionary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stage = root/'stage'
            stage.mkdir()
            self.make_bundle(stage)
            archive = root/'all-dictionaries.tar.gz'
            archive_directory(stage, archive)
            verify_archive(archive)
            (stage/'migemo-compact-dict').write_bytes(b'changed')
            archive_directory(stage, archive)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                verify_archive(archive)
            (stage/'CHANGES.md').unlink()
            archive_directory(stage, archive)
            with self.assertRaisesRegex(ValueError, 'Missing release documents'):
                verify_archive(archive)

    def test_archive_rejects_incorrect_license_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stage = root/'stage'
            stage.mkdir()
            self.make_bundle(stage)
            (stage/'LICENSES/manifest.json').write_text(json.dumps({'files': [
                {'file': 'LICENSE', 'sha256': 'incorrect'}]}))
            path = root/'all-dictionaries.tar.gz'
            archive_directory(stage, path)
            with self.assertRaisesRegex(ValueError, 'License/notice checksum mismatch'):
                verify_archive(path)

    def test_archive_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'bad.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                member = tarfile.TarInfo('../escape')
                member.size = 1
                archive.addfile(member, io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError, 'Unsafe archive'):
                verify_archive(path)

    def test_skk_reproducible_release_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tsv = root/'all.tsv'
            tsv.write_text('あ\t亜\n')
            Path(str(tsv)+'.manifest.json').write_text('{}')
            info = dict(modifier='maintainers', generated_at='2026-09-24T00:00:00Z', tag='test-1',
                        corresponding_source_url='https://example.com/test-1/dictionary-sources.tar.gz')
            for name in ['first', 'second']:
                jisyo.export_skk(tsv, root/name, skk_headers(info, 'all'))
            self.assertEqual((root/'first').read_bytes(), (root/'second').read_bytes())
            self.assertIn('GPL-3.0-only', (root/'first').read_text())
            self.assertIn(info['corresponding_source_url'], (root/'first').read_text())

    def test_release_identity_rejects_unsafe_tag(self):
        with self.assertRaises(ValueError):
            release_identity(Path('.'), 'owner/repo', '../../other', 'maintainer', 'date')
