# ライセンスの適用範囲

## 配布方針

本プロジェクトのコード・新規文書、および **all（全入り）とcoreの統合・編集辞書** は GPL-3.0-only で提供します。辞書については本プロジェクトが行った選択・正規化・制約適用・統合・重複排除を含む編集成果物に対する指定です。プログラムの出力だから自動的にGPLになる、あるいは形式変換だけで翻案になるという扱いではありません。

| 対象 | 適用する条件 |
| --- | --- |
| 自作コード・新規文書 | GPL-3.0-only（ルート LICENSE） |
| all / core の編集辞書、編集用TSV | GPL-3.0-only。原資料の帰属・告知も保持 |
| edrdg の辞書、編集用TSV | CC-BY-SA-4.0 |
| medical の辞書、編集用TSV | CC-BY-4.0 |
| 同梱した未変更の原データ・上流文書・依存コード | 各原ライセンスのまま。全体のGPL指定で原条件を削除しない |
| 出典対応表 | 各元データの帰属・条件を保持。統合した編集部分はGPL-3.0-only |

成果物アーカイブの `LICENSE` はそのprofileの辞書のライセンス本文です。対応ソースアーカイブのルート `LICENSE` は自作コードのGPLv3本文で、各辞書への適用は上表と `RELEASE.json` に記載します。上流のライセンス本文は `LICENSES/` と `UPSTREAM_NOTICES/`、依存ライブラリは `third_party/licenses/` と対応ソースの `vendor/` に保存します。

## 各原資料の扱い

- **SKK 8種**: 各ファイルのGPL-2.0-or-laterに基づき、統合成果物ではGPLv3を選択します。各辞書の著作権・既存の変更表示を含む先頭ヘッダーを保持します。
- **SudachiDict**: Apache-2.0、LEGAL全文（UniDicのBSD型条件、NEologd由来の表示など）を保持します。取得版にNOTICEが存在すればそれも取得・保存します。原CSVを改変せず同梱し、抽出・変換は `CHANGES.md` に表示します。
- **JMdict / JMnedict**: 原データのCC-BY-SA-4.0、EDRDG・James William BREEN等の著作権・免責・帰属を保持します。allではCCが認めるCC BY-SA 4.0→GPLv3の一方向互換を、統合・編集した翻案物のAdapter's Licenseとして採用する方針です。原資料までGPLに置き換えるものではありません。単なる形式変換や集合物に互換規定が自動適用されるとは扱いません。独立したedrdg版はCC-BY-SA-4.0です。
- **JMED-DICT mini**: CC-BY-4.0の出現形・出現形よみだけを利用し、原READMEを保持します。外部医療コードは含めません。対応ソースでは採用した語彙の編集可能なTSVを提供し、元ZIPのハッシュも記録します。
- **jawiki**: **配布作者Tokuhiro MatsunoのMIT指定を採用**します。その著作権付きMIT本文と、Wikipediaの軽微利用とする作者の説明を取得版READMEのまま保持します。WikipediaのCC条件を別途根拠にして再構成したデータではなく、記事・履歴単位の帰属を収集したと主張しません。
- **Rust依存**: 固定版のMITまたは選択ライセンスのMIT側を使用し、著作権付き原文とその他の既存告知を保持します。対応ソースにはCargo.lockとvendorを含めます。コンパイル済み生成ツール自体は配布しません。

## 帰属・変更・対応ソース

各配布版の `THIRD_PARTY_NOTICES.md` は、取得版の原文告知への索引を含みます。プロジェクト名だけを作者表示の代用にせず、`UPSTREAM_NOTICES/` に全文を同梱します。`CHANGES.md` に変更主体・生成日時・加工内容を記載します。SKKにも変更表示を入れ、compact/TSVには隣接manifestを添えます。

`RELEASE.json`、`SOURCES.md` とロックファイルが取得日・版・SHA-256・生成コードのGitコミットと未コミット状態を記録します。Gitコミットだけでは未コミットの生成コードを表せないため、対応ソースには実際のコードを収録し全ファイルのハッシュを付けます。編集可能なprofile別TSV、単語単位の出典対応、固定した入力、設定、生成スクリプト、依存ソース、`BUILD.md` を同じリリースの `dictionary-sources.tar.gz` として追加料金なしで提供します。最新に変わり続ける上流URLだけを対応ソースとはしません。

改変・再配布・商用利用は該当ライセンスの条件に従って行えます。独自の商用禁止・再配布禁止条件は追加しません。上流作者による推奨・公認を意味しません。元資料の免責条項も保持します。

## 根拠となる一次資料

- [GPLv3（第1・4〜6条を含む）](https://www.gnu.org/licenses/gpl-3.0.html)
- [CCの互換ライセンス一覧](https://creativecommons.org/compatible-licenses/)
- [CCのGPLv3互換・帰属に関する説明](https://wiki.creativecommons.org/wiki/ShareAlike_compatibility:_GPLv3)
- [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)
- [EDRDGの配布条件](https://www.edrdg.org/edrdg/licence.html)
