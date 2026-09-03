<p align="center">
  <img src="docs/assets/papertrans-logo.png" alt="PaperTrans logo" width="160">
</p>

<h1 align="center">PaperTrans</h1>

[日本語](README.md) | English

> **Next release candidate (v0.2.0-rc.2, unpublished)** — Official arXiv HTML remains the stable input path, with digital-PDF import through Docling as an experimental path. Both use a connected MCP client to translate into Japanese. The published `v0.2.0-rc.1` tag will not be moved.

PaperTrans is a local-first academic paper translation workspace. It translates prose into Japanese while preserving document structure, MathML equations, figures, tables, citations, cross-references, identifiers, and bibliography entries.

## What works

- Acquire and sanitize official arXiv HTML from an arXiv ID.
- Split only translatable prose into stable semantic units.
- Preserve equations, figures, tables, citation links, DOIs, and protected terms.
- Use a connected MCP client as the translation worker.
- Import a digital PDF in the web UI, parse it with Docling, and send it through the same translation flow.
- Validate block identity and protected tokens, then persist the normalized DocumentIR as the artifact source of truth.
- Generate sibling HTML and Markdown from that same DocumentIR and include both format-specific QA results in the ZIP.
- Manage search, tags, unread state, and favorites in a local library.
- Read papers inside the app with a navigable section outline.

Papers, translations, and library state stay on the local machine and are excluded from Git by default.

## Quick start

### Requirements

- macOS or Linux
- Git and [uv](https://docs.astral.sh/uv/)
- Node.js 22+ with Corepack or pnpm 11 available

```bash
git clone https://github.com/tenxnet/PaperTrans.git
cd PaperTrans
./papertrans start
```

On the first run, the launcher prepares the locked Python and Node dependencies, downloads and hash-verifies the Docling layout and table models, and builds the web app. It then starts MCP and Web on `127.0.0.1` and opens `http://127.0.0.1:3000`. The first run needs an internet connection, several minutes or more, and several GB of free space. Later runs reuse the verified setup.

Running `./papertrans` without a subcommand is equivalent to `start`. Keep the terminal open while using PaperTrans; press `Ctrl-C` to stop both Web and MCP. This is a source-checkout release for macOS and Linux, not a desktop app or installer.

Common management commands are:

```bash
./papertrans setup                 # Prepare dependencies, models, and the Web build
./papertrans doctor                # Check setup readiness
./papertrans start --no-browser    # Start without opening a browser
./papertrans status                # Probe the running Web and MCP services
```

Use `./papertrans start --offline` when all dependencies are cached and the models have already been verified. To change ports, for example, run `./papertrans start --web-port 3100 --mcp-port 8100`.

Before creating the first translation job, connect a client with the [MCP client setup guide](docs/mcp-client-setup.md). A local MCP client can connect directly to `http://127.0.0.1:8000/mcp`. Using ChatGPT requires separate creation and authorization of OpenAI Secure MCP Tunnel; `./papertrans` does not automate that external setup.

Under **New translation**, register an arXiv ID or a digital PDF up to 50 MB. Send the displayed **Worker request** to the connected client; PaperTrans then persists progress and artifacts.

A completed job stores `html/index.html` and `html/index.md` as sibling artifacts, with format-specific checks in `html/qa.json` and `html/markdown-qa.json`. From the web library, you can read the HTML, download the Markdown, or download a ZIP containing both formats and their local assets.

## Choose an MCP client

| Client | Connection | Tunnel |
| --- | --- | --- |
| Local MCP client | Connect directly to `http://127.0.0.1:8000/mcp` | Not required |
| ChatGPT | Connect through OpenAI Secure MCP Tunnel | Required |

The Web UI manages job preparation, progress, and artifacts. Model selection and usage limits belong to the MCP client.

## Scope

- Official arXiv HTML is the supported v1 input.
- Digital-PDF import through Docling remains experimental. Reading order, equations, and tables can still be reconstructed incorrectly in complex layouts.
- Scanned-PDF OCR, translated-PDF generation, and ar5iv or LaTeXML fallback are unavailable or still under evaluation.
- Japanese is the only v1 translation target. The web UI itself supports Japanese and English.
- The web app and MCP server are local, single-user tools.
- Public artifact hosting and collaboration are out of scope.

## Roadmap (planned or under consideration)

Priorities and specifications may change as experiments and issues provide new evidence.

- [ ] Fall back to ar5iv or LaTeXML when official arXiv HTML is unavailable.
- [ ] Strengthen layout, equation, table, and citation QA for Docling PDF imports, and evaluate OCR support.
- [ ] Evaluate translated-PDF generation through an isolated backend.
- [ ] Add glossary editing, per-paper rules, and section-level retranslation.
- [ ] Add navigable links between prose, citations, references, figures, and tables.
- [ ] Add translation parallelism, caching, and processing-time or usage metrics.
- [ ] Improve library management with folders, full-text search, and batch actions.
- [ ] Support translation targets beyond Japanese and optional external translation providers.
- [ ] Distribute a signed, auto-updating desktop application.

## Local data and safety

- `data/`, `output/`, and `.env*` are excluded from Git.
- Bind the web app and MCP server to `127.0.0.1`.
- The Web server rejects Host headers other than `127.0.0.1`, `localhost`, and `[::1]`. DNS aliases and reverse-proxy exposure require a separate security design including authentication and are unsupported here.
- Never expose the unauthenticated MCP server directly to the public internet.
- You are responsible for checking the source paper's license and applicable law before using or sharing a translation.
- Outputs are AI/MCP-generated machine translations. Translation and structural QA can be wrong, so always verify the source paper before research use or citation.
- To avoid unnecessary load on arXiv, acquire one paper at a time and leave a reasonable interval between consecutive requests. Reuse an already prepared job for the same paper.

## Documentation

- [MCP client setup](docs/mcp-client-setup.md)
- [Updates, backups, and uninstalling](docs/local-data-lifecycle.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Documentation index](docs/README.md)
- [Changelog](CHANGELOG.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Maintainer release runbook](RELEASING.md)

## Development and validation

```bash
uv lock --check
uv run --frozen --extra test --group docling pytest -q
pnpm typecheck
pnpm test
pnpm build
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes. Do not attach paper PDFs, generated translations, API keys, or other secrets to issues or pull requests.

## License

Source code, repository Skills, templates, and documentation copyrighted by the PaperTrans project are provided under the [Apache License 2.0](LICENSE). Separately identified third-party-derived material, including the patches against upstream AGPL code under `workers/babeldoc/patches/`, remains governed by its respective license and is not relicensed by the repository's Apache-2.0 license. See the [dependency license audit](docs/dependency-licenses.md) for dependency and distribution notes. These licenses do not automatically apply to papers acquired by users, figures and tables contained in those papers, or generated translation artifacts.
