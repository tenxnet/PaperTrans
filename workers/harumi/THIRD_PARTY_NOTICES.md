# Third-party and license gate

This file records the evidence and unresolved items for the harumi worker. It is not legal advice and is not yet a complete release notice bundle.

## harumi and harumi-ai

- Upstream: <https://github.com/kent-tokyo/harumi>
- Evaluated release commit: `c145a23775f78aaa0727b97bc8c6a4f17e6a6f5b`
- `harumi 1.19.0`: <https://crates.io/crates/harumi/1.19.0>
- `harumi-ai 0.9.0`: <https://crates.io/crates/harumi-ai/0.9.0>
- Declared SPDX expression: `MIT OR Apache-2.0`

The evaluated Git repository and both crates.io package archives declared the SPDX expression but did not include `LICENSE`, `LICENSE-MIT`, `LICENSE-APACHE`, or `NOTICE` files. Before distributing this worker, obtain upstream confirmation and exact license/copyright texts, retain them in the image/source bundle, and update this notice. Do not infer a production redistribution decision from the SPDX field alone.

## Cargo dependency audit

A conservative audit of the upstream `Cargo.lock` closure (including optional OCR/HTTP and target-specific crates) queried 243 package-version records from the crates.io API. No AGPL or mandatory GPL dependency was found. Two `r-efi` versions declared `MIT OR Apache-2.0 OR LGPL-2.1-or-later`, allowing a permissive choice. The current worker metadata inventory contains 136 packages and likewise shows no AGPL or mandatory GPL license; `r-efi 6.0.0` retains a permissive `MIT OR Apache-2.0` option alongside LGPL-2.1-or-later. Regenerate the metadata/SBOM and rerun the license scan after every lockfile change.

## Poppler exclusion

`harumi-ai` can invoke the external Poppler `pdftoppm` executable for vision repair. `pdftoppm` is GPL-2.0-or-later. This worker fixes `LayoutRepairMode::GeometryOnly`, never supplies a `VisionProvider`, and does not install Poppler. Do not add vision repair or Poppler to the image without a separate licensing and distribution review.

## Font and OCR/model assets

No font, OCR model, or translation model is included. A mounted Noto CJK font is generally OFL-1.1 and must carry its exact upstream license. Any future OCR or local translation model is a separate artifact with its own hash, provenance, and license gate; enabling a Rust feature does not license model weights.

## BabelDOC separation

This worker neither imports nor includes BabelDOC/pdf2zh. BabelDOC 0.6.4 is AGPL-3.0. Merely moving BabelDOC into a separate process does not remove its license obligations; any future BabelDOC worker must retain its own corresponding-source and network-use compliance path.
