# myhugejisyo

一般語・新語・固有名詞・専門語の公開辞書を収集し、**RustMigemo compact dictionary** を生成します。読みを推測し直さず、元データが持つ読みと表記を使います。

## 使う

Python 3.10 以降（追加パッケージ不要）、Rust/Cargo（edition 2024 対応）が必要です。リポジトリのディレクトリで実行してください。

```sh
python3 jisyo.py fetch
python3 jisyo.py ingest
python3 jisyo.py build
```

生成物は `dist/migemo-compact-dict`。RustMigemo の `CompactDictionary::new(&bytes)` で読み込めます。利用側でも固定したRustMigemo版との互換性を確認してください。

```sh
cargo run --release --locked -- lookup dist/migemo-compact-dict けんさく
cargo run --release --locked -- query dist/migemo-compact-dict kensaku 検索
```

`lookup` はひらがな読みの完全一致、`query` はローマ字から正規表現を生成します。最後の引数を付けるとその文字列に一致することを検証し、不一致なら失敗します。正規表現のバイト数・生成時間・コンパイル時間も表示します。

## データ源

| ID | 収集元と役割 | 条件の概要 |
| --- | --- | --- |
| `skk-l` | [SKK公式L](https://github.com/skk-dev/dict)：一般語 | GPL-2.0-or-later |
| `skk-jinmei`, `skk-geo`, `skk-station`, `skk-propernoun` | 同公式：人名・地名・駅名・固有名詞 | 各ファイルの著作権・GPL告知 |
| `skk-law`, `skk-hukugougo`, `skk-jis2004` | 同公式：法律・複合語・互換漢字 | 各ファイルの著作権・GPL告知 |
| `sudachi-small`, `sudachi-core`, `sudachi-notcore` | [SudachiDict](https://github.com/WorksApplications/SudachiDict)：3ファイル全部でfull | Apache-2.0＋UniDic等のLEGAL |
| `jawiki` | [jawiki-kana-kanji-dict](https://github.com/tokuhirom/jawiki-kana-kanji-dict)：Wikipediaの新語・百科事典語彙 | 作者はMITと説明。Wikipedia抽出の判断をREADMEに記載 |
| `jmdict`, `jmnedict` | [EDRDG](https://www.edrdg.org/)：一般語・異表記・人名等 | [CC BY-SA 4.0と告知](https://www.edrdg.org/edrdg/licence.html) |
| `jmed` | [NAIST JMED-DICT mini](https://sociocom.naist.jp/download/jmed-dict-mini/)：医学・医薬品・身体部位 | CC BY 4.0。本実装は出現形・出現形よみのみ利用 |

NEologdの単独取り込みは未実装です（SudachiDictが含む部分は入ります）。インターネット上の全語を網羅するものではなく、新語の鮮度は各配布元の更新に依存します。

## 更新・再現

```sh
# 最新の公開版・日次スナップショットを選び直す
python3 jisyo.py fetch --update
python3 jisyo.py ingest
python3 jisyo.py build

# 現在固定している出典を確認する
python3 jisyo.py list
```

`sources.lock.json` に版、取得URL、取得日時、SHA-256、利用条件と告知ファイルのSHA-256を保存します。通常の `fetch` はロックを守り、保存済みファイルのハッシュを検査して再利用します。途中の取得失敗はソースごとのreceiptで再開できます。

JMdict等は同じURLが更新されます。古いスナップショットが手元になく、配布元も古いデータを提供しない場合、ロックと同一の再取得はできません。その場合は黙って最新版に差し替えず失敗します。完全再現には `data/raw/` とロックを一緒に保管してください。`--update` は明示的な版の切替です。

SudachiのCSVレイアウトは20260723形式を対象としています。将来版でURL・形式が変わった場合はエラーにして変換処理の確認を求めます。

## 出典を選んで生成

```sh
# SudachiDict-fullだけ
python3 jisyo.py build --sources sudachi-small sudachi-core sudachi-notcore --output dist/sudachi-full

# 医学辞書だけ
python3 jisyo.py build --sources jmed --output dist/medical

# 中間TSVだけ
python3 jisyo.py export --sources jawiki --output dist/jawiki.tsv
```

指定を省略すると取り込み済みの全出典を結合します。全入り版の統合・編集成果物はGPL-3.0-onlyとし、原資料の条件・帰属を保持します。適用範囲はLICENSING.mdを参照してください。出典別に分けた生成もできます。

## 保存先

- `data/raw/<source>/`：取得した元データ、告知、取得記録
- `data/lexicon.sqlite3`：検索用読み・元の読み・表記・出典・品詞・読みの由来を保存する共通マスタ。SKKの送りなし/送りあり（`kind`）、両形式・SKKのみ・RustMigemoのみの区別（`scope`）、出典内の出現順（`rank`）、Sudachiのコスト（`cost`）、JMdictの優先度タグ（`pri`）も保持
- `data/import-report.json`：出典ごとの取り込み数、重複排除後の組数、除外理由・件数、全体のユニーク数
- `dist/migemo-compact-dict.tsv`：UTF-8の「読み TAB 表記」。同じ組を重複排除し、安定ソート
- `dist/migemo-compact-dict`：検索エンジンに渡すバイナリ
- `dist/migemo-compact-dict.manifest.json`：生成物ハッシュ・出典・全件読み戻し検証結果
- `dist/migemo-compact-dict.tsv.notices/`：出典の告知。JMEDのREADME.pdfも保存
- `dist/SKK-JISYO.myhugejisyo.utf8.skk.tsv`：SKK出力の編集用中間ファイル。UTF-8の「種別（`ari`/`nasi`） TAB 見出し TAB 候補」で、SKK出力と同じ行順・候補順

元データ・SQLite・生成物はGit管理外です。`sources.lock.json` と `Cargo.lock` は再現用に管理します。大規模ビルドには数GBの空きメモリとディスク容量を用意してください。上流RustMigemo builderは全語彙をメモリに保持します。

## 変換規則と制限

- 読みだけをNFKC・小文字化・カタカナ→ひらがなに正規化します。検索キーから `・、。「」【】` を除去し、たとえば `アーガイル・チェック` を `あーがいるちぇっく` で検索できます。元の読みは `original_reading` に保持し、変換件数は `separator_joined_pairs` に記録します。表記の全角・半角・異体字を勝手に統一しません。
- RustMigemoの対応キーはASCII（U+0020–007E）、ひらがな（U+3041–3096）、長音符です。それ以外は件数を記録して除外。空読み、欠損の `*`、制御文字を含む表記も除外します。
- Sudachiでは表示用表記（列4）と対応する読み（列11）を採用します。Unicodeエスケープを復元し、原形への置換や読みの推測はしません。活用形（列10）が `*` でも `終止形` で始まる形でもない行（`かか→書か` などの活用形）はRustMigemo専用（`scope=migemo`）とし、SKK出力には入れません。同じ出典の原形・無活用の行にも同じ組があれば両形式に残します。
- JMdict/JMnedictの `re_restr` を守り、`re_nokanji` は漢字と結び付けません。読み・表記の無条件な総当たりはしません。
- SKKの注釈を落とし、数値テンプレート（`#` を含む見出し）の項目、Lisp式の候補、送り仮名ブロック `[…]` の候補を除外します。式は実行しません。Lisp式・送り仮名ブロックを含む行でも通常候補は候補単位で残しますが、SKK専用（`scope=skk`）としRustMigemo出力には加えません。
- SKKの送りあり項目（`おおk` や接辞の `>わたs` など）は全SKK形式入力から `kind=ari`・`scope=skk` として取り込み、SKK出力の送りあり部にだけ使います。送りなし部にある送り仮名形の見出しや、送りあり部にある送り仮名のない見出しは除外します。SKK送り仮名からの活用展開は未実装です。
- JMEDのリンク先コード・外部医療コードは取り込みません。XLSXはセル値のみ読み、式や外部リンクを実行しません。
- `confidence=upstream-provided` は「出典に読みがある」という意味です。手動校正済み・正読保証ではありません。元データの自動生成や誤読は残り得ます。
- 同一の読み・表記を複数ソースが提供した場合、SQLiteでは出典をすべて残し、バイナリでは一組にします。同じ出典内で同じ検索キー・表記にまとまる場合、最初の品詞・元読み・出現順、最小のコスト、最初の優先度タグを保持し、`scope` は広い方（異なれば `all`）にします。すべての原文は `data/raw/` に残ります。
- 上流RustMigemo builderをコミット固定で利用し、生成した辞書を読み戻して**全キーの全候補が入力TSVと等しいこと**を検査してから出力を置き換えます。
- 短い読みは非常に多くの候補に展開されます。語彙の自動間引きはしません。利用するアプリで短い入力時の遅延・正規表現の上限を評価してください。

SQLiteの出典追跡例：

```sql
SELECT e.reading, e.original_reading, e.surface, e.source, e.pos, s.metadata
FROM entries e JOIN sources s ON s.id = e.source
WHERE e.surface = '検索拡張生成';
```

独自データを加える場合は、まず `catalog()` に出典・利用条件を定義し、`rows()` に読み・表記・品詞を返す変換器を追加します。独自のUTF-8 TSVをRustの `build INPUT.tsv OUTPUT` に直接渡すこともできますが、その経路では出典台帳の自動作成はしません。

## 確認

```sh
python3 -m unittest discover -s tests -v
cargo test --locked
cargo fmt --check
```

読み制限、SKKの特殊構文と送りあり項目の往復・降順、送りなし候補の推定順（skk-l優先・コスト・優先度タグ・既定値・調整・決定性・出典選択）、候補単位の除外、Sudachi活用形のSKK除外、出現順・コスト・優先度タグの保存、JMdictの対応制限、Sudachiのエスケープ、XLSXの疎なセル、重複排除と出典保持、取り込み失敗時の既存DB保持、補助漢字を含むバイナリ往復を検証します。

## この環境での生成確認（2026-09-24）

`sources.lock.json` の15入力ファイルを使い、**2,619,763読み・3,826,995組**を統合しました。全入りバイナリは **48,415,103 bytes**、生成後の全組読み戻しが成功しています。取得版はSudachiDict v20260723、jawiki v2026.09.21.185756、JMdict/JMnedict 2026-09-23 UTC取得、JMED-DICT mini v1.1.2です。

このMacでの一回の実測では、TSV出力とコンパイルを含むbuild全体が約105秒、Rustの生成・全件検証が約79秒、最大RSSは約1.25 GBでした。性能の保証値ではありません。医学単独版 `dist/medical` も138,326組・2,924,096 bytesで全件検証済みです。

検索確認は `dist/verification.json` に保存しています。

- 成功：`kensaku`→検索、`oshikatsu`→推し活、`seisei`→生成AI、`kensakukakuchouseisei`→検索拡張生成、`shinkinkousoku`→心筋梗塞、`saikenjouto`→債権譲渡。
- 上流検索APIの制限：`seiseie-ai` と `a-gairuchekku` は目的語に一致しません。辞書には「せいせいえーあい→生成AI」「あーがいるちぇっく→アーガイル・チェック」が存在し、かな読みの検索は成功します。長音ローマ字入力への対応は利用エンジン側の課題として残ります。
- 短い入力：`a` は正規表現89,801 bytes、生成約396ms・コンパイル約40ms。`k` はRust regexの既定コンパイル上限10 MiBを超えて失敗しました。全入り辞書を組み込む際は、入力長や候補展開の方針を利用側で決める必要があります。

今回の辞書生成・全件復元は成功していますが、すべてのローマ字入力で上流検索APIが動くという検証結果ではありません。

## ライセンス

生成ツールのコードは **GPL-3.0-only**（[LICENSE](LICENSE)）。第三者の辞書データと依存ライブラリは元の条件を保持します。全入り版とcoreの統合・編集辞書はGPL-3.0-only、edrdg版はCC-BY-SA-4.0、medical版はCC-BY-4.0です。未変更の原データを一括して再許諾するものではありません。[適用範囲・互換性の調査](LICENSING.md) と [第三者告知](THIRD_PARTY_NOTICES.md) を参照してください。正式な本文は `LICENSES/`、著作権付き上流告知は `third_party/licenses/` に保存しています。

## UTF-8 SKK出力

```sh
python3 jisyo.py skk
# 出典を絞る場合
python3 jisyo.py skk --sources jmed --output dist/SKK-JISYO.medical.utf8
```

SKK出力は**送りあり・送りなしの両方を持つ補完辞書**です。UTF-8で、標準のSKK辞書と同じく `;; okuri-ari entries.` 部を見出しの降順、`;; okuri-nasi entries.` 部を見出しの昇順に並べ、同じ見出しの候補を一行に統合します。共通マスタから、出力と同じ順の中間ファイル `<output>.skk.tsv` を作ってからSKK形式へ変換します。送りありの候補はskk-lを先頭に、以降は出典の登録順、各出典内では元の出現順です。個人辞書・公式Lの後ろに追加してください。

送りなしの候補は、よく使われそうな順を**辞書内部の情報だけから推定**して並べます。コーパスでの実測頻度ではなく、追加のダウンロードや生成AIによる採点も行いません。同じ入力なら出力は常にバイト単位で一致します。

1. SKK-JISYO.L（skk-l）にある候補は、Lと完全に同じ順で先頭に置きます。
2. 残りの候補は、Sudachi small辞書のコスト尺度（小さいほど優先）にそろえた推定コストの昇順です。複数の出典にある候補は最小値を使います。
   - sudachi-smallのコスト。ただしコスト0（`ゐざる`・`たゝく` などの異表記）は信号なしとみなします。sudachi-core/notcoreのコストは概数の仮値が多いため使いません。
   - JMdictの優先度タグ（`pri`）。nf01〜nf48を6つずつ8段階に分けたもの、nfのないnews1/ichi1/spec1/gai1とnews2/ichi2/spec2/gai2を、両方を持つ語のsudachi-smallコストの中央値（単調になるよう平滑化）へ対応付けた固定表（`jisyo.py` の `NF_COST`・`PRI_COST`）で換算します。JMdictで読みがカタカナの語（外来語）の優先度タグは、当て字の表記（`珈琲`）ではなく同じ見出しのカタカナ表記（`コーヒー`）の候補に付け替えます。
   - Sudachiはカタカナ語に分割用の低いコストを付けるため、JMdictの優先度タグがないカタカナだけの候補は、そのコストに一定値（`KATAKANA_SHIFT`）を加えます。優先度タグのある `コーヒー` などはそのままです。
   - 信号のない候補は出典ごとの既定値で、信号のある一般的な候補の後ろに次の順で並びます：JMdict（タグなし）・SKK複合語/法律/JIS2004 → sudachi-small/core/notcore → SKK人名/地名/駅名/固有名詞 → JMnedict → jawiki → JMED。SKK形式の出典内では元の出現順を保ちます。
3. かな見出しの下のASCII英字だけの候補（skk-lにないもの）は、信号のある候補・既定値の候補の後ろへ回します。見出しと同じひらがなの候補は変換の意味がないため最後に置きます。
4. 推定コストが同じ場合は、収録する出典系統（skk・sudachi・jmdict・jmnedict・jawiki・jmed）が多い候補、SKK形式の出典内の順、文字コード順の順です。

`--sources` で出典を絞った場合も同じ規則で、選ばれた出典の信号だけを使います（edrdgはJMdictの優先度タグと既定値、medicalは文字コード順）。`python3 scripts/eval_order.py --skk-l data/raw/skk-l/source --db data/lexicon.sqlite3` で、skk-lを除いた推定コストがSKK-JISYO.Lの候補順とどの程度一致するか（辞書順との比較）、先頭候補がかな・英字だけの見出し数、代表的な見出しの先頭候補を確認できます。

SKKで構文になる `/ ; # [ ]` を含む候補、`(` で始まる候補、空白や特殊文字を含むキー、送りなし部で送り仮名キーと誤解される見出し、送りあり部で送り仮名の付かない見出しは除外し、件数を `.manifest.json` に記録します。`pairs` は中間TSVの行数、実際のSKK候補数は `skk.pairs`（送りあり `skk.ari_pairs`、送りなし `skk.nasi_pairs`）です。任意の文字列をLisp式へ変換して出力することはありません。Sudachiの活用形とSKKの送りあり・救済候補の扱いが異なるため、SKK出力の候補数はRustMigemo出力と一致しません。

## GitHub Releasesによる配布（Pages不要）

`.github/workflows/dictionaries.yml` は、`main`へのpush、毎日03:17 JST相当（GitHub側で遅延する場合あり）と手動実行に対応します。最新データの取得→取り込み→4種類の辞書の生成→RustMigemo全件読み戻し→ソースと告知の梱包→SHA-256確認を行います。すべて成功した場合だけ公開工程へ進みます。

| Profile | 内容 | RustMigemo asset | SKK asset |
| --- | --- | --- | --- |
| all | 全15入力。jawikiも含む | `migemo-compact-dict` | `SKK-JISYO.myhugejisyo.utf8` |
| core | SKK＋Sudachi-full | `migemo-compact-dict-core` | `SKK-JISYO.myhugejisyo-core.utf8` |
| edrdg | JMdict＋JMnedict | `migemo-compact-dict-edrdg` | `SKK-JISYO.myhugejisyo-edrdg.utf8` |
| medical | JMED-DICT mini | `migemo-compact-dict-medical` | `SKK-JISYO.myhugejisyo-medical.utf8` |

各profileの `*-dictionaries.tar.gz` には、両辞書・編集可能なTSV・出典manifest・今回取得した出典の告知・ライセンス本文を含めます。`dictionary-sources.tar.gz` には固定版の元データ、生成スクリプト、ロックファイルを含めます。ただしJMEDは外部医療コードを再配布しないよう、採用した読み・表記だけのTSVへ置換しています。アーカイブ内のロックはそのTSVのハッシュを持ち、原本のハッシュも `materialized_from` に記録します。これでオフライン取り込みと同一語彙の再生成が可能です。バイナリ単独URLから取得する場合も対応するbundleの告知・利用条件を確認してください。各配布版に `CHANGES.md`・`THIRD_PARTY_NOTICES.md`・`UPSTREAM_NOTICES/`・`RELEASE.json`・`SOURCES.md` を生成します。対応ソースにはさらに編集用TSV（RustMigemo用 `editable/<profile>.tsv` とSKK用 `editable/<profile>.skk.tsv`）、単語ごとの出典・原読み、Rust依存ソース（vendor）、`BUILD.md` とオフライン再生成スクリプトを含めます。ソースへの案内は同じReleaseタグに固定されます。実際のアーカイブ全ファイルとライセンス参照を検証し、対応ソースから全profileをオフライン再生成して元の辞書とハッシュが一致した場合だけ公開工程へ進みます。

ローカルで配布物を準備するには（ネットワーク公開は行いません）：

```sh
python3 jisyo.py fetch --update
python3 jisyo.py ingest
python3 scripts/release.py
# 実行済みディレクトリへの混入を防ぐため、次回は別の空ディレクトリを指定
python3 scripts/release.py --output dist/release-next --tag dict-example --modifier "gw31415/myhugejisyo maintainers"
```

### 有効化

1. `main`へpushすると、テストと辞書生成が自動実行され、辞書の全検証成功後にReleaseを公開します。
2. 手動実行も可能です。Actionsの「Build and release dictionaries」で `publish=false` を選ぶと公開せず生成物を確認できます。
3. `publish=true` で実行すると、全成果物をdraft Releaseへアップロードし、成功後に公開してLatestに指定します。
4. 毎日の自動公開には、Repository Settings → Secrets and variables → Actions → Variables に `PUBLISH_DICTIONARIES=true` を設定します。未設定でも毎日の生成・7日保持のActions artifact保存は行いますが、Releaseの公開はしません。

カスタムPATは不要で、標準の `GITHUB_TOKEN` を公開jobだけ `contents: write` にします。組織のポリシー等が書き込みを制限している場合はそちらの設定も必要です。PR用のCIはテストのみで、外部データの取得やRelease作成を行いません。ActionsはコミットSHAで固定しています。

### 最新版の固定URL

最初のRelease公開後、以下のURLで毎回Latestの同名assetを取得できます。Release公開前や生成失敗時には、まだ取得できない場合があります。

- 全入りRustMigemo: https://github.com/gw31415/myhugejisyo/releases/latest/download/migemo-compact-dict
- 全入りUTF-8 SKK: https://github.com/gw31415/myhugejisyo/releases/latest/download/SKK-JISYO.myhugejisyo.utf8
- 全入り告知付きbundle: https://github.com/gw31415/myhugejisyo/releases/latest/download/all-dictionaries.tar.gz
- 元データ・生成ソース: https://github.com/gw31415/myhugejisyo/releases/latest/download/dictionary-sources.tar.gz
- 検証用ハッシュ: https://github.com/gw31415/myhugejisyo/releases/latest/download/SHA256SUMS

GitHub公式: https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases

公開リポジトリのReleasesから、認証なしで辞書と対応ソースを取得できます。

固定URLを複数回取得する間にLatestが更新される可能性があります。厳密に同じ版で辞書とSHA256SUMSを照合する場合は、Release APIでタグを一度取得し、`releases/download/<tag>/<asset>` を使ってください。失敗したビルドは既存Latestを更新せず、アップロード途中で失敗したReleaseはdraftのまま残ります。
