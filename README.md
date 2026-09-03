<p align="center">
  <img src="docs/assets/papertrans-logo.png" alt="PaperTrans logo" width="160">
</p>

<h1 align="center">PaperTrans</h1>

[English](README.en.md) | 日本語

> **次期リリース候補（v0.2.0-rc.2、未公開）** — 公式arXiv HTMLを安定経路とし、DoclingによるデジタルPDF取込を実験経路として提供します。どちらも接続したMCPクライアントで日本語へ翻訳します。公開済みの`v0.2.0-rc.1`タグは変更しません。

PaperTransは、学術論文の構造、MathML数式、図、表、引用、相互参照、識別子、参考文献を保持しながら、本文を日本語へ翻訳するローカルファーストのWebアプリです。

## できること

- arXiv IDから公式HTMLを取得し、安全なローカル文書へ正規化
- 翻訳対象だけを安定した意味単位へ分割
- 数式、図表、引用リンク、DOI、保護語を原文のまま保持
- 接続したMCPクライアントを翻訳ワーカーとして使い、日本語へ翻訳
- Web UIからデジタルPDFを取り込み、Doclingで解析して同じ翻訳フローへ接続
- block IDと保護トークンを検査し、正規化したDocumentIRを成果物の正本として保存
- 同じDocumentIRからHTMLとMarkdownを兄弟生成し、個別のQA結果とともにZIPへ収録
- ローカルライブラリで検索、タグ、未読・既読、お気に入りを管理
- 論文本文を章・節目次付きでアプリ内閲覧

論文、翻訳、ライブラリ情報はローカルに保存され、既定ではGitへ追加されません。

## Quick Start

### 必要なもの

- macOSまたはLinux
- Gitと[uv](https://docs.astral.sh/uv/)
- Node.js 22以上（Corepackまたはpnpm 11を利用できること）

```bash
git clone https://github.com/tenxnet/PaperTrans.git
cd PaperTrans
./papertrans start
```

初回起動では、固定されたPython・Node依存関係の準備、Doclingのレイアウト・表モデルのダウンロードとハッシュ検証、Webアプリのビルドを行います。その後、MCPとWebを`127.0.0.1`で起動し、`http://127.0.0.1:3000`を開きます。初回はインターネット接続、数分以上の時間、数GBの空き容量が必要です。2回目以降は検証済みの準備結果を再利用します。

`./papertrans`だけでも`start`と同じ動作になります。ターミナルを開いたまま利用し、終了するときは`Ctrl-C`を押すとWebとMCPの両方を停止します。これはmacOS/Linux向けのソースチェックアウト版であり、デスクトップアプリやインストーラーではありません。

よく使う管理コマンドは次のとおりです。

```bash
./papertrans setup                 # 依存関係・モデル・Webビルドだけを準備
./papertrans doctor                # セットアップ状態を確認
./papertrans start --no-browser    # ブラウザを自動で開かずに起動
./papertrans status                # 起動中のWebとMCPを確認
```

キャッシュ済みの依存関係と検証済みモデルだけを使う場合は`./papertrans start --offline`を指定します。ポートを変更する場合は、たとえば`./papertrans start --web-port 3100 --mcp-port 8100`とします。

最初の翻訳ジョブを作る前に、[MCPクライアント登録ガイド](docs/mcp-client-setup.md)に従ってクライアントを接続します。ローカルMCPクライアントは`http://127.0.0.1:8000/mcp`へ直接接続できます。ChatGPTを使う場合、OpenAI Secure MCP Tunnelの作成と認証は別途必要で、`./papertrans`はこの外部設定を自動化しません。

Webの「新しい翻訳」からarXiv IDまたは50MB以下のデジタルPDFを登録してください。表示された「ワーカーへの依頼」を接続済みクライアントへ送ると、PaperTransが進捗と成果物を保存します。

完了したジョブは`html/index.html`と`html/index.md`を兄弟成果物として保存し、`html/qa.json`と`html/markdown-qa.json`に形式別の検査結果を記録します。WebライブラリではHTMLの閲覧、Markdownのダウンロード、両形式とローカルアセットを含むZIPのダウンロードができます。

## MCPクライアントを選ぶ

| クライアント | 接続方法 | Tunnel |
| --- | --- | --- |
| ローカルMCPクライアント | `http://127.0.0.1:8000/mcp`へ直接接続 | 不要 |
| ChatGPT | OpenAI Secure MCP Tunnel経由 | 必要 |

Web UIはジョブ準備、進捗、成果物を管理します。使用モデルと利用枠はMCPクライアント側の設定に従います。

## 対象範囲

- v1の正式な入力は公式arXiv HTMLです。
- DoclingによるデジタルPDF取込は引き続き実験機能です。複雑なレイアウトでは読み順、数式、表を誤る可能性があります。
- スキャンPDFのOCR、翻訳済みPDFの生成、ar5iv・LaTeXMLフォールバックは未対応または評価中です。
- 翻訳先はv1では日本語のみです。Web UIは日本語と英語を切り替えられます。
- WebアプリとMCPサーバーは単独利用のローカル実行を前提としています。
- 公開成果物のホスティングや共同編集は対象外です。

## Roadmap（予定・検討中）

優先順位や仕様は、実験結果とIssueで変更される可能性があります。

- [ ] arXiv公式HTMLがない場合のar5iv・LaTeXMLフォールバック
- [ ] Docling PDF取込のレイアウト・数式・表・引用QAを強化し、OCR対応を評価
- [ ] 隔離したバックエンドで翻訳済みPDF生成を評価
- [ ] 用語集の編集、論文別ルール、対象節だけの再翻訳
- [ ] 本文から引用・参考文献・図表へ移動できる相互リンク
- [ ] 翻訳の並列化、キャッシュ、処理時間・使用量の計測
- [ ] フォルダ、全文検索、一括操作などライブラリ管理の強化
- [ ] 日本語以外の翻訳先と外部翻訳プロバイダー連携
- [ ] 署名・自動更新を備えたデスクトップアプリ配布

## ローカルデータと安全性

- `data/`、`output/`、`.env*`はGit管理外です。
- WebアプリとMCPサーバーは`127.0.0.1`へバインドしてください。
- Webは`127.0.0.1`、`localhost`、`[::1]`以外のHostヘッダーを拒否します。DNS別名やリバースプロキシ経由の公開は、認証を含む別のセキュリティ設計なしでは対応しません。
- 認証なしのMCPサーバーを直接インターネットへ公開しないでください。
- 論文と翻訳物の利用・共有可否は、利用者が原論文のライセンスと適用法を確認してください。
- 生成物はAI/MCPによる機械翻訳です。翻訳と構造QAは誤る可能性があるため、研究や引用に使う前に必ず原文と照合してください。
- arXivへの負荷を避けるため、原則として1件ずつ取得し、連続取得には適度な間隔を空けてください。既に準備済みの同一ジョブは再利用してください。

## ドキュメント

- [MCPクライアント登録](docs/mcp-client-setup.md)
- [更新・バックアップ・アンインストール](docs/local-data-lifecycle.md)
- [トラブルシューティング](docs/troubleshooting.md)
- [ドキュメント索引](docs/README.md)
- [変更履歴](CHANGELOG.md)
- [セキュリティポリシー](SECURITY.md)
- [コントリビューション](CONTRIBUTING.md)
- [メンテナー向けリリース手順](RELEASING.md)

## 開発と検証

```bash
uv lock --check
uv run --frozen --extra test --group docling pytest -q
pnpm typecheck
pnpm test
pnpm build
```

変更を送る前に[CONTRIBUTING.md](CONTRIBUTING.md)を確認してください。論文PDF、生成された翻訳、APIキー、秘密情報をIssueやPull Requestへ添付しないでください。

## ライセンス

PaperTransプロジェクトが著作権を有するソースコード、リポジトリ内Skills、テンプレート、ドキュメントは[Apache License 2.0](LICENSE)で提供します。`workers/babeldoc/patches/`の上流AGPLコードに対するパッチなど、個別に識別された第三者由来の素材にはそれぞれのライセンスが適用され、リポジトリのApache-2.0はそれらを再許諾しません。依存関係と配布上の注意は[依存ライセンス監査](docs/dependency-licenses.md)を確認してください。これらのライセンスは、利用者が取得した論文、論文中の図表、生成された翻訳成果物には自動的に適用されません。
