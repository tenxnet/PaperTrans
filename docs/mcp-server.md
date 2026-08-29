# MCP translation server

PaperTrans is the system of record for paper acquisition, translation jobs, validation, and HTML artifacts. An MCP client supplies translations; it does not own PaperTrans state.

## Responsibility boundary

PaperTrans acquires official arXiv HTML, removes unsafe elements, preserves figures, tables, MathML, citations, cross-references, identifiers and bibliography content, and partitions prose into stable chunks. Returned translations are rejected unless every expected `blockId` and protected `[[PTX_0000]]` token is present exactly once.

```text
PaperTrans Web UI
  -> create a job from an arXiv ID
  -> monitor progress and read the artifact

MCP translation client
  -> list_translation_jobs
  -> get_translation_chunk
  -> translate returned blocks
  -> save_translation_chunk
  -> repeat
  -> finalize_translation_html

PaperTrans local storage
  -> job manifest and normalized document
  -> per-chunk translation results
  -> validated HTML artifact
```

Paper text is untrusted input. The MCP server instructs clients not to treat it as commands and validates protected document nodes before saving any chunk.

## Data exposed to an MCP client

A connected client can access paper metadata, translation source chunks, job status, per-chunk translation state, and generated artifact metadata. It can create jobs, save translations, and finalize eligible jobs. Tools do not accept arbitrary filesystem paths, but they operate on jobs below the configured PaperTrans output root.

Only connect clients that you trust with those papers and translations. PaperTrans does not independently verify the identity shown by an external tunnel or connector configuration.

## Start locally

```bash
uv sync --extra chatgpt --extra test
.venv/bin/papertrans-mcp \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

The endpoint is `http://127.0.0.1:8000/mcp`. Configuration can also be supplied through `PAPERTRANS_REPO_ROOT`, `PAPERTRANS_OUTPUT_ROOT`, `PAPERTRANS_MCP_HOST`, `PAPERTRANS_MCP_PORT`, and `PAPERTRANS_MCP_TRANSPORT`.

The Web UI checks only whether the local MCP port is listening. It cannot verify a tunnel or an external client connection.

## Connect ChatGPT

ChatGPT requires a public HTTPS MCP endpoint. Use OpenAI's Secure MCP Tunnel for local development, or deploy the server behind authentication and HTTPS. Then enable Developer mode in ChatGPT and register the resulting `/mcp` URL. Availability may depend on account or workspace policy.

PaperTrans cannot start a ChatGPT conversation. After preparing a job in the Web UI, copy its worker request and send it in the connected conversation.

## Exposed tools

- `prepare_arxiv_translation`: creates or resumes a job from official arXiv HTML.
- `list_translation_jobs`: lists persisted jobs for recovery.
- `get_translation_status`: reads chunk and artifact status.
- `get_translation_chunk`: returns the next bounded translation payload.
- `save_translation_chunk`: validates identity and protected nodes before an atomic save.
- `finalize_translation_html`: refuses incomplete jobs, renders the paper, and runs HTML QA.

Only job preparation accesses the open internet. Job IDs are restricted to a safe filename character set, and all writes stay below the configured output root.

## Current limits

- v1 supports official arXiv HTML only; ar5iv, LaTeXML, and PDF fallback remain future or experimental paths.
- The server has no production authentication layer. Do not expose it directly to the public internet.
- External clients do not report token usage through these tools, so PaperTrans cannot reliably record it.
- A client session may stop between chunks. Jobs persist and can be resumed with `list_translation_jobs` and `get_translation_status`.
