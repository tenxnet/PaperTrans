# Experimental PDF pipeline

General PDF processing is not part of the supported v1 path. It remains available for experiments where official arXiv HTML is unavailable.

## Why it is experimental

PDF stores visual placement rather than a reliable semantic reading order. Multi-column layouts, footers, equations, figures, tables, and scanned pages require more computation and can still be reconstructed incorrectly. The PDF path also consumes substantially more model tokens than the official arXiv HTML path.

## Hybrid semantic pipeline

The hybrid path uses deterministic page analysis first and sends only low-confidence pages for model-assisted structure review.

```bash
.venv/bin/papertrans semantic-pipeline path/to/paper.pdf \
  --slug paper-name \
  --repo-root "$PWD" \
  --structure-mode hybrid \
  --structure-review-workers 3 \
  --translation-workers 3
```

Use `--structure-mode llm` only when comparing against the older full-page model review path. Runtime, model calls, cache hits, and chunk timing are recorded in `output/<slug>/run-metrics.json`.

## Re-render an existing DocumentIR

```bash
.venv/bin/papertrans render output/paper-name/work/document.json \
  --work-dir output/paper-name/work \
  --output-dir output/paper-name/html \
  --source-pdf data/papers/paper-name/source.pdf \
  --zip output/paper-name/paper-name-ja-html.zip
```

## Expectations

- Inspect section order, equations, figure and table crops, captions, footnotes, and references manually.
- Do not report PDF support as guaranteed in v0.1.
- Do not commit source PDFs, rendered pages, generated translations, or browser traces.
- Prefer official arXiv HTML whenever it is available.
