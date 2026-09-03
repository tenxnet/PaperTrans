# Third-party notices for the BabelDOC evaluation worker

This directory combines a PaperTrans-owned adapter with separately licensed
software. The repository's root Apache-2.0 license does not relicense the
components below.

## pdf2zh-next

- Project: <https://github.com/PDFMathTranslate-next/PDFMathTranslate-next>
- Pinned revision: `f8dffcf4c3a33b254391d43514439b975ce8d966`
- Upstream version: `2.9.0`; PaperTrans-patched version:
  `2.9.0+papertrans.1`
- Upstream metadata names `awwaawwa` as author and maintainer; the package
  module also identifies `Byaidu, awwaawwa` as authors.
- License: GNU Affero General Public License v3.0 only. The full text is in
  [`LICENSE-AGPL-3.0-only`](LICENSE-AGPL-3.0-only).

`UPSTREAM.lock` records the immutable source tree and file digests. The only
PaperTrans modifications are recorded in
`patches/0001-papertrans-safe-dependencies.patch`.

## BabelDOC

- Project: <https://github.com/funstory-ai/BabelDOC>
- Version: `0.6.4`, pinned with hashes in `requirements.lock`
- License: GNU Affero General Public License v3.0. The AGPL text shipped in
  this directory applies according to the package's own licensing terms.
- Preferred-form source: the official `babeldoc-0.6.4.tar.gz` sdist, SHA-256
  `dbd2a69ccaf6678c34089f8c422a38a0fa170f5fa88ee1313b4235103421a875`.

The build verifies every installed file against the wheel's `RECORD` and all
350 installed `babeldoc/` payload files against that sdist byte-for-byte. The
wheel's full AGPL license also matches the sdist license at SHA-256
`afca41723b45e26069f68d485bf906a202f892f90c801d3052f8e6296bb41454`.
PyPI's Sigstore attestations for both exact artifacts bind them to upstream
commit `17480db9df92ddcb37349ce34b312335226e8ec9`; the tagged package tree also
matches the sdist, apart from generated package metadata. Reproduce that check
with:

```sh
uvx pypi-attestations verify pypi \
  --repository https://github.com/funstory-ai/BabelDOC <artifact>
```

The BabelDOC wheel contains separately attributed vendored material. In
particular, `babeldoc/pdfminer/LICENSE` preserves Yusuke Shinyama's MIT notice,
`babeldoc/format/pdf/new_parser/runtime/data/cmap/README.txt` preserves the
Adobe CMap redistribution notice, and `babeldoc/pdfminer/_saslprep.py` carries
an Apache-2.0 header. These files are also retained verbatim in the locked
BabelDOC source archive; downstream repackaging must not strip them.

## PyMuPDF

- Project: <https://pymupdf.readthedocs.io/>
- Version: `1.26.7`, pinned with hashes in `requirements.lock`
- License: GNU Affero General Public License v3.0 or an Artifex commercial
  license, at the distributor's option and subject to the applicable terms.
- Preferred-form binding source: the official `pymupdf-1.26.7.tar.gz` sdist,
  SHA-256
  `71add8bdc8eb1aaa207c69a13400693f06ad9b927bea976f5d5ab9df0bb489c3`.

PyMuPDF 1.26.7's build script fixes its native MuPDF input at 1.26.12. The
Linux wheel also identifies MuPDF 1.26.12 in its shipped version header.
PaperTrans therefore includes the official
`mupdf-1.26.12-source.tar.gz` source archive, including its bundled third-party
source, under SHA-256
`6baf910928f404167ba49be6340195dec340795724722b331f5a2143f5aa0d01`.

For both Linux architectures the build verifies every installed file against
the wheel's `RECORD`, checks pip's observed wheel URL and SHA-256, compares ten
directly carried Python files against the sdist, and validates the wheel's
`_build.py` record. That record has SHA-256
`cbc07581332a1b30f6ae7cb3c20b4d2aa191393a50d6beefdebe72d66593a319`
and names clean PyMuPDF source commit
`8264a4b3798d06ec44af2e0e9d2a13abbc94e97d`, MuPDF 1.26.12, and SWIG 4.4.0.
The generated wrappers and native objects are not claimed to be a
bit-reproducible rebuild, and PyPI supplies no provenance attestation for
these PyMuPDF artifacts.

That MuPDF source archive retains upstream license/notice files for its bundled
third-party trees, including Brotli, curl, Extract, FreeGLUT, FreeType, Gumbo,
HarfBuzz, JBIG2Dec, Little CMS, MuJS, OpenJPEG, Tesseract, Zint, zlib, and
ZXing-C++. The exact PyMuPDF build can statically incorporate such code, so the
complete archive and its notices are part of the matching source assets even
when a Python-only SBOM represents the resulting native library as PyMuPDF.

The PyMuPDF 1.26.7 sdist has an upstream declaration inconsistency that this
notice does not resolve: its package metadata, README, `setup.py`, `COPYING`,
and most source headers describe the AGPL/commercial terms, while
`src/__init__.py` contains `SPDX-License-Identifier: GPL-3.0-only`. PaperTrans
records the package-level declaration without rewriting either upstream
notice. Maintainers must resolve this discrepancy with the licensor or use a
documented commercial-license path before approving image distribution.

The wheel's `COPYING` is a 64-byte dual-license declaration (SHA-256
`40e60697600535eabfb5ae05f72829d88cfe8d02dd4792f5a754f6f51dabe55b`),
not the full license text. The source archives' `COPYING` files provide the
full AGPL text at SHA-256
`57c8ff33c9c0cfc3ef00e650a1cc910d7ee479a8bc509f6c9209a7c2a11399d6`;
the image retains both kinds of evidence without treating them as identical.

No PaperTrans release record currently selects or documents a commercial
PyMuPDF license. The build prepares the source-availability portion of the
AGPL path, but
publishing the evaluation image remains blocked until maintainers approve the
license path and complete the final-image verification in `SOURCE-OFFER.md`
and the root OSS release checklist.

## Baked runtime assets

The image build currently downloads and bakes 182 files (352,451,566 bytes):
34 fonts, 146 Adobe-derived CMap JSON files, one DocLayout-YOLO ONNX model, and
the tiktoken `o200k_base` table. Build-time metadata generation rejects any
path or SHA3-256 drift from BabelDOC 0.6.4's audited 182-file inventory, emits
both SHA-256 and SHA3-256 for each file, and runtime readiness recomputes those
hashes plus canonical inventory digest
`aed82c0c1fe09f09f3dc5307c646e019948992e37ab62c99e780833220bb9320`.
This fixes byte-identity enforcement, but upstream URLs still use mutable
branches and do not carry all applicable notices into the generated cache.

- The font/CMap metadata matches `funstory-ai/BabelDOC-Assets` commit
  `1eeeec2a63b05fe6fe7a1f45f0955a8fa98793c4`. Most font binaries declare
  SIL Open Font License 1.1. MaruBuri uses Naver's separately stated terms.
  The asset repository describes Go Noto as Unlicense, while the exact
  GoNotoKurrent binaries themselves declare OFL-1.1; PaperTrans does not
  normalize that discrepancy.
- The CMap notice retained in the locked BabelDOC sdist attributes Adobe and
  requires the notice, conditions, and disclaimer to accompany binary
  redistribution.
- The exact DocLayout-YOLO model is from Hugging Face revision
  `ee7c3d744e5c47c58e267044ac825f95abe69653`, whose model card declares
  Apache-2.0. The model is 75,324,598 bytes with SHA-256
  `fece9af02f618b603ff7921ccec6861d13e7e1f9830e091dfb7e8ad9311e5b21`.
- The exact tiktoken table has SHA-256
  `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`.
  Tiktoken 0.14.0 source revision
  `4e71bbe0c078468e00fefbf94b39849389f346e5` carries the MIT license, and an
  upstream collaborator states that the repository license covers its encoding
  files. The exact license and that clarification must be preserved as release
  evidence.

These assets have not completed a distribution review. Do not publish the
image until immutable origin revisions and applicable license/notice texts are
embedded in a fail-closed asset-license inventory, the GoNoto declaration
mismatch is resolved, and the final image is verified. Byte hashes establish
identity, not permission to redistribute.

## PaperTrans adapter

The code under `worker/` is PaperTrans-owned and licensed under Apache-2.0.
The full text included with its wheel and container is
[`LICENSE-Apache-2.0`](LICENSE-Apache-2.0).

The generated CycloneDX inventory in an image is the authoritative component
list for that image. This notice is attribution and boundary documentation; it
is not a legal conclusion that a binary or hosted distribution is cleared.
