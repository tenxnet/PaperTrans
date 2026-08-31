# Local data lifecycle / ローカルデータの管理

[日本語](#日本語) | [English](#english)

## 日本語

### 保存場所と保持期間

ソースチェックアウト版は、既定で次の場所へデータを保存します。

| 場所 | 内容 |
| --- | --- |
| `data/papers/<job-id>/` | Webから取り込んだ原本PDF |
| `data/library.json` | タグ、既読、お気に入りなどのライブラリ状態 |
| `data/models/docling/` | リリースで固定・検証されたDoclingモデル |
| `data/runtime/`、`data/logs/` | セットアップ状態、実行状態、ローカルログ |
| `output/<job-id>/` | 中間状態、翻訳、HTML、Markdown、ZIP、QA結果 |
| `.venv/`、`node_modules/`、`.next/` | 再生成可能な依存関係とWebビルド |

`--data-root`、`--output-root`、`--model-root`を指定した場合は、その指定先が
正本です。同じデータで起動するときは毎回同じ指定を使用してください。

v0.2.0-rc.1は論文や成果物を自動削除しません。保持期間とバックアップ先は
利用者が決めます。原本PDF、翻訳、ログ、バックアップには機密情報や著作物が
含まれる可能性があります。端末の暗号化、アクセス権、バックアップ先、廃棄
方法をその内容に合わせて選んでください。

### 更新前のバックアップ

1. Web画面で、PDF取込が`preparing`のまま残っていないことを確認します。取込中に
   Webを停止しても、隔離されたPDF解析workerは処理を続ける場合があります。
2. `./papertrans`を実行しているターミナルで`Ctrl-C`を押します。
3. `./papertrans status`でWebとMCPが`stopped`、PDF importが`idle`または
   `stale`、Artifact maintenanceが`safe`であることを確認します。独自のポートや
   データ・成果物ルートを使用している場合は、起動時と同じ引数を付けます。
   `running`、`starting`、`unknown`、`wait`のいずれかが表示された間は、コピー、
   移動、削除、更新を行わず、解析終了後にもう一度確認してください。`stale`はworkerが
   終了済みであることを示しますが、中断された成果物は別途確認が必要です。
4. `data/`と`output/`、または指定したデータ・成果物ルートを、リポジトリ外の
   新しいバックアップ先へコピーします。
5. コピー後に、少なくとも`library.json`、取り込んだ`source.pdf`、必要な
   `work/*job.json`、`html/index.html`、`html/index.md`を開けることを確認します。
6. バックアップのリリース名、Git commit、日時、使用したカスタムルートを記録します。

モデル、`.venv/`、`node_modules/`、`.next/`は通常再生成できますが、完全な
オフライン復旧が必要ならDoclingモデルと利用中の依存キャッシュも保存して
ください。MCPクライアントやChatGPT Tunnelの登録はPaperTransリポジトリ外の
設定なので、別途記録が必要です。APIキーを平文バックアップへ入れないでください。

#### 旧形式のstale PDF取込ロック

更新前の版が残した単一ファイル形式の`output/.papertrans-pdf-import.lock`は、
別版のworkerを誤って解放しないため、このRCでは自動削除しません。PDF取込が
`pdf_import_busy`のままになる場合は、まずWebとMCPを停止し、
`./papertrans status`がPDF import `stale`かつArtifact maintenance `safe`を示す
ことを確認してください。対象が**通常ファイル**であることを確認した場合に限り、
削除せず、リポジトリ外の新しい隔離先へ移動してから再起動します。ロックが
ディレクトリ、`running`、`starting`、`unknown`のいずれかなら移動せず、ログを
秘匿化してissueへ報告してください。カスタム成果物ルートではその直下が対象です。

### ソース版の更新

作業ツリーへ変更がないこととバックアップを確認してから更新します。

```bash
git status --short --branch
git fetch origin --tags
git switch --detach v0.2.0-rc.1
./papertrans setup
./papertrans doctor
```

`git status`に変更がある場合は、その内容を確認せずに切り替えないでください。
タグを指定すると公開済みの同じソースを再現できます。初回の`setup`にはネット
接続が必要です。準備完了後に、ネットワークを切断した状態で
`./papertrans start --offline --no-browser`が起動することを確認できます。

新しい版で問題があればサービスを停止し、以前のタグを別の新しいcheckoutへ
取得して、バックアップのコピーを使って確認してください。新しいRCが書いた
データを古い版が読める保証はないため、唯一のバックアップを直接開かないでください。

### 論文単位の削除

このRCにはライブラリから原本と成果物を一括削除するUIがありません。削除する
場合は上記の手順でArtifact maintenanceが`safe`になったことを確認し、対象の正確な
`job-id`をWeb画面とmanifestで確認してから、
次の2ディレクトリをいったんリポジトリ外の隔離場所へ移動してください。

- `data/papers/<job-id>/`
- `output/<job-id>/`

カスタムルートを使用している場合はその配下が対象です。`data/library.json`には
タグ等が残る場合がありますが、対応する`output/<job-id>`がなければライブラリには
表示されません。確認できるまでは完全削除せず、復元可能な状態を保ってください。

### アンインストール

1. 上記の手順でArtifact maintenanceが`safe`になったことを確認し、必要な
   `data`と`output`をバックアップします。
2. MCPクライアントからPaperTrans接続を削除します。ChatGPT Tunnelを作成した
   場合は、Tunnel側でも停止・削除し、不要な認証情報を失効させます。
3. 正確なPaperTrans checkoutをファイルマネージャーのゴミ箱へ移動します。
   既定配置なら、これでモデル、`.venv`、Node依存、ビルドも対象になります。
4. checkout外に指定したデータ、成果物、モデル、uv/pnpmキャッシュ、バックアップは
   自動では削除されません。各パスを個別に確認してから保持または削除します。

PaperTransには常駐デーモンやOS全体のアンインストーラーはありません。uv、Node.js、
pnpm、Gitは共有ツールなので、他のプロジェクトが使っていないことを確認せず削除
しないでください。通常の削除は安全な消去を保証しません。

## English

### Locations and retention

The source-checkout release stores data in these locations by default:

| Location | Contents |
| --- | --- |
| `data/papers/<job-id>/` | Original PDFs imported through the Web UI |
| `data/library.json` | Tags, read state, favorites, and other library state |
| `data/models/docling/` | Release-pinned and verified Docling models |
| `data/runtime/`, `data/logs/` | Setup/runtime state and local logs |
| `output/<job-id>/` | Work state, translations, HTML, Markdown, ZIP, and QA results |
| `.venv/`, `node_modules/`, `.next/` | Reproducible dependencies and the Web build |

When `--data-root`, `--output-root`, or `--model-root` is used, that selected
location is authoritative. Reuse the same arguments whenever starting with the
same data.

v0.2.0-rc.1 does not automatically expire papers or artifacts. You choose the
retention period and backup destination. Source PDFs, translations, logs, and
backups may contain confidential or copyrighted material; use suitable disk
encryption, permissions, backup access controls, and disposal procedures.

### Back up before an update

1. In the Web UI, confirm that no PDF import remains in `preparing`. The isolated
   PDF worker may keep running if Web is stopped during an import.
2. Press `Ctrl-C` in the terminal running `./papertrans`.
3. Run `./papertrans status` and require Web and MCP to be `stopped`, PDF import
   to be `idle` or `stale`, and Artifact maintenance to be `safe`. Include the
   same custom ports, data root, and output root used at startup. Do not copy,
   move, delete, or update anything while the command reports `running`,
   `starting`, `unknown`, or `wait`; let the worker finish and check again.
   `stale` means the worker has exited, but an interrupted artifact still needs
   inspection.
4. Copy `data/` and `output/`, or the selected data and artifact roots, to a new
   backup location outside the repository.
5. Verify the copy by opening at least `library.json`, imported `source.pdf`
   files, the required `work/*job.json` manifests, `html/index.html`, and
   `html/index.md`.
6. Record the release, Git commit, date, and custom-root arguments with the backup.

Models, `.venv/`, `node_modules/`, and `.next/` are normally reproducible. For
a completely offline recovery, also preserve the verified Docling models and
the dependency caches you rely on. MCP-client and ChatGPT Tunnel registrations
live outside the PaperTrans repository and need a separate record. Do not put
API keys in an unencrypted backup.

#### Stale legacy PDF-import lock

This RC never automatically removes the single-file
`output/.papertrans-pdf-import.lock` left by an earlier version, because doing
so could release a worker still owned by that version. If imports remain
`pdf_import_busy`, first stop Web and MCP and require `./papertrans status` to
show PDF import `stale` and Artifact maintenance `safe`. Only after confirming
that the lock is a **regular file**, move it—do not delete it—to a new recovery
location outside the repository, then restart. Do not move a directory or a
lock reported as `running`, `starting`, or `unknown`; redact the logs and report
an issue instead. With a custom artifact root, inspect the lock directly under
that root.

### Update a source checkout

After confirming the backup and a clean worktree:

```bash
git status --short --branch
git fetch origin --tags
git switch --detach v0.2.0-rc.1
./papertrans setup
./papertrans doctor
```

Do not switch versions without understanding any changes shown by `git status`.
Checking out a tag reproduces the published source. The first `setup` requires
network access. Once it succeeds, you can disconnect the network and verify
`./papertrans start --offline --no-browser`.

If an update fails, stop the services, create a separate clean checkout of the
previous tag, and test it with a copy of the backup. A previous RC is not
guaranteed to read data already written by a newer RC, so never test a rollback
against the only backup.

### Remove one paper

This RC has no library action that atomically removes both a source and all of
its artifacts. First use the procedure above to confirm that Artifact maintenance
is `safe`, then confirm the exact `job-id` in the Web UI and manifest and move both
directories to a recoverable location outside the repository:

- `data/papers/<job-id>/`
- `output/<job-id>/`

Use the selected custom roots when applicable. Tags and similar state may
remain in `data/library.json`, but without the matching `output/<job-id>` the
paper is not listed. Keep the moved directories recoverable until the result
has been verified.

### Uninstall

1. Use the procedure above to confirm that Artifact maintenance is `safe`, then
   back up any `data` and `output` you want to retain.
2. Remove the PaperTrans connection from the MCP client. If you created a
   ChatGPT Tunnel, stop/delete it there and revoke credentials that are no
   longer needed.
3. Move the exact PaperTrans checkout to the operating system's Trash. With
   default paths this also includes its models, `.venv`, Node dependencies,
   and Web build.
4. Data, output, models, uv/pnpm caches, or backups configured outside the
   checkout are not removed automatically. Inspect each exact path before
   retaining or deleting it.

PaperTrans installs no system service and has no system-wide uninstaller. Git,
uv, Node.js, and pnpm are shared tools; do not remove them without checking
whether other projects use them. Ordinary deletion is not guaranteed secure
erasure.
