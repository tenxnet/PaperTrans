# Corresponding Source availability notice

The PaperTrans-specific adapter in `worker/` is licensed under Apache-2.0.
The bundled/forked pdf2zh-next engine and BabelDOC are licensed under
AGPL-3.0, and PyMuPDF is available under AGPL-3.0 or a commercial license.
Their copyright notices and license terms remain controlling.

The build now follows the AGPL source-availability path as its engineering
default. `source-artifacts.lock` pins the official BabelDOC 0.6.4 and PyMuPDF
1.26.7 sdists plus the MuPDF 1.26.12 source archive required by PyMuPDF's own
build script. `scripts/fetch_source_artifacts.py` checks their URLs, sizes,
SHA-256 digests, archive boundaries, license files, and the Linux wheel hashes
in `requirements.lock`; the image build fails if any check differs. The final
image contains those archives and their generated manifest under
`/opt/papertrans/corresponding-source/upstream-archives/`. It also contains the
adapter source, Dockerfile, build scripts, locks, patch, legal texts, and the
patched pdf2zh-next tree. Its root mirrors the original `workers/babeldoc`
build context (`Dockerfile`, `.dockerignore`, `patches/`, `scripts/`, and
`worker/`), so the recorded recipe does not refer to paths absent from the
extracted bundle. `/opt/papertrans/corresponding-source.manifest.json` hashes
every file in that complete directory. Build provenance binds that manifest,
and readiness recomputes the complete tree and directly rechecks each source
archive against `source-artifacts.lock` before importing the engine.

The runtime dependency install is constrained to the audited BabelDOC and
PyMuPDF wheels. Every installed file is verified against the wheel's `RECORD`,
and pip's install report is checked against the exact wheel URL and SHA-256 for
the build architecture. The verifier compares all 350 BabelDOC payload files
to its sdist and the ten directly carried PyMuPDF Python files to its sdist,
then checks PyMuPDF's generated build metadata for source commit
`8264a4b3798d06ec44af2e0e9d2a13abbc94e97d`, MuPDF 1.26.12, and SWIG 4.4.0.
Its deterministic result is stored as
`corresponding-source/runtime-source-map.json` before the complete directory
manifest is generated.

**Distribution gate:** this evaluation image has not completed a public-image
release gate. Do not publish or transfer it until the release record verifies
the source bundle extracted from the final image, selects and approves either
the documented AGPL path or an Artifex commercial-license path for PyMuPDF,
records the immutable PaperTrans revision in the OCI image labels, and closes
the separate baked-asset license/provenance gate in `THIRD_PARTY_NOTICES.md`.
The files and generated SBOM in a locally built image are audit inputs, not by
themselves a legal conclusion or evidence that final-release obligations have
been completed.

The mapping check is not a bit-for-bit reproducible-build attestation for
PyMuPDF's generated wrappers or native objects. The final image gate must
retain an independent build/review of those native inputs (or the approved
commercial-license record); PyPI currently supplies no provenance attestation
for the locked PyMuPDF wheels or sdist.

The PyMuPDF sdist's package-level AGPL/commercial declaration conflicts with a
`GPL-3.0-only` SPDX header in `src/__init__.py`; see `THIRD_PARTY_NOTICES.md`.
The source bundle deliberately preserves both. Source availability alone does
not settle that upstream licensing discrepancy.

For every PaperTrans BabelDOC worker image that passes those distribution
gates, the exact patched engine source and build/install information must be made
available without charge in all of these forms:

1. inside the image at `/opt/papertrans/corresponding-source/`; and
2. as release assets served next to the exact image release for no additional
   charge, together with the complete directory manifest; and
3. with its reproducible recipe in the matching PaperTrans repository revision
   under `workers/babeldoc/`, including the upstream revision, patch,
   dependency locks, Dockerfile, manifests, SBOM instructions, and adapter
   source.

The embedded source set is:

| Installed component | Preferred-form source | Locked SHA-256 |
| --- | --- | --- |
| BabelDOC 0.6.4 | `babeldoc-0.6.4.tar.gz` | `dbd2a69ccaf6678c34089f8c422a38a0fa170f5fa88ee1313b4235103421a875` |
| PyMuPDF 1.26.7 | `pymupdf-1.26.7.tar.gz` | `71add8bdc8eb1aaa207c69a13400693f06ad9b927bea976f5d5ab9df0bb489c3` |
| MuPDF 1.26.12 used by PyMuPDF | `mupdf-1.26.12-source.tar.gz` | `6baf910928f404167ba49be6340195dec340795724722b331f5a2143f5aa0d01` |

The patched pdf2zh-next source tree, patch, exact dependency locks, Dockerfile,
`.dockerignore`, build scripts, and adapter source complete the recorded build
inputs. Run the source fetcher against an empty output directory to reproduce
and verify the three archives:

```sh
uv run --frozen python workers/babeldoc/scripts/fetch_source_artifacts.py \
  --lock workers/babeldoc/source-artifacts.lock \
  --requirements-lock workers/babeldoc/requirements.lock \
  --upstream-lock workers/babeldoc/UPSTREAM.lock \
  --output /path/to/empty/source-output
```

After extracting the two `/opt/papertrans/corresponding-source*` paths from the
final image, verify the complete tree before uploading the matching release
assets:

```sh
python3 corresponding-source/scripts/verify_tree_manifest.py \
  --root corresponding-source \
  --manifest corresponding-source.manifest.json \
  --revision f8dffcf4c3a33b254391d43514439b975ce8d966
```

The extracted `corresponding-source/` directory is also a valid build context
for its included Dockerfile. A release reproduction must provide the verified
PaperTrans commit rather than accepting the `unreleased` label default:

```sh
docker buildx build \
  --build-arg PAPERTRANS_REVISION=<verified-full-PaperTrans-commit> \
  --load \
  corresponding-source
```

That build still downloads only hash-accepted upstream inputs and baked assets;
it is not an offline-build claim. Retain the embedded archives independently so
source availability does not depend on those upstream download services.

The public project location is
<https://github.com/tenxnet/PaperTrans>. Use the immutable PaperTrans revision
recorded with the image release; a mutable default branch or an upstream URL
alone is not a substitute for retained matching source. If a distributed copy
lacks its matching source,
request it through that repository's private security/contact channel. This
availability commitment is intended to remain valid for at least three years
after the corresponding image distribution.

Any downstream distributor or network operator following the AGPL path must
preserve the notices, publish its own exact modified Corresponding Source, and
provide remote users a prominent source link when AGPL section 13 applies. Do
not remove the embedded source or bypass its integrity checks. This notice
describes the project's source-availability procedure and is not legal advice.
