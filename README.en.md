# PaperTrans

[日本語](README.md) | English

> **Pre-release preview (v0.1)** — The supported v1 path prepares official arXiv HTML through the local MCP server and uses a connected MCP client to translate it into Japanese. PDF processing and the Codex CLI path remain experimental.

PaperTrans is a local-first academic paper translation workspace. It translates prose into Japanese while preserving document structure, MathML equations, figures, tables, citations, cross-references, identifiers, and bibliography entries.

## What works in v0.1

- Acquire and sanitize official arXiv HTML from an arXiv ID.
- Split only translatable prose into stable semantic units.
- Preserve equations, figures, tables, citation links, DOIs, and protected terms.
- Use a connected MCP client as the translation worker.
- Validate block identity and protected tokens before rendering.
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
uv sync --extra chatgpt --extra test
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

Open `http://127.0.0.1:3000` and register an arXiv ID under **New translation**. PaperTrans prepares official arXiv HTML and stable translation chunks for a connected MCP client. Add `--port 3100` to the web command if you need a different port.

## Translation methods

| Method | v0.1 status | Tunnel | Billing or quota |
| --- | --- | --- | --- |
| MCP translation worker | Supported v0.1 path | Depends on client | MCP client usage |
| Codex CLI | Experimental development path | Not required | Codex usage |
| OpenAI API | Not implemented | Not required | API usage billing |

The Web UI manages MCP status, job preparation, progress, and artifacts. Model selection belongs to the MCP client. ChatGPT connections require a Secure MCP Tunnel. See [MCP translation server](docs/mcp-server.md) for setup and the trust boundary.

## Scope

- Official arXiv HTML is the supported v1 input.
- ar5iv, LaTeXML, general PDF parsing, and PDF OCR are future or experimental paths.
- Japanese is the only v1 translation target. The web UI itself supports Japanese and English.
- The web app and MCP server are local, single-user tools.
- Public artifact hosting and collaboration are out of scope.

## Local data and safety

- `data/`, `output/`, and `.env*` are excluded from Git.
- Bind the web app and MCP server to `127.0.0.1`.
- Never expose the unauthenticated MCP server directly to the public internet.
- You are responsible for checking the source paper's license and applicable law before using or sharing a translation.
- Outputs are AI/MCP-generated machine translations. Translation and structural QA can be wrong, so always verify the source paper before research use or citation.
- To avoid unnecessary load on arXiv, acquire one paper at a time and leave a reasonable interval between consecutive requests. Reuse an already prepared job for the same paper.

## Documentation

- [Documentation index](docs/README.md)
- [MCP translation server](docs/mcp-server.md)
- [Experimental PDF pipeline](docs/pdf-pipeline.md)
- [Dependency license audit](docs/dependency-licenses.md)
- [Security policy](SECURITY.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Contributing](CONTRIBUTING.md)
- [OSS release checklist](docs/oss-release-checklist.md)

## Development and validation

```bash
.venv/bin/pytest -q
pnpm typecheck
pnpm build
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Do not attach paper PDFs, generated translations, API keys, or other secrets to issues or pull requests.

## License

PaperTrans source code, repository Skills, templates, and documentation are provided under the [Apache License 2.0](LICENSE). This license does not automatically apply to papers acquired by users, figures and tables contained in those papers, or generated translation artifacts.
