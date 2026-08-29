# ChatGPT translation worker (experimental)

> This integration lets a configured ChatGPT workspace read translation chunks and write validated translations through PaperTrans MCP tools. Starting a tunnel expands access beyond the local machine; review the data boundary below first.

## Responsibility boundary

PaperTrans is the system of record. It acquires official arXiv HTML, removes unsafe elements, preserves figures, tables, MathML, citations, cross-references, identifiers and bibliography content, partitions prose into stable chunks, validates returned translations, and creates the final HTML/ZIP artifacts.

ChatGPT is only the translation worker. It receives one bounded chunk at a time and returns Japanese text keyed by the unchanged `blockId`. Protected HTML nodes are represented as `[[PTX_0000]]` tokens. A save is rejected unless every expected block is present and every protected token occurs exactly once and byte-for-byte.

```text
ChatGPT conversation
  -> prepare_arxiv_translation
  -> get_translation_chunk
  -> translate returned blocks
  -> save_translation_chunk
  -> repeat
  -> finalize_translation_html

PaperTrans local storage
  -> chatgpt-job.json
  -> html-document.json
  -> per-chunk results
  -> validated HTML and offline ZIP
```

This first experiment is a tool-only MCP server. It deliberately has no ChatGPT widget because the reading UI and artifacts remain owned by the local PaperTrans web app.

## Data exposed through the Connector

A connected ChatGPT workspace can access paper metadata, translation source chunks, job status, per-chunk translation state, and generated artifact metadata. It can save translations and finalize eligible jobs. The MCP server does not expose arbitrary filesystem paths, but it does operate on every job below the configured PaperTrans output root.

Only connect a ChatGPT workspace that you trust with those papers and translations. PaperTrans does not independently verify the workspace identity shown by the tunnel configuration.

## Start locally

```bash
uv sync --extra chatgpt --extra test
.venv/bin/papertrans-mcp \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. Configuration can also be supplied through `PAPERTRANS_REPO_ROOT`, `PAPERTRANS_OUTPUT_ROOT`, `PAPERTRANS_MCP_HOST`, `PAPERTRANS_MCP_PORT`, and `PAPERTRANS_MCP_TRANSPORT`.

ChatGPT requires a public HTTPS MCP endpoint. Use OpenAI's Secure MCP Tunnel for local development or deploy the server behind authentication and HTTPS. Then enable Developer mode in ChatGPT and register the resulting `/mcp` URL. Availability of Developer mode can depend on the account or workspace policy.

The PaperTrans Web UI reports only whether the local MCP port is listening. It does not prove that the tunnel is running or that ChatGPT has registered the Connector.

## Exposed tools

- `prepare_arxiv_translation`: fetches official arXiv HTML and creates/resumes a job.
- `list_translation_jobs`: lists persisted jobs for recovery.
- `get_translation_status`: reads chunk and artifact status.
- `get_translation_chunk`: returns the next bounded translation payload.
- `save_translation_chunk`: validates identity and protected nodes before an atomic save.
- `finalize_translation_html`: refuses incomplete jobs, renders the paper, runs HTML QA, and creates a ZIP.

Only `prepare_arxiv_translation` accesses the open internet. No tool accepts an arbitrary filesystem path. Job IDs are restricted to a safe filename character set, and all writes stay below the configured output root.

## Known experimental limits

- This path supports official arXiv HTML only; ar5iv, LaTeXML source conversion, and PDF fallback are future routes.
- The server has no production authentication layer. Do not expose it directly to the public internet.
- ChatGPT does not report conversation token usage to MCP tools, so PaperTrans cannot record it.
- The current glossary is static. Paper-specific terminology extraction and user overrides remain future work.
- A ChatGPT conversation may stop between chunks. The persisted job can be resumed, but full autonomous completion depends on the ChatGPT tool-call session continuing.
