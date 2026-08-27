# PaperTrans MVP

学術PDFを構造化し、Codexで全文を日本語訳して、原文の図・表・数式を保持したオフラインHTMLへ変換するローカルWebアプリです。

## 現在のMVP

- PyMuPDFで本文ブロック、図、表、数式、ページ座標を`DocumentIR`へ抽出
- 図表・キャプション・数式は翻訳せず、PDFから高解像度画像として埋込み
- 検出漏れに備え、全ページの原文画像を折りたたみ表示で保持
- `.agents/skills/academic-paper-translator/`のSkillを指定してCodex CLIで節単位翻訳
- blockId、引用、DOI、保護語、翻訳量を検査し、失敗したチャンクだけ再試行
- 日本語本文、段落単位の原文、警告、原文PDFを含むオフラインHTML/ZIP生成
- Next.js画面で生成HTMLを閲覧し、追加PDFの取込とバックグラウンド翻訳を開始

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
