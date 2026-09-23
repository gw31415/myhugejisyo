# SPDX-License-Identifier: GPL-3.0-only
"""Release attribution, corresponding source, and archive integrity helpers."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.parse import quote

LICENSES = {'all': 'GPL-3.0-only', 'core': 'GPL-3.0-only',
            'edrdg': 'CC-BY-SA-4.0', 'medical': 'CC-BY-4.0'}
MODIFICATIONS = '''読みをNFKC・小文字・ひらがなへ正規化し、検索キーの区切り記号を除去しました。
読みと表記を抽出し、JMdictの対応制限を適用しました。SKKの送りあり・注釈・特殊構文、
未対応文字・空表記等を除外し、出典間の同じ読み・表記を統合・重複排除しました。
元の表記を保持し、編集用TSV、RustMigemo compact、UTF-8 SKKへ出力しました。
SKKでは安全に表せないキー・候補をさらに除外し、候補は辞書順です。
JMEDは出現形・出現形よみだけを抽出し、外部医療コードを除外しました。
同梱した原資料と上流告知は変更していません（JMED入力のみ語彙TSVへ置換）。
変更は本プロジェクトによるもので、上流作者による変更・推奨・公認ではありません。'''


def release_identity(root, repository, tag, modifier, generated_at):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Expected GitHub owner/repository')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', tag):
        raise ValueError('Release tag must contain only letters, numbers, dot, underscore, hyphen')
    if not modifier.strip() or any(c in modifier for c in '\r\n'):
        raise ValueError('Modifier must be a nonempty single line')
    def git(*args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    return dict(schema_version=2, repository=repository, tag=tag, modifier=modifier,
                generated_at=generated_at, git_commit=git('rev-parse', 'HEAD'),
                git_dirty=bool(git('status', '--porcelain', '--untracked-files=normal')),
                corresponding_source_url=f'https://github.com/{repository}/releases/download/{quote(tag)}/dictionary-sources.tar.gz',
                publication_status='Prepared assets; URL becomes available when this exact release is published',
                toolchain=dict(python=subprocess.check_output(['python3', '--version'], text=True).strip(),
                               rust=subprocess.check_output(['rustc', '--version'], text=True).strip(),
                               cargo=subprocess.check_output(['cargo', '--version'], text=True).strip()))


def skk_headers(info, profile):
    return [f"License: {LICENSES[profile]}; original attribution/conditions retained in accompanying bundle.",
            f"Modified by {info['modifier']} at {info['generated_at']}; release {info['tag']}; profile {profile}.",
            'Changes: extraction, normalization, filtering, merging, deduplication, format conversion; see CHANGES.md.',
            f"Corresponding source: {info['corresponding_source_url']}"]


def write_documents(stage, root, info, sources):
    (stage/'RELEASE.json').write_text(json.dumps(info, ensure_ascii=False, indent=2)+'\n')
    (stage/'CHANGES.md').write_text(f"# この配布版の変更表示\n\n変更主体: {info['modifier']}\n\n生成日時: {info['generated_at']}\n\n版: `{info['tag']}`\n\n{MODIFICATIONS}\n\n各成果物の隣接manifestに除外件数と収録元を記録します。入力別の件数は対応ソース内の `data/import-report.json`、単語ごとの出典と原読みは `provenance.tsv` を参照してください。\n")
    text = '# 取得版と対応ソース\n\n'
    text += f"[この版の対応ソース一式]({info['corresponding_source_url']})（追加料金なし）。生成時点では未公開です。\n\n"
    text += f"生成: {info['generated_at']}\n\nGit: `{info['git_commit']}`; 未コミット変更: `{info['git_dirty']}`。実際のコードはソースアーカイブと CONTENTS.sha256.json で固定します。\n\n"
    for source in sources:
        text += (f"## {source['id']}\n\n版: `{source['version']}`\n\n取得: {source['fetched_at']}\n\n"
                 f"出典: {source['url']}\n\nSHA-256: `{source['sha256']}`\n\n条件: {source['license']}\n\n"
                 f"原文告知・著作者・免責・既存変更表示: `UPSTREAM_NOTICES/{source['id']}/`。SKKの原著作者は source-header.txt に全文を保存しています。\n\n")
    (stage/'SOURCES.md').write_text(text)
    notices = (root/'THIRD_PARTY_NOTICES.md').read_text()
    # Avoid stale original snapshot paths in release-specific attribution.
    for source in sources:
        notices = notices.replace(f"third_party/licenses/{source['id']}/", f"UPSTREAM_NOTICES/{source['id']}/")
    notices = notices.replace('third_party/licenses/skk-*/', 'UPSTREAM_NOTICES/skk-*/')
    notices = notices.replace('third_party/licenses/sudachi-*/', 'UPSTREAM_NOTICES/sudachi-*/')
    notices += '\n## このリリースの原文告知\n\nこの版の帰属・既存告知は次の全ファイルを含めたものです。固定版の一般説明と相違がある場合も、原文を省略しません。\n\n'
    for path in sorted((stage/'UPSTREAM_NOTICES').rglob('*')):
        if path.is_file():
            rel = path.relative_to(stage).as_posix()
            notices += f'- [{rel}]({rel})\n'
    (stage/'THIRD_PARTY_NOTICES.md').write_text(notices)
    (stage/'BUILD.md').write_text('''# 対応ソースの再生成手順

Python 3.10以上とRust 1.98.1（Cargo含む）、tarを用意してください。実際に用いた版は RELEASE.json、Rust依存の固定版は Cargo.lock です。コンパイラ・Python自体は汎用ツールのため同梱しません。

```sh
tar -xzf dictionary-sources.tar.gz -C empty-directory
cd empty-directory
python3 scripts/verify_release.py --directory .
python3 scripts/rebuild.py
```

`rebuild.py` は vendor に同梱した固定版依存を使用して `cargo build --release --locked --offline` を実行します。ネット接続は不要です。編集可能な `editable/<profile>.tsv` から両形式を再生成し、RELEASE.json の全成果物ハッシュと比較します。語彙を編集した場合はハッシュが変わるため、`--allow-changes` を指定してください。SKKの変更表示は元リリースのメタデータから再現します。改変物を再配布するときは自身の変更主体・日付も追記してください。

元入力から取り込み直す場合は `python3 jisyo.py ingest` を実行し、RELEASE.json の各profileのsource_idsを `python3 jisyo.py export --sources ... --output rebuilt.tsv` に指定してください。取り込み条件・除外処理は jisyo.py、各入力の版・ハッシュは sources.lock.json です。`fetch --update` は別版の取得なので、同一版の再生成には実行しません。

JMEDの入力だけは外部医療コードを含まない正規化済みの読み・表記TSVです。原ZIPの版・ハッシュ・取得日もロックに残します。元の読みと単語ごとの出典対応は provenance.tsv（TSVの標準CSV引用規則）に保存しています。重複統合された候補にも複数の出典行を残します。SQLiteは自動再生成できる中間物なので省略します。

LICENSE・LICENSING.md・CHANGES.md・THIRD_PARTY_NOTICES.md・UPSTREAM_NOTICES/を併せて参照してください。Git履歴や秘密情報は再生成に不要であり同梱しません。
''')
    (stage/'RELEASE_NOTES.md').write_text(f"# {info['tag']}\n\nRustMigemo / UTF-8 SKK辞書。allは全収録元、coreはSKK＋Sudachi、edrdgはJMdict/JMnedict、medicalはJMEDです。\n\nall/coreの統合・編集成果物: GPL-3.0-only。edrdg: CC-BY-SA-4.0。medical: CC-BY-4.0。原資料の条件・帰属は保持しています。\n\n各 `*-dictionaries.tar.gz` に辞書、編集用TSV、ライセンス・帰属・変更表示を同梱しています。単独辞書を取得する場合も該当bundleの告知を参照してください。\n\n[同じ版の対応ソース（固定入力・編集用語彙・生成コード・依存ソース・再生成手順）]({info['corresponding_source_url']})を追加料金なしで取得できます。SHA256SUMSで全ダウンロードを検証できます。\n")


def add_checksums(stage):
    manifest = {}
    for path in sorted(stage.rglob('*')):
        if path.is_file() and path.name != 'CONTENTS.sha256.json':
            with path.open('rb') as f:
                digest = hashlib.file_digest(f, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else stream_digest(f)
            manifest[path.relative_to(stage).as_posix()] = digest
    (stage/'CONTENTS.sha256.json').write_text(json.dumps(manifest, indent=2)+'\n')


def stream_digest(stream):
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024*1024), b''):
        h.update(chunk)
    return h.hexdigest()


def archive_directory(stage, output):
    add_checksums(stage)
    with tarfile.open(output, 'w:gz', compresslevel=1) as archive:
        for path in sorted(stage.iterdir()):
            archive.add(path, arcname=path.name)
