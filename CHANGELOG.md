# Changelog

Notable user-visible changes to PaperTrans are recorded here. The project uses
[Semantic Versioning](https://semver.org/) for release tags. Python package
metadata uses the PEP 440 equivalent `0.2.0rc1` for this release candidate.

## [Unreleased]

## [0.2.0-rc.1] - 2026-08-31

### Added

- A macOS/Linux source-checkout launcher with `setup`, `doctor`, `start`, and `status` commands. It prepares locked dependencies and verified Docling models, builds the web app, and supervises loopback-only Web and MCP services.
- Experimental digital-PDF import from the web UI, with local structural parsing through Docling.
- Translation of prepared PDF jobs through the same resumable MCP workflow used by official arXiv HTML.
- Sibling HTML and Markdown artifacts generated from the shared normalized document, with format-specific QA and an offline ZIP bundle.
- Library actions for opening rendered HTML and downloading Markdown or the artifact bundle.
- Isolated evaluation paths for layout-preserving translated-PDF backends. These remain evaluation-only and are not a supported output path.

### Changed

- PDF results now use the common artifact manifest and QA contract before the web library exposes them.
- Official arXiv HTML remains the stable input path; Docling PDF import is explicitly release-candidate functionality.
- arXiv media is decoded in a bounded worker, normalized to PNG, and published only when covered by the acquisition manifest. Artifacts from older renderer generations must be finalized or acquired again before the Web UI exposes their HTML, Markdown, or ZIP.

### Known limitations

- PDF parsing targets digitally generated PDFs. OCR for scanned documents is not supported, and complex reading order, equations, or tables may require manual verification.
- PaperTrans produces translated HTML and Markdown; it does not currently produce a supported translated PDF.
- Translation still requires a connected MCP client. ChatGPT connectivity requires separately configuring OpenAI Secure MCP Tunnel; PaperTrans does not create or authorize that tunnel.
- This release is distributed as a source checkout for macOS and Linux and still requires `uv` and Node.js 22 or newer. A signed desktop application, automatic updates, and bundled system runtimes are future work.
- The first setup downloads the Docling layout and table models and can require several GB of free space. Offline mode works only after dependencies are cached and the pinned models have been verified.
- The experimental PDF paths include dependencies with separate code or model license obligations. Review `docs/dependency-licenses.md` before redistribution or hosted use.

## 0.1 preview (untagged)

### Added

- Local-first preparation and Japanese translation of official arXiv HTML through a connected MCP client.
- Structure-preserving normalization for equations, figures, tables, citations, links, identifiers, and references.
- A local paper library, resumable translation jobs, artifact QA, and HTML/Markdown output.
