# Dependency license audit

This is an engineering inventory, not legal advice. PaperTrans's Apache-2.0 license covers project-owned code, Skills, templates, and documentation; it does not relicense third-party packages, models, papers, or generated translations.

## Audit baseline

- Audited on: 2026-08-30
- Node source: `pnpm-lock.yaml` and 31 installed package manifests under `node_modules/.pnpm`
- Python source: `uv.lock`, `pyproject.toml`, and 42 installed third-party distribution metadata records in `.venv`
- History check: every Git ref, path, and blob in `git rev-list --all`

The Node production-license command could not use pnpm's package index in the current local installation, so package manifests were read directly. Python's optional Docling dependency was checked from its locked release metadata; Docling models may have separate licenses.

## Results

The installed Node packages report MIT, Apache-2.0, BSD-3-Clause, ISC, 0BSD, LGPL-3.0-or-later, or CC-BY-4.0 licenses. The non-permissive or attribution-sensitive entries are:

- `@img/sharp-libvips-darwin-arm64` / libvips: LGPL-3.0-or-later, installed as a platform package below Next.js/Sharp.
- `caniuse-lite`: CC-BY-4.0 browser compatibility data.

The installed Python environment is primarily MIT, BSD, Apache-2.0, PSF-2.0, and MIT-0. The material exception is:

- `PyMuPDF 1.28.2`: dual licensed under AGPL-3.0 or an Artifex commercial license. It is used by the experimental PDF pipeline and by conversion of PDF-formatted figure assets. Distributors and hosted-service operators must evaluate the applicable PyMuPDF license obligations. The first tagged release should either retain it with an explicit compliance decision, replace it, or isolate it further as an optional feature.

The locked optional `Docling 2.123.0` package reports MIT for its code. Individual models and model packages used through Docling can have separate licenses and must be checked when that experimental extra is enabled.

No dependency found in the installed v1/MCP environment had missing license metadata. The local `papertrans` editable package itself reports the repository's Apache-2.0 license through `pyproject.toml`; editable-install metadata did not expose that field in the environment scan.

## Maintenance

Repeat the audit whenever either lockfile changes and before a tagged release. Preserve third-party copyright notices when redistributing dependency code or bundled assets, and review model licenses separately from library licenses.
