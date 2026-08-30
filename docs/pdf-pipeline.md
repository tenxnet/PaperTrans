# Experimental PDF pipeline

General PDF processing is not part of the supported v1 path. It remains available for experiments where official arXiv HTML is unavailable.

## Why it is experimental

PDF stores visual placement rather than a reliable semantic reading order. Multi-column layouts, footers, equations, figures, tables, and scanned pages require more computation and can still be reconstructed incorrectly. The PDF path also consumes substantially more model tokens than the official arXiv HTML path.

## Install the Docling parser

Docling is an optional dependency. PaperTrans installs the CPU ONNX runtime
directly rather than Docling's cross-platform runtime extra, which resolves to a
GPU package on Linux. Layout inference stays on CPU and the Transformers layout
runtime is not loaded in the web process.

```bash
uv sync --extra docling --extra test --extra mcp
.venv/bin/docling-tools models download layout tableformer \
  --output-dir data/models/docling
export PAPERTRANS_DOCLING_ARTIFACTS_PATH="$PWD/data/models/docling"
```

Keep the model directory outside Git. Production workers should mount it
read-only and set `PAPERTRANS_DOCLING_ARTIFACTS_PATH` explicitly so that a request
cannot trigger an unreviewed model download.

The current adapter is for digitally generated PDFs. OCR is disabled by design;
a scanned or otherwise unreadable PDF fails explicitly instead of silently
producing an empty paper.

## Resource envelope

Docling imports fail closed before exceeding PaperTrans's per-job safety
envelope. The hard ceilings are 50 MiB and 300 pages per source PDF, 250,000 PDF
objects, 14,400 points on either page dimension, four parser threads, 15 minutes
of worker time, 64 MiB of worker JSON, 50,000 extracted items, 32 MiB of extracted
text, and 2,000 visual objects. Raster work is also shared across the whole job:
at most 4,000 renders, 25 million pixels in one render, 150 million cumulative
pixels, and 256 MiB of generated visual files. POSIX runtimes add CPU and
file-size limits as defense in depth. A process-tree supervisor samples aggregate
resident memory and terminates the worker tree above 6 GiB; finite address-space
or data-segment limits are applied as an additional OS-level guard where
supported.

The Web route bounds the received multipart body to 51 MiB and 60 seconds before
parsing it, admits one PDF import at a time through a process-persistent lock,
and gives the complete detached pipeline a 20-minute outer deadline. A timed-out
process group is terminated, including ordinary Docling descendants. Environment
tuning may lower the worker timeout or parser thread count, but cannot raise
these application ceilings.

## Docling semantic pipeline

Docling runs in an isolated child process and writes directly to PaperTrans's
existing evidence, structure, visual-object, and semantic-document contracts. It
does not use Markdown or an LLM as an intermediate representation.

```bash
.venv/bin/papertrans semantic-pipeline path/to/paper.pdf \
  --slug paper-name \
  --repo-root "$PWD" \
  --layout-parser docling \
  --structure-mode hybrid \
  --translation-workers 3
```

The completed run records the shared job manifest at
`output/<slug>/work/papertrans-job.json`, artifact QA at
`output/<slug>/html/qa.json`, metrics at `output/<slug>/run-metrics.json`, and the
web bundle at `output/<slug>/<slug>-html.zip`. A run with `--skip-translation`
finishes as `needs_review`; a parser or QA failure is recorded as `failed`.
The manifest's chunk progress is refreshed after semantic construction and after
each completed translation chunk.

The Web import route uses `--prepare-for-mcp` instead. It performs the same
Docling parse and artifact QA without local translation, then leaves the shared
manifest in `prepared` state for the ChatGPT MCP worker. Prepared previews are
not published by the Web artifact routes; they become visible only after MCP
translation and finalization complete.

QA fails closed when every page is text-empty or when a visible semantic source
block is missing from the document contract. A valid blank page inside an
otherwise readable paper is retained and makes the job `needs_review` rather
than failing the complete conversion.

## Compare parsers on a local corpus

Put representative, redistributable PDFs in one local directory and run the
bounded comparison (10 papers by default):

```bash
.venv/bin/papertrans pdf-benchmark path/to/pdf-corpus \
  --output output/pdf-parser-comparison.json \
  --work-root output/pdf-parser-benchmark \
  --limit 10
```

The command produces JSON plus a neighboring Markdown review sheet. Counts and
timings are diagnostics, not quality scores; use the checklist to inspect reading
order, headings, equations, figures, tables, captions, footnotes, and references.

## Legacy hybrid semantic pipeline

The hybrid path uses deterministic page analysis first and sends only low-confidence pages for model-assisted structure review.

```bash
.venv/bin/papertrans semantic-pipeline path/to/paper.pdf \
  --slug paper-name \
  --repo-root "$PWD" \
  --layout-parser pymupdf \
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
