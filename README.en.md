# PaperTrans

[日本語](README.md) | English

> **Pre-release preview (v0.1)** — The supported v1 input is official arXiv HTML. The ChatGPT MCP worker and PDF processing remain experimental.

PaperTrans is a local-first academic paper translation workspace. It translates prose into Japanese while preserving document structure, MathML equations, figures, tables, citations, cross-references, identifiers, and bibliography entries.

## What works in v0.1

- Acquire and sanitize official arXiv HTML from an arXiv ID.
- Split only translatable prose into stable semantic units.
- Preserve equations, figures, tables, citation links, DOIs, and protected terms.
- Translate with Codex CLI or the experimental ChatGPT Connector.
- Validate block identity and protected tokens before rendering.
- Manage search, tags, unread state, and favorites in a local library.
- Read papers inside the app with a navigable section outline.

Papers, translations, and library state stay on the local machine and are excluded from Git by default.

## Quick start

### Requirements

- macOS or Linux
- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and pnpm 11
- A signed-in Codex CLI when using Codex translation

```bash
git clone https://github.com/tenxnet/PaperTrans.git
cd PaperTrans
uv sync --extra test
pnpm install --frozen-lockfile
```

Translate official arXiv HTML with Codex:

```bash
.venv/bin/papertrans arxiv-html-pipeline 2508.19843 \
  --slug arxiv-2508.19843 \
  --repo-root "$PWD"
```

Start the local web app:

```bash
pnpm dev --hostname 127.0.0.1
```

Open `http://127.0.0.1:3000`. Add `--port 3100` if you need a different port. Completed jobs under `output/` are discovered automatically.

## Translation methods

| Method | v0.1 status | Tunnel | Billing or quota |
| --- | --- | --- | --- |
| Codex CLI | Supported local path | Not required | Codex usage |
| ChatGPT Connector | Experimental | Required | ChatGPT usage |
| OpenAI API | Not implemented | Not required | API usage billing |

Use **Provider settings** in the Web UI sidebar to switch between ChatGPT Connector and Codex CLI. The Codex CLI path generates a command with the selected model and reasoning effort. See [Provider settings](docs/providers.md) for details.

Using ChatGPT as the translation worker requires the local MCP server and a Secure MCP Tunnel. PaperTrans cannot start a ChatGPT conversation directly. See [ChatGPT translation worker](docs/chatgpt-worker.md) for the trust boundary, exposed data, and setup.

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
- Generated translations and structural QA can be wrong. Verify important claims and citations against the source paper.

## Documentation

- [Documentation index](docs/README.md)
- [ChatGPT translation worker](docs/chatgpt-worker.md)
- [Provider settings](docs/providers.md)
- [Experimental PDF pipeline](docs/pdf-pipeline.md)
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
