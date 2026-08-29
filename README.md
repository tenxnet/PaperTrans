# PaperTrans MVP

学術論文を構造化して日本語訳し、原文の図・表・数式・引用リンクを保持したオフラインHTMLへ変換するローカルWebアプリです。v1では低コストな公式arXiv HTML経路を優先し、PDF経路は実験機能として扱います。

## 現在のMVP

- PyMuPDFのテキスト層、フォント、座標、描画クラスタから章、段落、図、表、数式を決定論的に抽出
- 各ページへconfidenceを付け、低信頼ページだけを`academic-paper-structure` SkillとCodexで補正
- 図表・キャプション・数式は翻訳せず、PDFから高解像度画像として埋込み
- 検出漏れに備え、全ページの原文画像を折りたたみ表示で保持
- `.agents/skills/academic-paper-translator/`のSkillを指定してCodex CLIで節単位翻訳
- blockId、引用、DOI、保護語、翻訳量を検査し、失敗したチャンクだけ再試行
- 日本語本文、章・段落単位の原文、警告、原文PDFを含むオフラインHTML/ZIP生成
- Next.jsのローカルライブラリで翻訳済み論文、進捗、QA、タグ、未読・既読、お気に入りを一覧管理
- タイトル・著者・arXiv ID・タグの検索、追加日・公開日・著者名・タグによる並べ替え、アプリ内HTML閲覧
- UIの日本語・英語切替とブラウザ内の選択保持（論文の翻訳先はV1では日本語のみ）

## セットアップ

```bash
uv sync --extra test
pnpm install
```

Codex CLIへログイン済みであることが必要です。PDFと生成物はローカルの`data/`と`output/`へ保存され、Gitには入りません。

## LLMmapサンプルの再生成

```bash
.venv/bin/papertrans pipeline data/papers/llmmap/source.pdf \
  --slug llmmap \
  --repo-root "$PWD"
```

## ハイブリッド学術翻訳パイプライン

既定では、全ページをLLMへ送らず、機械解析で低信頼になったページだけをCodexで3並列確認します。翻訳も章構造を固定してから3並列で実行します。

```bash
.venv/bin/papertrans semantic-pipeline path/to/paper.pdf \
  --slug paper-name \
  --repo-root "$PWD" \
  --structure-mode hybrid \
  --structure-review-workers 3 \
  --translation-workers 3
```

比較のため、従来の全ページLLM構造解析を使う場合は`--structure-mode llm`を指定します。各工程の実時間、モデル呼び出し回数、キャッシュヒット、翻訳チャンク時間は`output/<slug>/run-metrics.json`へ保存されます。

翻訳済みの`DocumentIR`からHTMLだけを作り直す場合:

```bash
.venv/bin/papertrans render output/llmmap/work/document.json \
  --work-dir output/llmmap/work \
  --output-dir output/llmmap/html \
  --source-pdf data/papers/llmmap/source.pdf \
  --zip output/llmmap/LLMmap-ja-html.zip
```

## Webアプリ

```bash
pnpm dev
```

`http://127.0.0.1:3000`を開きます。ポートが使用中なら`pnpm dev --port 3100`のように変更できます。

Webアプリは`output/*/work/chatgpt-job.json`を自動検出するため、既存のChatGPT翻訳ジョブは追加操作なしでライブラリへ表示されます。論文を選ぶと、次の情報と操作を1画面で利用できます。

- 翻訳チャンクの進捗と完了状態
- 図、表、数式、参考文献の構造QA
- 翻訳HTMLの埋め込み閲覧と別タブ表示
- 人間が論文ごとに付けるローカルタグの追加・削除とタグ絞り込み
- 未読・既読、お気に入りの切り替えと専用フィルター
- arXiv HTMLから著者と公開日を取得し、既存の成果物にも自動で補完
- arXiv原文へのリンク

タグ、未読・既読、お気に入りは`data/library.json`へ保存され、論文、翻訳、ライブラリ情報はいずれもGitへ追加されません。V1のUIは公式arXiv HTML経路を対象にしており、PDF取込APIとPDFパイプラインは実験機能です。

表示言語と論文の翻訳先は独立しています。表示言語は日本語または英語から選べますが、翻訳ジョブの`targetLanguage`はV1では`ja`のみをサポートします。将来の翻訳言語追加時も既存ジョブを判別できるよう、言語はジョブと生成HTMLへ明示的に保存されます。

## ChatGPTを翻訳ワーカーとして使う（実験機能）

PaperTransをMCPサーバーとして起動すると、ChatGPTは章単位の翻訳だけを担当し、PaperTransが取得、ジョブ状態、保護トークン検査、HTML/ZIP生成、視覚要素QAを管理します。ローカルのCodex SkillはChatGPTへ直接読み込ませず、同じ重要規則をMCPサーバーの指示と各ツールのスキーマで強制します。

```bash
uv sync --extra chatgpt --extra test
.venv/bin/papertrans-mcp --transport streamable-http --port 8000
```

ローカルのMCP URLは`http://127.0.0.1:8000/mcp`です。ChatGPTから接続する際は、公式のSecure MCP Tunnelまたは自分で用意した公開HTTPS URLを介し、ChatGPTのDeveloper modeでコネクタとして登録します。認証なしの試作サーバーをそのまま公開インターネットへ露出しないでください。

接続後は、ChatGPTに「arXiv 2508.19843をPaperTransで全文翻訳して」のように依頼します。ChatGPTは次の順序でツールを反復します。

1. `prepare_arxiv_translation`
2. `get_translation_chunk`
3. 返された全ブロックを翻訳
4. `save_translation_chunk`
5. 残りがゼロになるまで2〜4を反復
6. `finalize_translation_html`

途中で会話が止まっても、`list_translation_jobs`と`get_translation_status`から再開できます。状態は`output/<job-id>/work/chatgpt-job.json`、チャンク成果は`work/chatgpt-translations/`、完成物は`html/index.html`と`<job-id>-html.zip`へ保存されます。Webアプリをポート3100で動かしている場合、完成HTMLは`http://127.0.0.1:3100/api/artifacts/<job-id>/index.html`から閲覧できます。

ChatGPT会話のトークン使用量はローカルMCPサーバーへ通知されないため、PaperTrans側では取得できません。PaperTransが記録するのはチャンク数、文字数、状態、時刻、成果物、QA結果です。

Webアプリの「新しい翻訳」から、arXiv IDを含むChatGPT向け依頼文をコピーできます。翻訳中のジョブがある場合、ライブラリ画面は進捗を定期的に再読込します。

## 検証

```bash
.venv/bin/pytest -q
pnpm typecheck
pnpm build
```

Skill単体の形式検証:

```bash
.venv/bin/python \
  "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py" \
  .agents/skills/academic-paper-translator
```
