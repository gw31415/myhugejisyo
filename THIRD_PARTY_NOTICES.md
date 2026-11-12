# Third-party notices

このファイルは原ライセンスを置き換えません。適用範囲は `LICENSING.md` を参照してください。

## 辞書

- **SKK dictionaries** — SKK Development Teamと各ファイルに記載された著作者。GPL-2.0-or-later。採用した8ファイルの全先頭ヘッダーを `third_party/licenses/skk-*/source-header.txt` に保持。https://github.com/skk-dev/dict
- **SudachiDict** — Works Applications Co., Ltd.; UniDic Consortium; Toshinori Satoほか。Apache-2.0と同梱条件。`third_party/licenses/sudachi-*/notice-0.txt`（Apache本文）、`notice-1.txt`（LEGAL、著作権、UniDic条件、NEologdの全告知）。https://github.com/WorksApplications/SudachiDict
- **jawiki-kana-kanji-dict** — Copyright © 2020 Tokuhiro Matsuno。配布者は生成辞書をMITと指定。Wikipedia日本語版の貢献者に帰属し、軽微利用に関する配布者の説明を含む原README全文を `third_party/licenses/jawiki/notice-0.txt` に保持。https://github.com/tokuhirom/jawiki-kana-kanji-dict
- **JMdict / JMnedict** — Copyright James William BREEN and the Electronic Dictionary Research and Development Group。CC BY-SA 4.0。本プロジェクトは両辞書の日本語の読み・表記を利用しています。EDRDGによる推奨・承認を意味しません。`third_party/licenses/jmdict/notice-0.txt`、`jmnedict/notice-0.txt` に公式の配布条件を保持。https://www.edrdg.org/ ／ https://www.edrdg.org/edrdg/licence.html
- **Mozc OSS辞書**（google/mozc `src/data/dictionary_oss/dictionary00.txt`〜`09.txt`、`mozc-00`〜`mozc-09`）— Copyright 2010-2018, Google Inc.（BSD-3-Clause）。辞書データには「Copyright 2000, 2001, 2002, 2003 Nara Institute of Science and Technology. All Rights Reserved.」のIPAdic許諾文と免責、ICOT Free Softwareの条件（NO WARRANTY）、沖縄辞書の「Public Domain Data」表示が付きます。単語コストをSKK送りなし候補の並べ替えだけに使い、読み・表記を出力の語彙に加えていません。固定コミットのLICENSE全文を `third_party/licenses/mozc/notice-0.txt`、IPAdic・ICOT・沖縄辞書の条件を含む辞書README全文を `third_party/licenses/mozc/notice-1.txt` に保持（10ファイル共通）。Googleによる推奨・承認を意味しません。https://github.com/google/mozc
- **JMED-DICT mini v1.1.2** — 奈良先端科学技術大学院大学 ソーシャル・コンピューティング研究室、JMED-DICT作業班。CC BY 4.0。出現形・出現形よみのみを利用。`third_party/licenses/jmed/README.pdf` と `notice-0.txt` に原文を保持。https://sociocom.naist.jp/download/jmed-dict-mini/

取得版とハッシュは `sources.lock.json`、加工内容は `LICENSING.md`、除外・重複排除の件数は `data/import-report.json` を参照してください。各生成物のCHANGES.mdと付属manifestに、その版の変更主体・日時・加工内容を記録します。

## Rust依存（Cargo.lockの固定版）

すべてMITの条件で使用します。選択可能な別ライセンスも上流ファイルのまま保存しています。

| Component | Version | 保存先 |
| --- | --- | --- |
| rustmigemo | 0.1.7-dev.0 / 121101dfcfd9465c998ba53fabe43773b14bf868 | `third_party/licenses/rustmigemo/` |
| regex | 1.13.1 | `third_party/licenses/regex/` |
| regex-automata | 0.4.18 | `third_party/licenses/regex-automata/` |
| regex-syntax | 0.8.11 | `third_party/licenses/regex-syntax/` |
| aho-corasick | 1.1.5 | `third_party/licenses/aho-corasick/` |
| memchr | 2.8.3 | `third_party/licenses/memchr/` |
| byteorder | 1.5.0 | `third_party/licenses/byteorder/` |

各ディレクトリのLICENSE/COPYINGには著作権表示と免責条項を含む原文を保持しています。rustmigemoのTHIRDPARTY_LICENSESは上流の告知の保存であり、その記載にある任意機能の全依存を本ツールが使用しているという意味ではありません。依存更新時はCargo.lockと告知の版を合わせて更新してください。
