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
かk /書;write/描/[く/書/]/[け/描/]/
>わたs /渡/
へんしょう /貶称/
#かk /#1書/
;; okuri-nasi entries.
けんさく /検索;annotation/研削/
#かい /第#0回/
かk /書/
てすと /(concat "a/b")/テスト/[x/X/]/試験;note/
あんぜん /安全/
'''
        stats = Counter()
        E = jisyo.Entry
        self.assertEqual(list(jisyo.parse_skk(io.StringIO(source), stats)),
                         [E('かk', '書', '', 'ari', 'skk'), E('かk', '描', '', 'ari', 'skk'), E('>わたs', '渡', '', 'ari', 'skk'),
                          E('けんさく', '検索', ''), E('けんさく', '研削', ''),
                          E('てすと', 'テスト', '', 'nasi', 'skk'), E('てすと', '試験', '', 'nasi', 'skk'),
                          E('あんぜん', '安全', '')])
        self.assertEqual(stats['skk_okuri_entry'], 2)
        self.assertEqual(stats['skk_malformed_entry'], 1)
        self.assertEqual(stats['skk_template_entry'], 3)
        self.assertEqual(stats['skk_expression_entry'], 2)
        self.assertEqual(stats['skk_expression_candidate'], 11)

    def test_sudachi_inflected_forms_are_migemo_only(self):
        def row(reading, surface, form, cost):
            return ','.join([surface, '1', '1', str(cost), surface, '動詞', '一般', '*', '*', '五段-カ行', form, reading] + ['*'] * 7)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'sudachi').mkdir()
            path = root/'sudachi/source'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('small_lex.csv', '\n'.join([row('カク', '書く', '終止形-一般', 5000), row('カカ', '書か', '未然形-一般', 6000),
                                                       row('ヒト', '人', '*', 3000)]) + '\n')
            source = {'id': 'sudachi', 'format': 'sudachi', 'sha256': jisyo.digest(path)}
            with patch.object(jisyo, 'CACHE', root):
                self.assertEqual([(e.reading, e.surface, e.scope, e.cost) for e in jisyo.rows(source, Counter())],
                                 [('カク', '書く', 'all', 5000), ('カカ', '書か', 'migemo', 6000), ('ヒト', '人', 'all', 3000)])
            with patch.multiple(jisyo, DB=root/'db.sqlite', CACHE=root, ROOT=root):
                jisyo.ingest([dict(source, notice_files=[])])
                jisyo.export(None, root/'migemo.tsv')
                jisyo.export_skk_tsv(None, root/'skk.tsv')
            self.assertEqual((root/'migemo.tsv').read_text(), 'かか\t書か\nかく\t書く\nひと\t人\n')
            self.assertEqual((root/'skk.tsv').read_text(), 'nasi\tかく\t書く\nnasi\tひと\t人\n')

    def test_jmdict_restrictions_no_kanji_and_kana(self):
        xml = b'''<!DOCTYPE JMdict [<!ENTITY n "noun">]><JMdict><entry>
        <k_ele><keb>A</keb><ke_pri>news1</ke_pri><ke_pri>ichi1</ke_pri></k_ele><k_ele><keb>B</keb></k_ele>
        <r_ele><reb>a</reb><re_restr>A</re_restr><re_pri>spec1</re_pri></r_ele>
        <r_ele><reb>b</reb><re_nokanji/></r_ele>
        <r_ele><reb>c</reb></r_ele><sense><pos>&n;</pos></sense>
        </entry><entry><r_ele><reb>d</reb></r_ele></entry></JMdict>'''
        entries = list(jisyo.parse_jmdict(io.BytesIO(xml), Counter()))
        self.assertEqual([e[:3] for e in entries],
                         [('a', 'A', 'noun'), ('b', 'b', 'noun'), ('c', 'A', 'noun'), ('c', 'B', 'noun'), ('d', 'd', '')])
        self.assertEqual([e.pri for e in entries], ['ichi1|news1|spec1', None, 'ichi1|news1', None, None])

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
                self.assertEqual([e[:3] for e in jisyo.rows(source, Counter())],
                                 [('しんきんこうそく', '心筋梗塞', 'materialized vocabulary')])
                path.write_text('reading\tsurface\texternal code\n')
                source['sha256'] = jisyo.digest(path)
                with self.assertRaises(ValueError):
                    list(jisyo.rows(source, Counter()))

    def test_utf8_skk_plain_candidates_and_exclusion_counts(self):
        import json
        pairs = sorted([('けんさく', '検索'), ('けんさく', '研削'),
                        ('きごう', 'a/b'), ('きごう', '(evil)'), ('きごう', 'a;b'),
                        ('あ a', '空白キー'), ('かk', '書'), ('よし', '𠮷'), ('きごう', '記号')])
        with tempfile.TemporaryDirectory() as d:
            tsv = Path(d)/'input.tsv'
            tsv.write_text(''.join('nasi\t'+k+'\t'+v+'\n' for k,v in pairs), encoding='utf-8')
            Path(str(tsv)+'.manifest.json').write_text(json.dumps({'sources': []}))
            out = Path(d)/'SKK-JISYO.test.utf8'
            manifest = jisyo.export_skk(tsv, out)
            self.assertIn('coding: utf-8', out.read_text())
            actual = [e[:3] for e in jisyo.parse_skk(io.StringIO(out.read_text()), Counter())]
            self.assertEqual(actual, [('きごう', '記号', ''), ('けんさく', '検索', ''), ('けんさく', '研削', ''), ('よし', '𠮷', '')])
            self.assertEqual(manifest['skk']['pairs'], 4)
            self.assertEqual(manifest['skk']['unsupported_key_pairs'], 2)
            self.assertEqual(manifest['skk']['unsafe_candidate_pairs'], 3)
            original = out.read_bytes()
            for bad in ['nasi\tよし\t𠮷\nnasi\tあ\t亜\n', 'ari\tあk\t開\nari\tかk\t書\n', 'nasi\tあ\t亜\nari\tかk\t書\n']:
                tsv.write_text(bad)
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

    def test_ingest_keeps_rank_lowest_cost_first_pri_and_widest_scope(self):
        E = jisyo.Entry
        parsed = [E('あ', '亜', 'p1', cost=50), E('い', '以', '', scope='skk'), E('あ', '亜', 'p2', cost=10, pri='news1'),
                  E('い', '以', '', scope='migemo'), E('う', '宇', '', pri='ichi1'), E('う', '宇', '', pri='spec1'),
                  E('え', '江', '', scope='skk'), E('え', '江', '', scope='skk')]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(jisyo, DB=root/'db.sqlite', ROOT=root, rows=lambda source, stats: iter(parsed)):
                jisyo.ingest([dict(id='one')])
                import sqlite3
                with sqlite3.connect(root/'db.sqlite') as db:
                    self.assertEqual(db.execute('SELECT reading,kind,scope,rank,cost,pri,pos FROM entries ORDER BY reading').fetchall(),
                                     [('あ', 'nasi', 'all', 0, 10, 'news1', 'p1'), ('い', 'nasi', 'all', 1, None, None, ''),
                                      ('う', 'nasi', 'all', 4, None, 'ichi1', ''), ('え', 'nasi', 'skk', 6, None, None, '')])

    def test_okuri_ari_round_trip_scopes_and_migemo_export(self):
        sources = {'skk-l': ''';; okuri-ari entries.
かk /書/描/
うごk /動/
おおk /大/多/
;; okuri-nasi entries.
てすと /(concat "x")/テスト/
''', 'other': ''';; okuri-ari entries.
かk /掛/書/欠/
;; okuri-nasi entries.
かか /書か/
'''}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            lock = []
            for id in ['other', 'skk-l']:
                folder = root/'raw'/id
                folder.mkdir(parents=True)
                (folder/'source').write_text(sources[id])
                lock.append(dict(id=id, format='skk', encoding='utf-8', sha256=jisyo.digest(folder/'source'), notice_files=[]))
            with patch.multiple(jisyo, DB=root/'db.sqlite', CACHE=root/'raw', ROOT=root):
                jisyo.ingest(lock)
                jisyo.export(None, root/'migemo.tsv')
                self.assertEqual((root/'migemo.tsv').read_text(), 'かか\t書か\n')
                jisyo.export_skk_tsv(None, root/'skk.tsv')
                self.assertEqual((root/'skk.tsv').read_text(),
                                 'ari\tかk\t書\nari\tかk\t描\nari\tかk\t掛\nari\tかk\t欠\nari\tおおk\t大\nari\tおおk\t多\n'
                                 'ari\tうごk\t動\nnasi\tかか\t書か\nnasi\tてすと\tテスト\n')
                jisyo.export_skk(root/'skk.tsv', root/'SKK-JISYO.test')
                text = (root/'SKK-JISYO.test').read_text()
                self.assertIn(';; okuri-ari entries.\nかk /書/描/掛/欠/\nおおk /大/多/\nうごk /動/\n;; okuri-nasi entries.\nかか /書か/\nてすと /テスト/\n', text)
                self.assertEqual([e[:5] for e in jisyo.parse_skk(io.StringIO(text), Counter())][:3],
                                 [('かk', '書', '', 'ari', 'skk'), ('かk', '描', '', 'ari', 'skk'), ('かk', '掛', '', 'ari', 'skk')])

class CandidateOrderTests(unittest.TestCase):
    SKK = {'skk-l', 'skk-jinmei', 'jawiki'}

    def order(self, key, rows):
        return jisyo.nasi_candidates(key, rows, self.SKK)

    def test_skk_l_first_then_cost_pri_defaults_and_adjustments(self):
        rows = [('幹事', 'sudachi-small', 9, 100, None), ('監事', 'sudachi-small', 1, 7403, None),
                ('感じ', 'sudachi-small', 2, 5548, None), ('完治', 'jmdict', 3, None, 'news1|nf22'),
                ('冠辞', 'jmdict', 4, None, None), ('丞治', 'sudachi-core', 5, 0, None), ('乾二', 'jmnedict', 6, None, 'spec1'),
                ('関自', 'jawiki', 7, None, None), ('患耳', 'jmed', 8, None, None), ('Kanji', 'sudachi-small', 9, 100, None),
                ('かんじ', 'sudachi-small', 10, 50, None), ('カンジ', 'sudachi-small', 11, 2148, None),
                ('幹事', 'skk-l', 21, None, None), ('漢字', 'skk-l', 20, None, None), ('漢字', 'jawiki', 1, None, None)]
        self.assertEqual(self.order('かんじ', rows),
                         ['漢字', '幹事', '感じ', '完治', '監事', 'カンジ', '冠辞', '丞治', '乾二', '関自', '患耳', 'Kanji', 'かんじ'])
        self.assertEqual(jisyo.pri_cost('ichi1|news1|nf01'), jisyo.NF_COST[0])
        self.assertEqual(jisyo.pri_cost('news2|nf48'), jisyo.NF_COST[-1])
        self.assertEqual([jisyo.pri_cost(p) for p in ['gai1', 'spec1|news2', 'ichi2']],
                         [jisyo.PRI_COST['1'], jisyo.PRI_COST['1'], jisyo.PRI_COST['2']])
        self.assertEqual(list(jisyo.NF_COST), sorted(jisyo.NF_COST))
        self.assertLess(max(jisyo.NF_COST), jisyo.PRI_COST['1'])
        self.assertLess(jisyo.PRI_COST['1'], jisyo.PRI_COST['2'])

    def test_katakana_zero_cost_and_skk_l_exceptions(self):
        coffee = [('珈琲', 'sudachi-small', 1, 5856, None), ('コーヒー', 'sudachi-small', 2, 1541, None),
                  ('珈琲', 'jmdict', 3, None, 'gai1|ichi1', 'コーヒー'), ('coffee', 'sudachi-small', 4, 2833, None)]
        # The JMdict tag of the katakana reading moves to that spelling, so it is not shifted.
        self.assertEqual(self.order('こーひー', coffee), ['コーヒー', '珈琲', 'coffee'])
        self.assertEqual(self.order('こーひー', [r[:5] for r in coffee]), ['珈琲', 'コーヒー', 'coffee'])
        self.assertEqual(jisyo.candidate_rows(coffee[2:3] + [('可否', 'jmdict', 5, None, None, 'かひ')]),
                         {'珈琲': [('jmdict', 3, None, 'gai1|ichi1')], '可否': [('jmdict', 5, None, None)]})
        self.assertEqual(self.order('おくらす', [('オクラス', 'sudachi-small', 1, 3888, None), ('遅らす', 'jmdict', 2, None, 'ichi1')]),
                         ['遅らす', 'オクラス'])
        self.assertEqual(self.order('たたく', [('たゝく', 'sudachi-small', 1, 0, None), ('叩く', 'sudachi-small', 2, 7329, None),
                                              ('敲く', 'jmdict', 3, None, None)]), ['叩く', '敲く', 'たゝく'])
        # Rule 1 wins over the adjustments: skk-l keeps its own order even for ASCII or the key itself.
        self.assertEqual(self.order('てすと', [('てすと', 'skk-l', 1, None, None), ('test', 'skk-l', 2, None, None),
                                              ('試験', 'sudachi-small', 3, 10, None)]), ['てすと', 'test', '試験'])
        self.assertEqual(self.order('cd', [('cd', 'jmdict', 1, None, None), ('CD', 'sudachi-small', 2, 5000, None)]), ['CD', 'cd'])

    def test_ties_are_deterministic(self):
        import random
        rows = [('乙', 'sudachi-small', 1, 7404, None), ('甲', 'sudachi-small', 2, 7404, None),
                ('丙', 'sudachi-small', 3, 7404, None), ('丙', 'jawiki', 4, None, None),
                ('名二', 'skk-jinmei', 8, None, None), ('名一', 'skk-jinmei', 9, None, None),
                ('地', 'jmnedict', 5, None, None), ('天', 'jmnedict', 3, None, None)]
        expected = ['丙', '乙', '甲', '名二', '名一', '地', '天']
        for seed in range(5):
            random.Random(seed).shuffle(rows)
            self.assertEqual(self.order('き', rows), expected)

    def test_export_orders_per_selected_sources(self):
        E = jisyo.Entry
        parsed = {'skk-l': [E('かんじ', '監事', ''), E('かんじ', '漢字', '')],
                  'sudachi-small': [E('かんじ', '漢字', '', cost=6125), E('かんじ', '感じ', '', cost=5548),
                                    E('かんじ', 'かんじ', '', cost=10), E('かか', '書か', '', scope='migemo', cost=1)],
                  'jmdict': [E('かんじ', '漢字', '', pri='ichi1|news1|nf07'), E('かんじ', '監事', '', pri='news2|nf26'),
                             E('かんじ', '冠辞', ''), E('かんじ', '幹事', '', pri='ichi1|news1|nf01')],
                  'jmnedict': [E('かんじ', '寛治', '', pri='spec1'), E('かんじ', 'かんじ', '')],
                  'jmed': [E('かんじ', '患児', ''), E('かんじ', '患耳', '')]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(jisyo, DB=root/'db.sqlite', ROOT=root, rows=lambda source, stats: iter(parsed[source['id']])):
                jisyo.ingest([dict(id=id, format='skk' if id == 'skk-l' else 'x', notice_files=[]) for id in parsed])
                with patch.object(jisyo, 'write_export', lambda output, records, *_: list(records)):
                    def nasi(ids):
                        return [s for kind, key, s in jisyo.export_skk_tsv(ids, root/'x') if key == 'かんじ']
                    self.assertEqual(nasi(None), ['監事', '漢字', '感じ', '幹事', '冠辞', '寛治', '患児', '患耳', 'かんじ'])
                    self.assertEqual(nasi(['jmdict', 'jmnedict']), ['幹事', '漢字', '監事', '冠辞', '寛治', 'かんじ'])
                    self.assertEqual(nasi(['jmed']), ['患児', '患耳'])
                    self.assertEqual(nasi(None), nasi(None))
                    self.assertEqual(jisyo.export(None, root/'m'), [('かか', '書か')] + [('かんじ', s) for s in sorted(
                        ['監事', '漢字', '感じ', '幹事', '冠辞', '寛治', '患児', '患耳', 'かんじ'])])


if __name__ == '__main__':
    unittest.main()
