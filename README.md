# PaperTrans

[English](README.en.md) | 日本語

> **公開前プレビュー（v0.1）** — v1の正式対象は、公式arXiv HTMLから日本語の閲覧用HTMLを作るローカル用途です。ChatGPT MCPワーカーとPDF解析は実験機能です。

PaperTransは、学術論文の構造、MathML数式、図、表、引用、相互参照、識別子、参考文献を保持しながら、本文を日本語へ翻訳するローカルファーストのWebアプリです。

## v0.1でできること

- arXiv IDから公式HTMLを取得し、安全なローカル文書へ正規化
- 翻訳対象だけを安定した意味単位へ分割
- 数式、図表、引用リンク、DOI、保護語を原文のまま保持
- Codex CLI、または実験的なChatGPT Connectorで日本語へ翻訳
- block IDと保護トークンを検査してからHTMLを生成
- ローカルライブラリで検索、タグ、未読・既読、お気に入りを管理
- 論文本文を章・節目次付きでアプリ内閲覧

論文、翻訳、ライブラリ情報はローカルに保存され、既定ではGitへ追加されません。

## Quick Start

### 必要なもの

- macOSまたはLinux
- Python 3.10以上と[uv](https://docs.astral.sh/uv/)
- Node.js 22以上とpnpm 11
- Codex翻訳を使う場合は、ログイン済みのCodex CLI

```bash
git clone https://github.com/tenxnet/PaperTrans.git
cd PaperTrans
uv sync --extra test
pnpm install --frozen-lockfile
```

公式arXiv HTMLをCodexで翻訳します。

```bash
.venv/bin/papertrans arxiv-html-pipeline 2508.19843 \
  --slug arxiv-2508.19843 \
  --repo-root "$PWD"
```

ローカルWebアプリを起動します。

```bash
pnpm dev --hostname 127.0.0.1
```

`http://127.0.0.1:3000`を開きます。ポートを変更する場合は、末尾に`--port 3100`を追加してください。`output/`にある完成済みジョブは自動的にライブラリへ表示されます。

## 翻訳方法

| 方法 | v0.1での位置付け | Tunnel | 費用・利用枠 |
| --- | --- | --- | --- |
| Codex CLI | 標準のローカル経路 | 不要 | Codex利用枠 |
| ChatGPT Connector | 実験機能 | 必要 | ChatGPT側の利用枠 |
| OpenAI API | 未実装 | 不要 | API従量課金 |

Web UIのサイドバーにある「Provider設定」から、ChatGPT ConnectorとCodex CLIを切り替えられます。Codex CLIを選ぶと、モデルと推論強度を指定した実行コマンドを生成します。詳しくは[Provider設定](docs/providers.md)を参照してください。

ChatGPTを翻訳ワーカーとして使う場合は、ローカルMCPサーバーとSecure MCP Tunnelの設定が必要です。PaperTransからChatGPTの会話を直接開始することはできません。詳しい責任範囲、公開データ、起動方法は[ChatGPT翻訳ワーカー](docs/chatgpt-worker.md)を参照してください。

## 対象範囲

- v1の正式な入力は公式arXiv HTMLです。
- ar5iv、LaTeXML、一般PDF解析、PDF OCRは将来候補または実験機能です。
- 翻訳先はv1では日本語のみです。Web UIは日本語と英語を切り替えられます。
- WebアプリとMCPサーバーは単独利用のローカル実行を前提としています。
- 公開成果物のホスティングや共同編集は対象外です。

## ローカルデータと安全性

- `data/`、`output/`、`.env*`はGit管理外です。
- WebアプリとMCPサーバーは`127.0.0.1`へバインドしてください。
- 認証なしのMCPサーバーを直接インターネットへ公開しないでください。
- 論文と翻訳物の利用・共有可否は、利用者が原論文のライセンスと適用法を確認してください。
- 翻訳と構造QAは誤る可能性があります。研究や引用に使う前に原文と照合してください。

## ドキュメント

- [ドキュメント索引](docs/README.md)
- [ChatGPT翻訳ワーカー](docs/chatgpt-worker.md)
- [Provider設定](docs/providers.md)
- [実験的PDFパイプライン](docs/pdf-pipeline.md)
- [トラブルシューティング](docs/troubleshooting.md)
- [コントリビューション](CONTRIBUTING.md)
- [OSSリリースチェックリスト](docs/oss-release-checklist.md)

## 開発と検証

```bash
.venv/bin/pytest -q
pnpm typecheck
pnpm build
```

変更を送る前に[CONTRIBUTING.md](CONTRIBUTING.md)を確認してください。論文PDF、生成された翻訳、APIキー、秘密情報をIssueやPull Requestへ添付しないでください。

## ライセンス

PaperTransのソースコード、リポジトリ内Skills、テンプレート、ドキュメントは[Apache License 2.0](LICENSE)で提供します。このライセンスは、利用者が取得した論文、論文中の図表、生成された翻訳成果物には自動的に適用されません。
