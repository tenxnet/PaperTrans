# PaperTrans

[日本語](README.md) | English

> **Pre-release preview (v0.1)** — The supported v1 path converts official arXiv HTML into Japanese reading HTML. PDF processing and the ChatGPT MCP worker remain experimental.

PaperTrans is a local-first academic paper translation workspace. It preserves the source document's structure, MathML equations, figures, tables, citations, cross-references, identifiers, and bibliography while translating academic prose into Japanese.

## What works today

- Acquire and sanitize official arXiv HTML.
- Split translatable prose into stable semantic chunks.
- Preserve MathML, figures, tables, citations, links, and protected terminology.
- Translate chunks with Codex CLI or the experimental ChatGPT MCP worker.
- Validate block identity and protected tokens before rendering.
- Read completed papers in a local Next.js library with search, tags, unread state, and favorites.
- Keep papers, translations, and library metadata on the local machine.

## Requirements

- macOS or Linux
- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and pnpm 11
- A signed-in Codex CLI when using the Codex translation path

## Quick start

```bash
git clone https://github.com/tenxnet/PaperTrans.git
cd PaperTrans
uv sync --extra test
pnpm install --frozen-lockfile
pnpm dev --hostname 127.0.0.1
```

Open `http://127.0.0.1:3000`. If that port is occupied, add `--port 3100` to the development command.

Run the official-arXiv-HTML pipeline with Codex:

```bash
.venv/bin/papertrans arxiv-html-pipeline 2508.19843 \
  --slug arxiv-2508.19843 \
  --repo-root "$PWD"
```

The local library discovers completed jobs under `output/`. Papers, generated artifacts, local state, and `.env*` files are intentionally excluded from Git.

## ChatGPT translation worker (experimental)

Run the local MCP server with:

```bash
uv sync --extra chatgpt --extra test
.venv/bin/papertrans-mcp --transport streamable-http --port 8000
```

The web app uses **New translation** as the single entry point for translation requests. It accepts an arXiv ID or an `arxiv.org/abs/...` URL, normalizes the ID, and copies a request for ChatGPT. PaperTrans cannot start a ChatGPT conversation directly, so paste the copied request into a connected ChatGPT conversation to run the worker.

The bottom of the sidebar shows ChatGPT Connector as the default provider and checks whether the local MCP server is listening. **Connection settings** shows the MCP URL and execution model. This check does not verify the Secure MCP Tunnel or the connector registration inside ChatGPT. OpenAI API and `codex exec` are future provider options and are not selectable in the v1 UI.

## Scope and safety

- Official arXiv HTML is the supported v1 source. ar5iv, LaTeXML, and PDF fallbacks are future or experimental paths.
- The web app and MCP server are single-user local tools. They do not provide user authentication or multi-tenant isolation.
- Bind services to `127.0.0.1`. Do not expose the unauthenticated MCP server directly to the public internet.
- You are responsible for checking the source paper's license and applicable law before using or sharing a translation.
- Generated translations and structural QA can be wrong. Verify important claims and citations against the source paper.

## Development

```bash
uv run pytest -q
pnpm typecheck
pnpm build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Do not attach copyrighted paper files, generated translations, credentials, or private local data to issues or pull requests.

The remaining public-release work is tracked in the [OSS release checklist](docs/oss-release-checklist.md).

## License

PaperTrans source code, repository Skills, templates, and documentation are provided under the [Apache License 2.0](LICENSE). This license does not automatically apply to papers acquired by users, figures and tables contained in those papers, or generated translation artifacts.
