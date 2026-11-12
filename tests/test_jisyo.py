# SPDX-License-Identifier: GPL-3.0-only
import io
import sqlite3
import json
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
        xml = '''<!DOCTYPE JMdict [<!ENTITY n "noun">]><JMdict><entry>
        <k_ele><keb>A</keb><ke_pri>news1</ke_pri><ke_pri>ichi1</ke_pri></k_ele><k_ele><keb>B</keb></k_ele>
        <r_ele><reb>a</reb><re_restr>A</re_restr><re_pri>spec1</re_pri><re_pri>news1</re_pri></r_ele>
        <r_ele><reb>b</reb><re_nokanji/></r_ele>
        <r_ele><reb>c</reb></r_ele><sense><pos>&n;</pos></sense>
        </entry><entry><r_ele><reb>d</reb></r_ele></entry>
        <entry><k_ele><keb>珈琲</keb></k_ele><r_ele><reb>コーヒー</reb><re_pri>gai1</re_pri></r_ele></entry></JMdict>'''.encode()
        entries = list(jisyo.parse_jmdict(io.BytesIO(xml), Counter()))
        self.assertEqual([e[:3] for e in entries],
                         [('a', 'A', 'noun'), ('b', 'b', 'noun'), ('c', 'A', 'noun'), ('c', 'B', 'noun'), ('d', 'd', ''), ('コーヒー', '珈琲', '')])
        # Kanji forms keep the ke_pri tags shared with the reading's re_pri; a katakana (loanword) reading keeps its re_pri.
        self.assertEqual([e.pri for e in entries], ['news1', None, None, None, None, 'gai1'])

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
                  E('え', '江', '', scope='skk'), E('え', '江', '', scope='skk'),
                  E('お', '尾', '', inf='iK|oK'), E('お', '尾', '', inf='iK|rK'), E('か', '可', '', inf='iK'), E('か', '可', '')]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(jisyo, DB=root/'db.sqlite', ROOT=root, rows=lambda source, stats: iter(parsed)):
                jisyo.ingest([dict(id='one')])
                import sqlite3
                with sqlite3.connect(root/'db.sqlite') as db:
                    self.assertEqual(db.execute('SELECT reading,kind,scope,rank,cost,pri,pos FROM entries ORDER BY reading').fetchall(),
                                     [('あ', 'nasi', 'all', 0, 10, 'news1', 'p1'), ('い', 'nasi', 'all', 1, None, None, ''),
                                      ('う', 'nasi', 'all', 4, None, 'ichi1', ''), ('え', 'nasi', 'skk', 6, None, None, ''),
                                      ('お', 'nasi', 'all', 8, None, None, ''), ('か', 'nasi', 'all', 10, None, None, '')])
                    # JMdict form tags keep only those every occurrence carries.
                    self.assertEqual(db.execute('SELECT reading,inf FROM entries WHERE inf IS NOT NULL').fetchall(), [('お', 'iK')])

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


def sudachi_row(surface, reading, conjugation, form, lemma, normalized, cost=5000, pos='動詞', connection='1'):
    return ','.join([surface, connection, connection, str(cost), surface, pos, '一般', '*', '*', conjugation, form, reading, normalized,
                     str(lemma), 'A'] + ['*'] * 4)


JMDICT_DTD = '''<!DOCTYPE JMdict [<!ENTITY v5k "Godan verb with 'ku' ending"><!ENTITY v1 "Ichidan verb"><!ENTITY exp "expressions">
<!ENTITY sK "search-only kanji form"><!ENTITY sk "search-only kana form"><!ENTITY iK "word containing irregular kanji usage">
<!ENTITY ok "out-dated or obsolete kana usage"><!ENTITY n "noun">]>'''


class OkuriTests(unittest.TestCase):
    def test_split_and_letters_per_conjugation(self):
        # (inflected surface, reading) -> SKK okuri-ari pairs; one row per 五段 行, 一段, adjectives, サ変, カ変 and 音便.
        cases = [('書か', 'カカ', ['かk']), ('泳ご', 'オヨゴ', ['およg']), ('話し', 'ハナシ', ['はなs']), ('立ち', 'タチ', ['たt']),
                 ('死な', 'シナ', ['しn']), ('飛べ', 'トベ', ['とb']), ('読も', 'ヨモ', ['よm']), ('取ろ', 'トロ', ['とr']),
                 ('買わ', 'カワ', ['かw']), ('買え', 'カエ', ['かe']), ('買お', 'カオ', ['かo']), ('買う', 'カウ', ['かu']),
                 ('書い', 'カイ', ['かi']), ('泳い', 'オヨイ', ['およi']), ('買っ', 'カッ', ['かt', 'かc']), ('取っ', 'トッ', ['とt', 'とc']),
                 ('読ん', 'ヨン', ['よn']), ('飛ん', 'トン', ['とn']), ('死ん', 'シン', ['しn']),
                 ('食べる', 'タベル', ['たb']), ('見る', 'ミル', ['みr']), ('見よ', 'ミヨ', ['みy']), ('感じ', 'カンジ', ['かんj', 'かんz']),
                 ('大きく', 'オオキク', ['おおk']), ('美しかっ', 'ウツクシカッ', ['うつくs']), ('高かろ', 'タカカロ', ['たかk']),
                 ('酸っぱい', 'スッパイ', ['すp']), ('愛さ', 'アイサ', ['あいs']), ('来い', 'コイ', ['こi']), ('来る', 'クル', ['くr']),
                 ('取り扱う', 'トリアツカウ', ['とりあつかu']), ('ぶっ飛ばす', 'ブットバス', []), ('ググる', 'ググル', []),
                 ('見', 'ミ', []), ('書く', 'ヨム', []), ('取り扱う', 'トルアツカウ', []), ('Ｇる', 'ジールル', []), ('ブチ切れ', 'ブチキレ', [])]
        for surface, reading, keys in cases:
            with self.subTest(surface=surface, reading=reading):
                self.assertEqual([k for k, _ in jisyo.okuri_keys(surface, reading)], keys)
        self.assertEqual(jisyo.okuri_keys('取り扱う', 'とりあつかう'), [('とりあつかu', '取り扱')])
        self.assertEqual(jisyo.okuri_split('大きく', 'おおきく'), ('おお', '大', 'きく'))

    def test_jmdict_conjugation_tables(self):
        cases = [('書く', 'かく', 'v5k', 'かk かi'), ('泳ぐ', 'およぐ', 'v5g', 'およg およi'), ('話す', 'はなす', 'v5s', 'はなs'),
                 ('立つ', 'たつ', 'v5t', 'たt たc'), ('死ぬ', 'しぬ', 'v5n', 'しn'), ('飛ぶ', 'とぶ', 'v5b', 'とb とn'),
                 ('読む', 'よむ', 'v5m', 'よm よn'), ('取る', 'とる', 'v5r', 'とr とt とc とn'), ('有る', 'ある', 'v5r-i', 'あr あt あc'),
                 ('買う', 'かう', 'v5u', 'かw かi かu かe かo かt かc'), ('問う', 'とう', 'v5u-s', 'とw とi とu とe とo'),
                 ('行く', 'いく', 'v5k-s', 'いk いt いc'), ('行く', 'ゆく', 'v5k-s', 'ゆk'), ('下さる', 'くださる', 'v5aru', 'くだs'),
                 ('食べる', 'たべる', 'v1', 'たb'), ('見る', 'みる', 'v1', 'みr みy みt みn みm'), ('来る', 'くる', 'vk', 'くr こi こy きt こn きm こr'),
                 ('愛する', 'あいする', 'vs-s', 'あいs'), ('感ずる', 'かんずる', 'vz', 'かんz かんj'), ('大きい', 'おおきい', 'adj-i', 'おおk'),
                 ('高い', 'たかい', 'adj-i', 'たかi たかk たかs'), ('良い', 'よい', 'adj-ix', 'よi よk よs'), ('良い', 'いい', 'adj-ix', 'いi'),
                 ('くる', 'くる', 'vk', ''), ('勉強', 'べんきょう', 'vs', ''), ('会す', 'かいす', 'vs-c', '')]
        for surface, reading, code, keys in cases:
            with self.subTest(surface=surface, code=code):
                self.assertEqual(' '.join(k for k, _ in jisyo.jmdict_okuri(surface, reading, code)), keys)
        self.assertEqual({s for _, s in jisyo.jmdict_okuri('来る', 'くる', 'vk')}, {'来'})

    def test_jmdict_form_tags_and_generation(self):
        xml = (JMDICT_DTD + '''<JMdict><entry><k_ele><keb>書く</keb><ke_pri>ichi1</ke_pri></k_ele><k_ele><keb>畫く</keb><ke_inf>&iK;</ke_inf></k_ele>
        <k_ele><keb>描く</keb></k_ele><k_ele><keb>書ク</keb><ke_inf>&sK;</ke_inf></k_ele>
        <r_ele><reb>かく</reb><re_pri>ichi1</re_pri></r_ele><r_ele><reb>かゝく</reb><re_inf>&sk;</re_inf></r_ele><sense><pos>&v5k;</pos></sense></entry>
        <entry><k_ele><keb>気が付く</keb></k_ele><r_ele><reb>きがつく</reb></r_ele><sense><pos>&exp;</pos><pos>&v5k;</pos></sense></entry>
        <entry><k_ele><keb>居る</keb></k_ele><r_ele><reb>ゐる</reb><re_inf>&ok;</re_inf></r_ele><sense><pos>&v1;</pos></sense></entry>
        </JMdict>''').encode()
        stats = Counter()
        entries = list(jisyo.parse_jmdict(io.BytesIO(xml), stats))
        nasi = {(e.reading, e.surface): (e.scope, e.inf, e.pri) for e in entries if e.kind == 'nasi'}
        self.assertEqual(nasi[('かく', '書く')], ('all', None, 'ichi1'))
        self.assertEqual(nasi[('かく', '畫く')], ('all', 'iK', None))
        # Search-only kanji (sK) and kana (sk) forms stay for RustMigemo only.
        self.assertEqual(nasi[('かく', '書ク')][0], 'migemo')
        self.assertEqual({s for (r, s), v in nasi.items() if r == 'かゝく'}, {'書く', '畫く', '描く', '書ク'})
        self.assertTrue(all(v[0] == 'migemo' for (r, _), v in nasi.items() if r == 'かゝく'))
        self.assertEqual(nasi[('ゐる', '居る')], ('all', 'ok', None))
        self.assertEqual(stats['jmdict_search_only_pair'], 5)
        ari = [(e.reading, e.surface, e.pos, e.scope, e.confidence) for e in entries if e.kind == 'ari']
        # Only the headword (or a form with its own priority) generates; irregular, search-only, expression entries do not.
        self.assertEqual(ari, [('かk', '書', 'okuri-rule|v5k|かく|書く', 'skk', 'rule-generated'),
                               ('かi', '書', 'okuri-rule|v5k|かく|書く', 'skk', 'rule-generated')])

    def test_sudachi_lemmas_need_canonical_dictionary_form(self):
        rows = [sudachi_row('書か', 'カカ', '五段-カ行', '未然形-一般', 10, '書く', 6000),
                sudachi_row('書い', 'カイ', '五段-カ行', '連用形-イ音便', 10, '書く', 5000),
                sudachi_row('書く', 'カク', '五段-カ行', '終止形-一般', 10, '書く', 4000),
                # Okurigana-omitted spelling with its own lemma: normalized form differs, skipped.
                sudachi_row('書', 'カク', '五段-カ行', '終止形-一般', 11, '書く'),
                sudachi_row('止どめる', 'トドメル', '下一段-マ行', '終止形-一般', 20, '止める'),
                sudachi_row('止どめ', 'トドメ', '下一段-マ行', '連用形-一般', 20, '止める'),
                # A stem reading that differs from the dictionary form's (大っきい) is skipped; カ変 may change it.
                sudachi_row('大きい', 'オオキイ', '形容詞', '終止形-一般', 30, '大きい', 3000, '形容詞'),
                sudachi_row('大きく', 'オオキク', '形容詞', '連用形-一般', 30, '大きい', 3000, '形容詞'),
                sudachi_row('大っきく', 'オッキク', '形容詞', '連用形-一般', 30, '大きい', 3000, '形容詞'),
                sudachi_row('高う', 'タコウ', '形容詞', '連用形-ウ音便', 31, '高い', 3000, '形容詞'),
                sudachi_row('高い', 'タカイ', '形容詞', '終止形-一般', 31, '高い', 3000, '形容詞'),
                sudachi_row('来る', 'クル', 'カ行変格', '終止形-一般', 40, '来る', 4500),
                sudachi_row('来い', 'コイ', 'カ行変格', '命令形', 40, '来る', 4500),
                sudachi_row('ググる', 'ググル', '五段-ラ行', '終止形-一般', 50, 'ググる'),
                sudachi_row('愛する', 'アイスル', 'サ行変格', '終止形-一般', 60, '愛する'),
                # Connection id -1 rows (cost 0) are split-only variants, not words: 散る read アカル, a stray 書っ.
                sudachi_row('散る', 'アカル', '五段-ラ行', '終止形-一般', 70, '散る', 0, connection='-1'),
                sudachi_row('散ら', 'アカラ', '五段-ラ行', '未然形-一般', 70, '散る', 0, connection='-1'),
                sudachi_row('書っ', 'カッ', '五段-カ行', '連用形-促音便', 10, '書く', 0, connection='-1')]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'sudachi').mkdir()
            path = root/'sudachi/source'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('small_lex.csv', '\n'.join(rows) + '\n')
            source = {'id': 'sudachi', 'format': 'sudachi', 'sha256': jisyo.digest(path)}
            stats = Counter()
            with patch.object(jisyo, 'CACHE', root):
                ari = [(e.reading, e.surface, e.cost, e.pos) for e in jisyo.rows(source, stats) if e.kind == 'ari']
        self.assertEqual(ari, [('おおk', '大', 3000, 'okuri-rule|形容詞|おおきい|大きい'), ('かi', '書', 4000, 'okuri-rule|五段-カ行|かく|書く'),
                               ('かk', '書', 4000, 'okuri-rule|五段-カ行|かく|書く'), ('くr', '来', 4500, 'okuri-rule|カ行変格|くる|来る'),
                               ('こi', '来', 4500, 'okuri-rule|カ行変格|くる|来る'), ('たかi', '高', 3000, 'okuri-rule|形容詞|たかい|高い')])
        self.assertEqual(stats['okuri_skipped_lemma'], 1)
        self.assertEqual(stats['okuri_skipped_form'], 2)  # 大っきく, 高う (たこう)

    def test_ari_order_keeps_upstream_prefix_then_generated_by_cost(self):
        order = {'skk-l': (False, 0), 'skk-geo': (True, 1), 'sudachi-small': (True, 2), 'jmdict': (True, 3)}
        G, U = jisyo.GENERATED, 'upstream-provided'
        rows = [('描', 'skk-l', 2, None, None, None, U, None), ('書', 'skk-l', 1, None, None, None, U, None),
                ('欠', 'skk-geo', 0, None, None, None, U, None), ('書', 'sudachi-small', 9, 4000, None, None, G, ('かく', '書く')),
                ('掻', 'sudachi-small', 5, 9000, None, None, G, ('かく', '掻く')), ('舁', 'jmdict', 3, None, None, None, G, ('かく', '舁く')),
                ('斯', 'jmdict', 4, None, 'news1|nf10', None, G, ('かく', '斯く')), ('畫', 'jmdict', 1, None, 'ichi1', 'iK', G, ('かく', '畫く'))]
        # skk-l in L order first, other SKK sources next, then generated by cost; irregular forms after source defaults.
        self.assertEqual(jisyo.ari_candidates('かk', rows, order), ['書', '描', '欠', '斯', '掻', '舁', '畫'])
        # Mozc costs of the dictionary forms decide when Mozc is selected; absent forms are shifted.
        self.assertEqual(jisyo.ari_candidates('かk', rows, order, {('かく', '掻く'): 3000}), ['書', '描', '欠', '掻', '斯', '舁', '畫'])
        import random
        for seed in range(5):
            random.Random(seed).shuffle(rows)
            self.assertEqual(jisyo.ari_candidates('かk', rows, order), ['書', '描', '欠', '斯', '掻', '舁', '畫'])

    def test_irregular_forms_demoted_in_okuri_nasi(self):
        def order(rows):
            return jisyo.nasi_candidates('かく', rows, {'skk-l'})
        rows = [('書く', 'jmdict', 1, None, 'ichi1', 'かく', None), ('畫く', 'jmdict', 2, None, 'ichi1', 'かく', 'iK'),
                ('掻く', 'sudachi-small', 3, 9000, None), ('舁く', 'jmnedict', 4, None, None), ('角', 'jmed', 5, None, None)]
        self.assertEqual(order(rows), ['書く', '掻く', '舁く', '角', '畫く'])
        # A spelling some JMdict row lists as regular is not demoted; rule 1 (skk-l order) still wins.
        self.assertEqual(order(rows + [('畫く', 'jmdict', 6, None, None, 'かく', None)])[:2], ['書く', '畫く'])
        self.assertEqual(order(rows + [('畫く', 'skk-l', 0, None, None)])[0], '畫く')
        self.assertTrue(jisyo.irregular([('jmdict', 0, None, None, True), ('sudachi-small', 0, 1, None, False)]))
        self.assertFalse(jisyo.irregular([('sudachi-small', 0, 1, None, False)]))

    def test_profiles_generate_by_source_and_keep_l_prefix(self):
        E, G = jisyo.Entry, jisyo.GENERATED
        parsed = {'skk-l': [E('かk', '書', '', 'ari', 'skk'), E('かk', '描', '', 'ari', 'skk'), E('かく', '書く', '')],
                  'sudachi-small': [E('かく', '書く', '', cost=4000), E('かk', '掻', 'okuri-rule|五段-カ行|かく|掻く', 'ari', 'skk', 9000, confidence=G),
                                    E('かk', '書', 'okuri-rule|五段-カ行|かく|書く', 'ari', 'skk', 4000, confidence=G)],
                  'jmdict': [E('かく', '書く', ''), E('かく', '書ク', '', scope='migemo', inf='sK'),
                             E('かk', '舁', 'okuri-rule|v5k|かく|舁く', 'ari', 'skk', confidence=G)],
                  'jmed': [E('かく', '核', '')]}
        profiles = {'core': ['skk-l', 'sudachi-small'], 'edrdg': ['jmdict'], 'medical': ['jmed'], 'all': list(parsed)}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(jisyo, DB=root/'db.sqlite', ROOT=root, rows=lambda source, stats: iter(parsed[source['id']])):
                jisyo.ingest([dict(id=id, format='skk' if id == 'skk-l' else 'x', notice_files=[]) for id in parsed])
                report = json.loads((root/'data/import-report.json').read_text())
                self.assertEqual(report['sudachi-small']['okuri_generated_unique_pairs'], 2)
                self.assertEqual(report['totals']['okuri_ari_generated_only_pairs'], 2)
                with patch.object(jisyo, 'write_export', lambda output, records, *rest: (list(records), rest[-1])):
                    # The search-only form stays in RustMigemo; generated okuri-ari pairs never reach it.
                    self.assertEqual(jisyo.export(None, root/'m.tsv')[0], [('かく', '書く'), ('かく', '書ク'), ('かく', '核')])
                    def ari(profile):
                        records, extra = jisyo.export_skk_tsv(profiles[profile], root/'x')
                        return [s for kind, key, s in records if kind == 'ari'], extra['okuri_ari'], records
                    self.assertEqual(ari('core')[0], ['書', '描', '掻'])
                    self.assertEqual(ari('edrdg')[0], ['舁'])
                    self.assertEqual(ari('medical')[0], [])
                    candidates, origin, records = ari('all')
                    self.assertEqual(candidates, ['書', '描', '掻', '舁'])
                    self.assertEqual(origin, {'candidates_jmdict': 1, 'candidates_skk': 2, 'candidates_sudachi': 1, 'keys': 1, 'keys_with_upstream': 1})
                    self.assertNotIn(('nasi', 'かく', '書ク'), records)
            provenance = sqlite3.connect(root/'db.sqlite').execute("SELECT confidence FROM entries WHERE reading='かk' AND source='jmdict'").fetchone()
            self.assertEqual(provenance, (G,))


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
        # カンジ is only a katakana spelling of the key (no JMdict/Mozc evidence): after the other words, before ASCII.
        self.assertEqual(self.order('かんじ', rows),
                         ['漢字', '幹事', '感じ', '完治', '監事', '冠辞', '丞治', '乾二', '関自', '患耳', 'カンジ', 'Kanji', 'かんじ'])
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
                         {'珈琲': [('jmdict', 3, None, 'gai1|ichi1', False)], '可否': [('jmdict', 5, None, None, False)]})
        self.assertEqual(self.order('おくらす', [('オクラス', 'sudachi-small', 1, 3888, None), ('遅らす', 'jmdict', 2, None, 'ichi1')]),
                         ['遅らす', 'オクラス'])
        self.assertEqual(self.order('ぼむ', [('ボム', 'sudachi-small', 1, 3000, None), ('爆弾', 'jawiki', 2, None, None)]), ['爆弾', 'ボム'])
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


class MozcTests(unittest.TestCase):
    MOZC = ('ばんでん\t1920\t1920\t6826\t番田\nばんでん\t1924\t1924\t6704\t番田\nばんでん\t1841\t1841\t8159\tバンデン\n'
            'てれび\t1851\t1851\t3607\tテレビ\nいく\t1370\t1370\t0\tイク\nすうぃーつ\t1851\t1851\t5000\tスウィーツ\n'
            'あーがいる・ちぇっく\t1851\t1851\t7000\tアーガイル・チェック\nあばんしあ\t1841\t1841\t9218\tアヴァンシア\tSPELLING_CORRECTION\n'
            'ぎょうざ\t1851\t1851\t5100\t餃子\n')

    def build(self, root, sources):
        lock = []
        for id, format, text in sources:
            folder = root/'raw'/id
            folder.mkdir(parents=True)
            (folder/'source').write_text(text)
            lock.append(dict(id=id, format=format, encoding='utf-8', sha256=jisyo.digest(folder/'source'), notice_files=[]))
        jisyo.ingest(lock)

    def test_parser_min_cost_per_pair_and_normalization(self):
        from contextlib import closing
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(jisyo, DB=root/'db.sqlite', CACHE=root/'raw', ROOT=root):
                self.build(root, [('skk-l', 'skk', 'ばんでん /番田/\n'), ('mozc-00', 'mozc', self.MOZC)])
                with closing(sqlite3.connect(root/'db.sqlite')) as db:
                    self.assertEqual(db.execute('SELECT reading,surface,cost FROM costs ORDER BY reading,surface').fetchall(),
                                     [('あーがいるちぇっく', 'アーガイル・チェック', 7000), ('いく', 'イク', 0), ('ぎょうざ', '餃子', 5100),
                                      ('すうぃーつ', 'スウィーツ', 5000), ('てれび', 'テレビ', 3607),
                                      ('ばんでん', 'バンデン', 8159), ('ばんでん', '番田', 6704)])
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM entries WHERE source='mozc-00'").fetchone()[0], 0)
                report = json.loads((root/'data/import-report.json').read_text())
                self.assertEqual(report['mozc-00']['mozc_flagged_row'], 1)
                self.assertEqual(report['totals']['ranking_pairs'], 7)
                (root/'raw/mozc-00/source').write_text('あ\t1\t1\tx\t亜\n')
                with self.assertRaisesRegex(ValueError, 'Mozc'):
                    list(jisyo.rows(dict(id='mozc-00', format='mozc', sha256=jisyo.digest(root/'raw/mozc-00/source')), Counter()))

    def test_ranking_only_and_order_with_mozc_present_or_absent(self):
        skk = 'ばんでん /番田/バンデン/\nてれび /テレビ/\n'
        sudachi = [jisyo.Entry('ばんでん', 'バンデン', '', cost=2000), jisyo.Entry('ばんでん', '晩田', '', cost=7000),
                   jisyo.Entry('とくず', 'トクズ', '', cost=2000), jisyo.Entry('とくず', '土葛', '', cost=9000),
                   jisyo.Entry('ぎょうざ', '餃子', '', cost=9000), jisyo.Entry('ぎょうざ', '行座', '', cost=8000)]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rows = jisyo.rows
            def fake_rows(source, stats):
                return iter(sudachi) if source['id'] == 'sudachi-small' else rows(source, stats)
            with patch.multiple(jisyo, DB=root/'db.sqlite', CACHE=root/'raw', ROOT=root, rows=fake_rows):
                self.build(root, [('jawiki', 'skk', skk), ('sudachi-small', 'x', ''), ('mozc-00', 'mozc', self.MOZC)])
                with patch.object(jisyo, 'write_export', lambda output, records, *_: list(records)):
                    without = ['jawiki', 'sudachi-small']
                    migemo = jisyo.export(None, root/'m')
                    self.assertEqual(migemo, jisyo.export(without, root/'m'))
                    self.assertNotIn(('すうぃーつ', 'スウィーツ'), migemo)
                    def nasi(ids):
                        result = {}
                        for _, key, surface in jisyo.export_skk_tsv(ids, root/'x'):
                            result.setdefault(key, []).append(surface)
                        return result
                    present, absent = nasi(None), nasi(without)
                    self.assertEqual({k: sorted(v) for k, v in present.items()}, {k: sorted(v) for k, v in absent.items()})
                    # Without Mozc: Sudachi costs; unevidenced katakana spellings of the key after the words.
                    self.assertEqual(absent['ばんでん'], ['晩田', '番田', 'バンデン'])
                    self.assertEqual(absent['ぎょうざ'], ['行座', '餃子'])
                    # With Mozc: its cost decides and is evidence for バンデン; words it lacks are shifted.
                    self.assertEqual(present['ばんでん'], ['番田', '晩田', 'バンデン'])
                    self.assertEqual(present['ぎょうざ'], ['餃子', '行座'])
                    self.assertEqual(present['とくず'], absent['とくず'])
                    self.assertEqual(present['とくず'], ['土葛', 'トクズ'])
                    self.assertEqual(nasi(['mozc-00']), {})
                with self.assertRaisesRegex(ValueError, 'No pairs'):
                    jisyo.export(['mozc-00'], root/'mozc-only.tsv')

    def test_katakana_rule_and_mozc_scale(self):
        def order(key, rows, mozc=None):
            return jisyo.nasi_candidates(key, rows, {'skk-l'}, mozc)
        tokuzu = [('トクズ', 'sudachi-small', 1, 2000, None), ('土葛', 'skk-geo', 2, None, None), ('tokuzu', 'jawiki', 3, None, None)]
        self.assertEqual(order('とくず', tokuzu), ['土葛', 'トクズ', 'tokuzu'])
        self.assertEqual(order('とくず', tokuzu, {}), ['土葛', 'トクズ', 'tokuzu'])
        self.assertEqual(order('とくず', tokuzu, {'トクズ': 5000}), ['トクズ', '土葛', 'tokuzu'])
        # Near-zero kana costs are POS artifacts, not evidence (いく→イク 0 in Mozc).
        self.assertEqual(order('とくず', tokuzu, {'トクズ': 0}), ['土葛', 'トクズ', 'tokuzu'])
        self.assertEqual(order('とくず', tokuzu + [('トクズ', 'skk-l', 0, None, None)]), ['トクズ', '土葛', 'tokuzu'])
        self.assertEqual(order('とくず', tokuzu + [('トクズ', 'jmdict', 4, None, None)]), ['トクズ', '土葛', 'tokuzu'])
        tv = [('テレビ', 'sudachi-small', 1, 1500, None), ('電視', 'jmnedict', 2, None, None), ('てれび', 'jawiki', 3, None, None)]
        self.assertEqual(order('てれび', tv, {'テレビ': 3607}), ['テレビ', '電視', 'てれび'])
        self.assertEqual(order('てれび', tv + [('テレビ', 'jmdict', 4, None, 'ichi1')]), ['テレビ', '電視', 'てれび'])
        # Kanji surfaces may use any Mozc cost; candidates come only from rows.
        self.assertEqual(order('かく', [('核', 'sudachi-small', 1, 5000, None), ('各', 'sudachi-small', 2, 9000, None)],
                               {'各': 0, '書く': 1451}), ['各', '核'])
        knots = jisyo.MOZC_KNOTS
        self.assertEqual([x for x, _ in knots], sorted({x for x, _ in knots}))
        self.assertEqual([y for _, y in knots], sorted({y for _, y in knots}))
        self.assertEqual(jisyo.mozc_cost(0), knots[0][1])
        self.assertEqual(jisyo.mozc_cost(18318), knots[-1][1])
        self.assertEqual(jisyo.mozc_cost(knots[3][0]), knots[3][1])
        self.assertLess(jisyo.mozc_cost(knots[3][0]), jisyo.mozc_cost(knots[3][0] + 1))

    def test_join_costs_merges_sorted_streams(self):
        groups = [('あ', 1), ('い', 2), ('う', 3), ('お', 4)]
        costs = [('あ', {'亜': 1}), ('いい', {'良い': 2}), ('う', {'有': 3}), ('え', {'絵': 4})]
        self.assertEqual(list(jisyo.join_costs(groups, costs)),
                         [('あ', 1, {'亜': 1}), ('い', 2, {}), ('う', 3, {'有': 3}), ('お', 4, {})])


if __name__ == '__main__':
    unittest.main()
