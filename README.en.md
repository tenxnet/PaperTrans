<p align="center">
  <img src="docs/assets/papertrans-logo.png" alt="PaperTrans logo" width="160">
</p>

<h1 align="center">PaperTrans</h1>

[日本語](README.md) | English

> **Pre-release preview (v0.1)** — The supported v1 path prepares official arXiv HTML through the local MCP server and uses a connected MCP client to translate it into Japanese. PDF processing and the Codex CLI path remain experimental.

PaperTrans is a local-first academic paper translation workspace. It translates prose into Japanese while preserving document structure, MathML equations, figures, tables, citations, cross-references, identifiers, and bibliography entries.

## What works

- Acquire and sanitize official arXiv HTML from an arXiv ID.
- Split only translatable prose into stable semantic units.
- Preserve equations, figures, tables, citation links, DOIs, and protected terms.
- Use a connected MCP client as the translation worker.
- Validate block identity and protected tokens, then persist the normalized DocumentIR as the artifact source of truth.
- Generate sibling HTML and Markdown from that same DocumentIR and include both format-specific QA results in the ZIP.
- Manage search, tags, unread state, and favorites in a local library.
- Read papers inside the app with a navigable section outline.

Papers, translations, and library state stay on the local machine and are excluded from Git by default.

## Quick start

### Requirements

- macOS or Linux
- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and pnpm 11

```bash
git clone https://github.com/tenxnet/PaperTrans.git
cd PaperTrans
uv sync --extra mcp
pnpm install --frozen-lockfile
```

Start the local MCP server:

```bash
.venv/bin/papertrans-mcp --host 127.0.0.1 --port 8000
```

Start the web app in another terminal:

```bash
pnpm dev --hostname 127.0.0.1
```

Before creating the first job, connect a client with the [MCP client setup guide](docs/mcp-client-setup.md). A local MCP client can use the URL above directly; ChatGPT uses Secure MCP Tunnel.

Open `http://127.0.0.1:3000` and register an arXiv ID under **New translation**. Copy the displayed **Worker request** into the connected client; PaperTrans then persists progress and artifacts. Add `--port 3100` to the web command if you need a different port.

A completed job stores `html/index.html` and `html/index.md` as sibling artifacts, with format-specific checks in `html/qa.json` and `html/markdown-qa.json`. The download ZIP contains both formats and their local assets.

## Choose an MCP client

| Client | Connection | Tunnel |
| --- | --- | --- |
| Local MCP client | Connect directly to `http://127.0.0.1:8000/mcp` | Not required |
| ChatGPT | Connect through OpenAI Secure MCP Tunnel | Required |

The Web UI manages job preparation, progress, and artifacts. Model selection and usage limits belong to the MCP client.

## Scope

- Official arXiv HTML is the supported v1 input.
- ar5iv, LaTeXML, general PDF parsing, and PDF OCR are future or experimental paths.
- Japanese is the only v1 translation target. The web UI itself supports Japanese and English.
- The web app and MCP server are local, single-user tools.
- Public artifact hosting and collaboration are out of scope.

## Roadmap (planned or under consideration)

Priorities and specifications may change as experiments and issues provide new evidence.

- [ ] Fall back to ar5iv or LaTeXML when official arXiv HTML is unavailable.
- [ ] Preserve structure, equations, figures, tables, and citations in general PDFs such as IEEE papers.
- [ ] Add glossary editing, per-paper rules, and section-level retranslation.
- [ ] Add navigable links between prose, citations, references, figures, and tables.
- [ ] Add translation parallelism, caching, and processing-time or usage metrics.
- [ ] Improve library management with folders, full-text search, and batch actions.
- [ ] Support translation targets beyond Japanese and optional external translation providers.

## Local data and safety

- `data/`, `output/`, and `.env*` are excluded from Git.
- Bind the web app and MCP server to `127.0.0.1`.
- Never expose the unauthenticated MCP server directly to the public internet.
- You are responsible for checking the source paper's license and applicable law before using or sharing a translation.
- Outputs are AI/MCP-generated machine translations. Translation and structural QA can be wrong, so always verify the source paper before research use or citation.
- To avoid unnecessary load on arXiv, acquire one paper at a time and leave a reasonable interval between consecutive requests. Reuse an already prepared job for the same paper.

## Documentation

- [MCP client setup](docs/mcp-client-setup.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Documentation index](docs/README.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Development and validation

```bash
.venv/bin/pytest -q
pnpm typecheck
pnpm build
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Do not attach paper PDFs, generated translations, API keys, or other secrets to issues or pull requests.

## License

PaperTrans source code, repository Skills, templates, and documentation are provided under the [Apache License 2.0](LICENSE). This license does not automatically apply to papers acquired by users, figures and tables contained in those papers, or generated translation artifacts.
