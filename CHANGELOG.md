# Changelog

Notable user-visible changes to PaperTrans are recorded here. The project uses
[Semantic Versioning](https://semver.org/) for release tags. Python package
metadata uses the PEP 440 equivalent `0.2.0rc2` for the next release candidate.

## [Unreleased]

## [0.2.0-rc.2] - 2026-09-03

### Added

- First-run library guidance, visible QA review reasons, keyboard-contained dialogs, and mobile access to paper actions, metadata, tags, the table of contents, and persistence feedback.
- Cross-process serialization and atomic persistence for tags, read state, and favorites, with corrupt metadata preserved for manual recovery instead of being overwritten.
- Python package README, project links, classifiers, keywords, and a reproducibly pinned build backend.

### Fixed

- MCP PDF jobs now resolve their source only from the configured custom data root.
- Failed arXiv job preparation now cleans its private staging area and can be retried; concurrent preparation of the same job is serialized before atomic publication.
- Concurrent MCP chunk saves and finalization are serialized per job, use collision-free atomic writes, and repair result and manifest files when a client retries after an interrupted partial commit.
- arXiv input handling now accepts only complete identifiers or official HTTPS arXiv URLs, and acquired HTML must report the requested paper identity and exact requested version.
- Direct Next.js development and production commands now bind to loopback by default.
- The Web server now rejects non-loopback Host headers across pages, artifacts, and APIs, and mutation routes reject cross-origin browser requests and browser-safelisted media types.
- Base Python installs now give an actionable `papertrans[mcp]` installation message instead of a traceback when the optional MCP command is invoked.
- The Docling application dependency now pins security-fixed `transformers 5.10.4`; the vulnerable 5.8.1 path is absent from the lock and path-traversal regression tests cover malicious and legitimate named templates.

### Changed

- The standard Web validation command is now `pnpm test`; CI also audits production Node dependencies, checks `uv.lock`, and runs the Python suite on both 3.10 and 3.12.
- Python wheel and sdist contents are limited to the core package, with built-artifact smoke tests for entry points and license files.
- Experimental Docling dependencies moved from a published wheel extra to a source-checkout-only uv group because upstream macOS metadata cannot express PaperTrans's tested security override.
- Project-owned Apache-2.0 material and separately licensed BabelDOC-derived patch material are distinguished in public license documentation, and the evaluation worker now carries its license texts, notices, source-offer gate, and immutable revision metadata.
- BabelDOC worker SBOM generation now fails closed on missing license metadata, preserves declared license classifiers without guessing SPDX identifiers, and permits only version- and content-hash-verified license overrides.
- Release CI now verifies a clean exact-SHA checkout before and after every job, audits every distinct Python 3.10/3.11/3.12 lock branch, rejects release tags outside `main` history, and the checked-in `main` ruleset policy pins all four required checks to GitHub Actions.

### Known limitations

- PDF parsing targets digitally generated PDFs. OCR for scanned documents is not supported, and complex reading order, equations, or tables may require manual verification.
- PaperTrans produces translated HTML and Markdown; it does not currently produce a supported translated PDF.
- Translation still requires a connected MCP client. ChatGPT connectivity requires separately configuring OpenAI Secure MCP Tunnel; PaperTrans does not create or authorize that tunnel.
- This release is distributed as a source checkout for macOS and Linux and still requires `uv` and Node.js 22 or newer. A signed desktop application, automatic updates, and bundled system runtimes are future work.
- The first setup downloads the Docling layout and table models and can require several GB of free space. Offline mode works only after dependencies are cached and the pinned models have been verified.
- Experimental PDF paths include dependencies with separate code or model license obligations. Review `docs/dependency-licenses.md` before redistribution or hosted use.
- Library state supports one local host and a local filesystem. Shared volumes and network filesystems are unsupported; if a forcibly terminated internal writer leaves updates busy, restart the complete PaperTrans process before retrying.

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
