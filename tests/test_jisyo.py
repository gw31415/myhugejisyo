# SPDX-License-Identifier: GPL-3.0-only
import io
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
import zipfile
import jisyo


class ConversionTests(unittest.TestCase):
    def test_reading_normalized_surface_preserved(self):
        self.assertEqual(jisyo.normalize(' ｳﾞｨｰＲ '), 'ゔぃーr')
        self.assertTrue(jisyo.supported('ゔぁーai'))
        self.assertFalse(jisyo.supported('読み'))
        self.assertFalse(jisyo.supported('あ\nい'))
        self.assertFalse(jisyo.supported(''))

    def test_separator_search_key(self):
        self.assertEqual(jisyo.search_key('アーガイル・チェック'), 'あーがいるちぇっく')
        self.assertEqual(jisyo.search_key('ニジュウゴジ、ナイトコードデ。'), 'にじゅうごじないとこーどで')
        self.assertEqual(jisyo.search_key('C++'), 'c++')

    def test_sudachi_unicode_escape(self):
        self.assertEqual(jisyo.unescape_sudachi(r'\u002C\u{20BB7}ヴ'), ',𠮷ヴ')
        self.assertEqual(jisyo.unescape_sudachi('日本語'), '日本語')

    def test_skk_okuri_annotations_templates_and_expressions(self):
        source = ''';; okuri-ari entries.
かk /書/描/
;; okuri-nasi entries.
けんさく /検索;annotation/研削/
#かい /第#0回/
てすと /(concat "a/b")/テスト/
あんぜん /安全/
'''
        stats = Counter()
        self.assertEqual(list(jisyo.parse_skk(io.StringIO(source), stats)),
                         [('けんさく', '検索', ''), ('けんさく', '研削', ''), ('あんぜん', '安全', '')])
        self.assertEqual(stats['skk_okuri_entry'], 1)
        self.assertEqual(stats['skk_expression_entry'], 1)

    def test_jmdict_restrictions_no_kanji_and_kana(self):
        xml = b'''<!DOCTYPE JMdict [<!ENTITY n "noun">]><JMdict><entry>
        <k_ele><keb>A</keb></k_ele><k_ele><keb>B</keb></k_ele>
        <r_ele><reb>a</reb><re_restr>A</re_restr></r_ele>
        <r_ele><reb>b</reb><re_nokanji/></r_ele>
        <r_ele><reb>c</reb></r_ele><sense><pos>&n;</pos></sense>
        </entry><entry><r_ele><reb>d</reb></r_ele></entry></JMdict>'''
        self.assertEqual(list(jisyo.parse_jmdict(io.BytesIO(xml), Counter())),
                         [('a', 'A', 'noun'), ('b', 'b', 'noun'), ('c', 'A', 'noun'), ('c', 'B', 'noun'), ('d', 'd', '')])

    def test_xlsx_sparse_inline_shared_strings(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('xl/sharedStrings.xml', '<sst xmlns="'+jisyo.NS['m']+'"><si><t>読み</t></si></sst>')
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="'+jisyo.NS['m']+'"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="C1" t="inlineStr"><is><t>表記</t></is></c></row><row><c r="A2" t="inlineStr"/><c r="C2" t="inlineStr"><is><t>検索</t></is></c></row></sheetData></worksheet>')
        self.assertEqual(list(jisyo.xlsx_rows(data.getvalue())), [{'A': '読み', 'C': '表記'}, {'A': '', 'C': '検索'}])

    def test_materialized_vocabulary_input(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            folder = root/'jmed'
            folder.mkdir()
            path = folder/'source'
            path.write_text('しんきんこうそく\t心筋梗塞\n')
            source = {'id': 'jmed', 'format': 'reading-tsv', 'sha256': jisyo.digest(path)}
            with patch.object(jisyo, 'CACHE', root):
                self.assertEqual(list(jisyo.rows(source, Counter())),
                                 [('しんきんこうそく', '心筋梗塞', 'materialized vocabulary')])
                path.write_text('reading\tsurface\texternal code\n')
                source['sha256'] = jisyo.digest(path)
                with self.assertRaises(ValueError):
                    list(jisyo.rows(source, Counter()))

    def test_utf8_skk_plain_candidates_and_exclusion_counts(self):
        import json
        pairs = sorted([('けんさく', '検索'), ('けんさく', '研削'),
                        ('きごう', 'a/b'), ('きごう', '(evil)'), ('きごう', 'a;b'),
                        ('あ a', '空白キー'), ('かk', '書'), ('よし', '𠮷')])
        with tempfile.TemporaryDirectory() as d:
            tsv = Path(d)/'input.tsv'
            tsv.write_text(''.join(k+'\t'+v+'\n' for k,v in pairs), encoding='utf-8')
            Path(str(tsv)+'.manifest.json').write_text(json.dumps({'sources': []}))
            out = Path(d)/'SKK-JISYO.test.utf8'
            manifest = jisyo.export_skk(tsv, out)
            self.assertIn('coding: utf-8', out.read_text())
            actual = list(jisyo.parse_skk(io.StringIO(out.read_text()), Counter()))
            self.assertEqual(actual, [('けんさく', '検索', ''), ('けんさく', '研削', ''), ('よし', '𠮷', '')])
            self.assertEqual(manifest['skk']['pairs'], 3)
            self.assertEqual(manifest['skk']['unsupported_key_pairs'], 2)
            self.assertEqual(manifest['skk']['unsafe_candidate_pairs'], 3)
            original = out.read_bytes()
            tsv.write_text('よし\t𠮷\nあ\t亜\n')
            with self.assertRaises(ValueError):
                jisyo.export_skk(tsv, out)
            self.assertEqual(out.read_bytes(), original)

    def test_ingest_dedup_provenance_export_and_failure_preserves_database(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sources = []
            for id in ['one', 'two']:
                folder = root / 'raw' / id
                folder.mkdir(parents=True)
                (folder / 'source').write_text('けんさく /検索/検索/\nむこう /無効/\n')
                sources.append(dict(id=id, format='skk', encoding='utf-8', sha256=jisyo.digest(folder/'source'), notice_files=[]))
            with patch.multiple(jisyo, DB=root/'db.sqlite', CACHE=root/'raw', ROOT=root):
                jisyo.ingest(sources)
                jisyo.export(None, root/'all.tsv')
                self.assertEqual((root/'all.tsv').read_text(), 'けんさく\t検索\nむこう\t無効\n')
                import sqlite3
                with sqlite3.connect(root/'db.sqlite') as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 4)
                original = jisyo.digest(root/'db.sqlite')
                (root/'raw/one/source').write_text('corrupt')
                with self.assertRaises(ValueError):
                    jisyo.ingest(sources)
                self.assertEqual(jisyo.digest(root/'db.sqlite'), original)

if __name__ == '__main__':
    unittest.main()
