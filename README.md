<p align="center">
  <img src="docs/assets/papertrans-logo.png" alt="PaperTrans logo" width="160">
</p>

<h1 align="center">PaperTrans</h1>

[English](README.en.md) | 日本語

> **公開前プレビュー（v0.1）** — v1の正式経路は、公式arXiv HTMLをローカルMCPサーバーで準備し、接続したMCPクライアントで日本語へ翻訳する方法です。PDF解析とCodex CLI経路は実験機能です。

PaperTransは、学術論文の構造、MathML数式、図、表、引用、相互参照、識別子、参考文献を保持しながら、本文を日本語へ翻訳するローカルファーストのWebアプリです。

## できること

- arXiv IDから公式HTMLを取得し、安全なローカル文書へ正規化
- 翻訳対象だけを安定した意味単位へ分割
- 数式、図表、引用リンク、DOI、保護語を原文のまま保持
- 接続したMCPクライアントを翻訳ワーカーとして使い、日本語へ翻訳
- block IDと保護トークンを検査し、正規化したDocumentIRを成果物の正本として保存
- 同じDocumentIRからHTMLとMarkdownを兄弟生成し、個別のQA結果とともにZIPへ収録
- ローカルライブラリで検索、タグ、未読・既読、お気に入りを管理
- 論文本文を章・節目次付きでアプリ内閲覧

論文、翻訳、ライブラリ情報はローカルに保存され、既定ではGitへ追加されません。

## Quick Start

### 必要なもの

- macOSまたはLinux
- Python 3.10以上と[uv](https://docs.astral.sh/uv/)
- Node.js 22以上とpnpm 11

```bash
git clone https://github.com/tenxnet/PaperTrans.git
cd PaperTrans
uv sync --extra mcp
pnpm install --frozen-lockfile
```

ローカルMCPサーバーを起動します。

```bash
.venv/bin/papertrans-mcp --host 127.0.0.1 --port 8000
```

別のターミナルでWebアプリを起動します。

```bash
pnpm dev --hostname 127.0.0.1
```

最初のジョブを作る前に、[MCPクライアント登録ガイド](docs/mcp-client-setup.md)に従ってクライアントを接続します。ローカルMCPクライアントは上記URLへ直接接続でき、ChatGPTはSecure MCP Tunnelを使います。

`http://127.0.0.1:3000`を開き、「新しい翻訳」からarXiv IDを登録してください。表示された「ワーカーへの依頼」をコピーして接続済みクライアントへ送ると、PaperTransが進捗と成果物を保存します。ポートを変更する場合は、Web起動コマンドの末尾に`--port 3100`を追加します。

完了したジョブは`html/index.html`と`html/index.md`を兄弟成果物として保存し、`html/qa.json`と`html/markdown-qa.json`に形式別の検査結果を記録します。ダウンロードZIPには両形式とそのローカルアセットが含まれます。

## MCPクライアントを選ぶ

| クライアント | 接続方法 | Tunnel |
| --- | --- | --- |
| ローカルMCPクライアント | `http://127.0.0.1:8000/mcp`へ直接接続 | 不要 |
| ChatGPT | OpenAI Secure MCP Tunnel経由 | 必要 |

Web UIはジョブ準備、進捗、成果物を管理します。使用モデルと利用枠はMCPクライアント側の設定に従います。

## 対象範囲

- v1の正式な入力は公式arXiv HTMLです。
- ar5iv、LaTeXML、一般PDF解析、PDF OCRは将来候補または実験機能です。
- 翻訳先はv1では日本語のみです。Web UIは日本語と英語を切り替えられます。
- WebアプリとMCPサーバーは単独利用のローカル実行を前提としています。
- 公開成果物のホスティングや共同編集は対象外です。

## Roadmap（予定・検討中）

優先順位や仕様は、実験結果とIssueで変更される可能性があります。

- [ ] arXiv公式HTMLがない場合のar5iv・LaTeXMLフォールバック
- [ ] IEEEなど一般PDF向けの構造解析（数式・図表・引用の保持）
- [ ] 用語集の編集、論文別ルール、対象節だけの再翻訳
- [ ] 本文から引用・参考文献・図表へ移動できる相互リンク
- [ ] 翻訳の並列化、キャッシュ、処理時間・使用量の計測
- [ ] フォルダ、全文検索、一括操作などライブラリ管理の強化
- [ ] 日本語以外の翻訳先と外部翻訳プロバイダー連携

## ローカルデータと安全性

- `data/`、`output/`、`.env*`はGit管理外です。
- WebアプリとMCPサーバーは`127.0.0.1`へバインドしてください。
- 認証なしのMCPサーバーを直接インターネットへ公開しないでください。
- 論文と翻訳物の利用・共有可否は、利用者が原論文のライセンスと適用法を確認してください。
- 生成物はAI/MCPによる機械翻訳です。翻訳と構造QAは誤る可能性があるため、研究や引用に使う前に必ず原文と照合してください。
- arXivへの負荷を避けるため、原則として1件ずつ取得し、連続取得には適度な間隔を空けてください。既に準備済みの同一ジョブは再利用してください。

## ドキュメント

- [MCPクライアント登録](docs/mcp-client-setup.md)
- [トラブルシューティング](docs/troubleshooting.md)
- [ドキュメント索引](docs/README.md)
- [セキュリティポリシー](SECURITY.md)
- [コントリビューション](CONTRIBUTING.md)

## 開発と検証

```bash
.venv/bin/pytest -q
pnpm typecheck
pnpm build
```

変更を送る前に[CONTRIBUTING.md](CONTRIBUTING.md)を確認してください。論文PDF、生成された翻訳、APIキー、秘密情報をIssueやPull Requestへ添付しないでください。

## ライセンス

PaperTransのソースコード、リポジトリ内Skills、テンプレート、ドキュメントは[Apache License 2.0](LICENSE)で提供します。このライセンスは、利用者が取得した論文、論文中の図表、生成された翻訳成果物には自動的に適用されません。
