# Dependency license audit

This is an engineering inventory, not legal advice. PaperTrans's Apache-2.0 license covers project-owned code, Skills, templates, and documentation; it does not relicense third-party packages, models, papers, or generated translations.

## Repository license boundary

The root Apache-2.0 license applies only to material copyrighted by the
PaperTrans project. In particular, the patch under
`workers/babeldoc/patches/` modifies upstream `pdf2zh-next` code licensed under
AGPL-3.0-only and remains governed by that license. The matching upstream
revision and patch digest are recorded in `workers/babeldoc/UPSTREAM.lock`, and
the source-availability requirements are documented in
`workers/babeldoc/SOURCE-OFFER.md`. The root Apache-2.0 license does not
relicense that patch, the upstream engine, dependencies, model files, source
papers, or generated translations.

Python sdists and wheels intentionally exclude `workers/`. Evaluation workers
are available from the GitHub source tree only, alongside their upstream locks,
source offer, and third-party notices. Do not copy a worker out of that context
without retaining its applicable notices and corresponding-source obligations.

## Audit baseline

- Audited on: 2026-09-03
- Node source: `pnpm-lock.yaml` and 31 installed package manifests under `node_modules/.pnpm`
- Python source: `uv.lock`, `pyproject.toml`, and 42 installed third-party distribution metadata records in `.venv`
- History check: every Git ref, path, and blob in `git rev-list --all`

The Node production-license command could not use pnpm's package index in the current local installation, so package manifests were read directly. Python's source-only Docling dependency group was checked from its locked release metadata; Docling models may have separate licenses.

## Results

The installed Node packages report MIT, Apache-2.0, BSD-3-Clause, ISC, 0BSD, LGPL-3.0-or-later, or CC-BY-4.0 licenses. The non-permissive or attribution-sensitive entries are:

- `@img/sharp-libvips-darwin-arm64` / libvips: LGPL-3.0-or-later, installed as a platform package below Next.js/Sharp.
- `caniuse-lite`: CC-BY-4.0 browser compatibility data.

The installed Python environment is primarily MIT, BSD, Apache-2.0, PSF-2.0, and MIT-0. The material exception is:

- `PyMuPDF 1.28.2`: dual licensed under AGPL-3.0 or an Artifex commercial license. It is used by the experimental PDF pipeline and by the isolated worker that normalizes untrusted arXiv SVG/raster assets to PNG. Distributors and hosted-service operators must evaluate the applicable PyMuPDF license obligations. The rc.2 source archive contains no PyMuPDF wheel, but the source-checkout application declares PyMuPDF as a runtime dependency and `uv` acquires it during setup. A future bundled application, worker-image publication, or hosted service still requires an explicit compliance decision.

The locked source-only `Docling 2.123.0` package reports MIT for its code. Its
exact model-license inventory is recorded below; those declarations are
separate from the Python package license.

No dependency found in the installed v1/MCP environment had missing license metadata. The local `papertrans` editable package itself reports the repository's Apache-2.0 license through `pyproject.toml`; editable-install metadata did not expose that field in the environment scan.

## Security follow-up (2026-09-03)

- `pnpm audit --prod` reported no known production dependency vulnerability.
- Docling was moved into the source-only dependency group and
  `transformers 5.10.4` was selected, removing the previously recorded
  `transformers 5.8.1` advisory from the lock.
- `pip-audit` over the resulting fully provisioned lock branches reported zero
  known vulnerabilities at this checkpoint: 131 dependencies on Python 3.10
  and 125 dependencies on each of Python 3.11 and 3.12.

The earlier 42-distribution license inventory does not cover every transitive
package in today's fully provisioned environment. Generate a lock-derived SBOM
and complete the expanded notice review before publishing bundled applications
or claiming full transitive-license coverage.

## BabelDOC worker SBOM control (2026-09-03)

The worker's locked runtime currently resolves to 130 components. Its image
metadata generator refuses to emit a partial SBOM when a component has no
declared license expression, license value, or license classifier. It preserves
Trove license classifiers as declaration text rather than inferring an SPDX
identifier.

`peewee 4.4.0` is the sole audited metadata override: that exact version is
pinned to the wheel hash in `workers/babeldoc/requirements.lock`, the installed
wheel's MIT license text must match its recorded SHA-256 digest, and the SBOM
records the immutable upstream source plus both hashes. A different version or
license text fails closed. A successful 130-component SBOM is inventory
evidence, not legal clearance for the worker image.

## BabelDOC worker Corresponding Source control (2026-09-03)

The worker now has a hash-locked, fail-closed source acquisition path for every
AGPL component that contributes code to its installed PDF engine:

- BabelDOC 0.6.4 official PyPI sdist, SHA-256
  `dbd2a69ccaf6678c34089f8c422a38a0fa170f5fa88ee1313b4235103421a875`;
- PyMuPDF 1.26.7 official PyPI sdist, SHA-256
  `71add8bdc8eb1aaa207c69a13400693f06ad9b927bea976f5d5ab9df0bb489c3`;
- MuPDF 1.26.12 official source archive selected by that PyMuPDF build,
  SHA-256
  `6baf910928f404167ba49be6340195dec340795724722b331f5a2143f5aa0d01`.

`workers/babeldoc/source-artifacts.lock` also maps the BabelDOC universal wheel
and the PyMuPDF Linux amd64/arm64 wheels back to this source set. The build
requires those two projects to install from binary wheels, verifies every
installed file against the wheel's `RECORD`, and checks pip's observed download
URL and SHA-256 instead of merely recording an architecture-derived expected
value. It compares all 350 BabelDOC package payload files with the sdist
byte-for-byte. For PyMuPDF it compares the ten directly carried Python files,
checks the wheel's generated build record (PyMuPDF source commit
`8264a4b3798d06ec44af2e0e9d2a13abbc94e97d`, MuPDF 1.26.12 source URL, and
SWIG 4.4.0), and requires the expected generated wrappers and native library
names. The deterministic result is included as `runtime-source-map.json` in
the complete source-tree manifest and checked by readiness.

The build also checks the source hashes against `requirements.lock`, validates
each archive and its license file, and embeds the archives beside the patched
pdf2zh-next tree, exact locks, patch, and build scripts. The complete directory
manifest is bound into build provenance and checked by runtime readiness;
readiness also parses the fixed source lock and directly rechecks the three
archive sizes, hashes, and generated license-evidence manifest. A final image
must still be extracted and independently checked by digest, and maintainers
must approve the AGPL or commercial PyMuPDF distribution path before
publishing it. The engineering bundle is not legal clearance.

The PyMuPDF 1.26.7 sdist itself is not internally uniform: package metadata,
the README, build script, bundled AGPL text, and most headers state the
AGPL/commercial licensing path, while `src/__init__.py` declares
`GPL-3.0-only`. The source bundle preserves that evidence unchanged. The
release decision must resolve the discrepancy with Artifex or use documented
commercial terms; PaperTrans must not invent a normalized SPDX conclusion.

The PyMuPDF wheel contains native MuPDF libraries that can statically include
MuPDF's bundled third-party code, while the Python-environment SBOM represents
that native bundle as the PyMuPDF component. The locked MuPDF source archive
retains the upstream license and notice files for all bundled third-party
trees, and `workers/babeldoc/THIRD_PARTY_NOTICES.md` calls out that boundary.
Final image review must retain those files and inspect the independent
image-level SBOM; a Python-only component count is not proof of native-license
completeness.

The wheel's 64-byte `COPYING` file is only its dual-license declaration
(SHA-256
`40e60697600535eabfb5ae05f72829d88cfe8d02dd4792f5a754f6f51dabe55b`),
whereas the locked PyMuPDF and MuPDF source archives carry the full AGPL text
(SHA-256
`57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6`).
Both are preserved as distinct evidence. The generated wrappers and native
objects are not asserted to be byte-reproducible from the retained archives;
PyPI provides no provenance attestation for these PyMuPDF artifacts. A future
image release must perform and retain an independent native-source rebuild
check or document the approved commercial path rather than treating the
mapping record as a reproducible-build attestation.

## BabelDOC baked runtime-asset gate (2026-09-03)

The evaluation image's build-time `warmup()` does more than install Python
packages. It bakes 182 separately downloaded files (352,451,566 bytes): 34
fonts, 146 CMap JSON files, one DocLayout-YOLO ONNX model, and the tiktoken
`o200k_base` table. The metadata generator rejects missing, additional,
symlinked, wrong-size, or wrong-SHA3-256 files against the exact embedded
BabelDOC 0.6.4 inventory before emitting its schema-2 manifest. That manifest
records SHA-256 and SHA3-256 for each file, and runtime readiness recomputes
both hashes and the canonical 182-path inventory digest
`aed82c0c1fe09f09f3dc5307c646e019948992e37ab62c99e780833220bb9320`
before importing the engine. The official sdist's
`embedding_assets_metadata.py`, from which this inventory is loaded, has
SHA-256
`d7aca596e4b73bf0631836a8877a7522e9944cf21fa5f4b582979d7b97b5d316`.
This prevents a mutable font-metadata response from redefining accepted bytes,
but the download URLs still name mutable `main` or `master` branches and the
asset manifest does not record origin revisions or license evidence.

The embedded BabelDOC metadata for the 34 runtime fonts and 146 CMaps matches
the metadata at immutable BabelDOC-Assets commit
[`1eeeec2a63b05fe6fe7a1f45f0955a8fa98793c4`](https://github.com/funstory-ai/BabelDOC-Assets/tree/1eeeec2a63b05fe6fe7a1f45f0955a8fa98793c4).
Its `README.md` has SHA-256
`20b847a06b7d16057f214a3ab1c93f6996b5285e508ded3f60da32ff74d6a8a8`.
That README links each font family to its terms, but the exact GoNotoKurrent
font binaries declare SIL Open Font License 1.1 while the asset README labels
the Go Noto project with the Unlicense. The MaruBuri artifact contains only a
copyright string; its linked upstream terms require the copyright/license
notice when the font is bundled or redistributed. The CMaps derive from Adobe
CMap resources whose retained notice requires reproduction for binary
redistribution.

The exact DocLayout model bytes map to immutable Hugging Face commit
[`ee7c3d744e5c47c58e267044ac825f95abe69653`](https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx/tree/ee7c3d744e5c47c58e267044ac825f95abe69653):
75,324,598 bytes, SHA-256
`fece9af02f618b603ff7921ccec6861d13e7e1f9830e091dfb7e8ad9311e5b21`,
and BabelDOC's expected SHA3-256
`60be061226930524958b5465c8c04af3d7c03bcb0beb66454f5da9f792e3cf2a`.
That exact model card declares Apache-2.0 and has SHA-256
`ef30e30b57e315278703ddd7baf813df88f1594e2f4c0a8b2696a326a9ce8ad8`.
The tiktoken table is 3,613,922 bytes with SHA-256
`446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`
and BabelDOC's expected SHA3-256
`cb04bcda5782cfbbe77f2f991d92c0ea785d9496ef1137c91dfc3c8c324528d6`.
The tiktoken 0.14.0 source revision
[`4e71bbe0c078468e00fefbf94b39849389f346e5`](https://github.com/openai/tiktoken/tree/4e71bbe0c078468e00fefbf94b39849389f346e5)
ships an MIT license whose SHA-256 is
`418cb499b436128d653d79941333a5437b7be2ea9213dcc2f04d15d5d2c51d86`;
an upstream collaborator also states that the repository license covers the
[encoding files](https://github.com/openai/tiktoken/issues/92#issuecomment-1497875652).

This evidence identifies the bytes but does not clear their distribution.
Before publishing the image, replace mutable asset origins with immutable
revision URLs, preserve the exact model card and applicable font/CMap license
texts in the image, resolve the GoNoto declaration mismatch, document the
tiktoken MIT evidence above, and make those items part of a fail-closed
asset-license manifest. The source-only PaperTrans RC does not contain these
baked assets.

## Locked Docling model license evidence (2026-09-03)

PaperTrans's source distribution does not contain these weights. On explicit
setup, `huggingface_hub` retrieves only the allowlisted files directly from
their origin repositories at the revisions and file hashes in
`docling-models.lock.json`. The exact revision model cards declare:

| Locked repository and revision | Files used | Declared license | Immutable model-card evidence |
| --- | --- | --- | --- |
| [`docling-project/docling-layout-heron@8f39ad3…`](https://huggingface.co/docling-project/docling-layout-heron/blob/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8/README.md) | Safetensors model, config, preprocessor config | Apache-2.0 | `README.md` SHA-256 `175700839bc7808eac6af1d0c23e4f483606ab2276fe01122f4093e61a1a65b6` |
| [`docling-project/docling-layout-heron-onnx@40bde044…`](https://huggingface.co/docling-project/docling-layout-heron-onnx/blob/40bde044036bb181c130ddf6c51792187268748f/README.md) | ONNX model, config, preprocessor config | Apache-2.0; identifies the Heron repository above as its base model | `README.md` SHA-256 `48de0f07390f6823a225ce8aab348e7b57408534b56b71f1433ed6280456597f` |
| [`docling-project/docling-models@fc0f2d4…`](https://huggingface.co/docling-project/docling-models/blob/fc0f2d45e2218ea24bce5045f58a389aed16dc23/README.md) | TableFormer fast/accurate weights and configs | CDLA-Permissive-2.0 | `README.md` SHA-256 `d17f233378eff1240b623b36da76ee8b40afcca05d505949713bf03f7e00822a` |

Reproduce the model-card hashes from immutable raw URLs:

```sh
curl -fsSL 'https://huggingface.co/docling-project/docling-layout-heron/resolve/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8/README.md?download=true' | shasum -a 256
curl -fsSL 'https://huggingface.co/docling-project/docling-layout-heron-onnx/resolve/40bde044036bb181c130ddf6c51792187268748f/README.md?download=true' | shasum -a 256
curl -fsSL 'https://huggingface.co/docling-project/docling-models/resolve/fc0f2d45e2218ea24bce5045f58a389aed16dc23/README.md?download=true' | shasum -a 256
```

The evidence is the upstream model cards at those exact commits, not a mutable
repository badge. The
[CDLA-Permissive-2.0 text](https://cdla.dev/permissive-2-0/) section 2.1 permits sharing with or
without modifications only when the agreement text is made available with the
shared data. Therefore, any future PaperTrans binary/image/archive that bundles
the TableFormer weights must include that agreement text. A bundle containing
either Heron form must include the Apache-2.0 text and retain applicable model
card notices. The corresponding exact model cards should also be retained with
the release evidence. These requirements do not currently turn the source-only
RC into a model-weight distribution.

## Maintenance

Repeat the audit whenever either lockfile changes and before a tagged release. Preserve third-party copyright notices when redistributing dependency code or bundled assets, and review model licenses separately from library licenses.
