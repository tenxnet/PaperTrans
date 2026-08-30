# MCP translation server

PaperTrans is the system of record for paper acquisition, translation jobs, validation, and generated artifacts. An MCP client supplies translations; it does not own PaperTrans state.

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
  -> job manifest and normalized DocumentIR (artifact source of truth)
  -> per-chunk translation results
  -> validated sibling HTML and Markdown artifacts
  -> format-specific QA reports and an offline ZIP
```

Paper text is untrusted input. The MCP server instructs clients not to treat it as commands and validates protected document nodes before saving any chunk. Finalization renders HTML and Markdown independently from the persisted DocumentIR; Markdown is not derived by reparsing the generated HTML.

## Data exposed to an MCP client

A connected client can access paper metadata, translation source chunks, job status, per-chunk translation state, and generated artifact metadata. It can create jobs, save translations, and finalize eligible jobs. Tools do not accept arbitrary filesystem paths, but they operate on jobs below the configured PaperTrans output root.

Only connect clients that you trust with those papers and translations. PaperTrans does not independently verify the identity shown by an external tunnel or connector configuration.

## Start locally

```bash
uv sync --extra mcp
.venv/bin/papertrans-mcp \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

The endpoint is `http://127.0.0.1:8000/mcp`. Configuration can also be supplied through `PAPERTRANS_REPO_ROOT`, `PAPERTRANS_OUTPUT_ROOT`, `PAPERTRANS_MCP_HOST`, `PAPERTRANS_MCP_PORT`, and `PAPERTRANS_MCP_TRANSPORT`.

The Web UI checks only whether the local MCP port is listening. It cannot verify a tunnel or an external client connection.

## Connect a client

A local MCP client can connect directly to `http://127.0.0.1:8000/mcp`. ChatGPT connects through OpenAI Secure MCP Tunnel; the local server itself remains on loopback. Follow the bilingual [MCP client setup guide](mcp-client-setup.md) for both paths.

PaperTrans cannot start a client conversation. After preparing a job in the Web UI, copy its worker request and send it to the connected client.

## Exposed tools

- `prepare_arxiv_translation`: creates or resumes a job from official arXiv HTML.
- `list_translation_jobs`: lists persisted jobs for recovery.
- `get_translation_status`: reads chunk and artifact status.
- `get_translation_chunk`: returns the next bounded translation payload.
- `save_translation_chunk`: validates identity and protected nodes before an atomic save.
- `finalize_translation_html`: refuses incomplete jobs, renders sibling HTML and Markdown artifacts, and runs format-specific QA.

Only job preparation accesses the open internet. Job IDs are restricted to a safe filename character set, and all writes stay below the configured output root.

## Final artifacts

A successful `finalize_translation_html` call writes the following files below `output/<job-id>/html/`:

- `index.html` and `qa.json`
- `index.md` and `markdown-qa.json`
- the normalized `document.json`, source-routing metadata, and localized `assets/`

The existing `output/<job-id>/<job-id>-html.zip` bundle contains both rendered formats, both QA reports, and their supporting files. Finalization succeeds only after the required artifacts and QA checks are complete.

## Current limits

- v1 supports official arXiv HTML only; ar5iv, LaTeXML, and PDF fallback remain future or experimental paths.
- The server has no production authentication layer. Do not expose it directly to the public internet.
- External clients do not report token usage through these tools, so PaperTrans cannot reliably record it.
- A client session may stop between chunks. Jobs persist and can be resumed with `list_translation_jobs` and `get_translation_status`.
