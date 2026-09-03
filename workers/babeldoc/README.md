# PaperTrans BabelDOC worker (isolated evaluation fork)

This directory is a self-contained, **experimental and fail-closed** worker for
layout-preserving PDF translation. It is not imported by the PaperTrans Web or
Python processes. The host may launch it only as a one-job container through
the process contract below.

The fork is based on pdf2zh-next commit
`f8dffcf4c3a33b254391d43514439b975ce8d966` and is versioned
`2.9.0+papertrans.1`. Its dependency patch pins **BabelDOC 0.6.4** (the first
selected PaperTrans release after the `<=0.6.2` CMap-deserialization issue) and
**PyMuPDF 1.26.7**. That PyMuPDF version is the minimum accepted by BabelDOC
0.6.4 and therefore the narrowest safe constraint change; a full PDF corpus
regression remains a release gate. The published pdf2zh-next 2.9.0 constraints are never
installed by this build. The worker calls only the documented
`pdf2zh_next.high_level.do_translate_async_stream(settings, file)` translation
boundary; it does not call BabelDOC translation internals.

## Status and hard stops

Do not treat this package as production-ready merely because it builds. A run
is refused with exit 3 unless all of these checks pass:

- exact installed versions: pdf2zh-next `2.9.0+papertrans.1`, BabelDOC `0.6.4`,
  PyMuPDF `1.26.7`, Python `3.12.11`, adapter `0.1.1`;
- exact upstream revision, patched source tree, patch, runtime/build/source
  locks, build manifest, internal CycloneDX SBOM, baked BabelDOC asset
  manifest, and build provenance digests;
- host-supplied source/build/image/SBOM/runtime-lock digests that match those
  verified bytes (build and image digests are the same immutable image digest);
- a valid provider profile in `/run/secrets/papertrans-provider.json`, readable
  only by its owner (`0400` or `0600`);
- Linux non-root execution with zero effective capabilities,
  `no-new-privileges`, seccomp filtering, a read-only root filesystem, a
  read-only `/input`, a writable isolated `/output`, and tmpfs mounts for
  `/tmp`, config, and the BabelDOC/pdf2zh-next translation caches.

`health` applies immutable image/source/dependency/SBOM/lock and base sandbox
checks without requiring provider, input, or output mounts. Both cache tmpfs
mounts are still required because the pinned engines initialize SQLite during
import. `run` additionally requires the provider secret, read-only input, and
writable isolated output. The engine API is imported only after all checks for
the selected command pass. A simulated BabelDOC 0.6.2 is covered by the policy
tests; no vulnerable BabelDOC release is installed for testing.

Models, fonts, CMaps, and tiktoken data are downloaded during the image build,
hashed, moved to `/opt/papertrans/assets/babeldoc`, then made read-only. After
all provenance and sandbox gates pass, the worker creates manifest-derived
links from the empty BabelDOC cache tmpfs to those immutable files. The SQLite
cache remains writable and bounded without duplicating or hiding the 352 MB
asset set. Runtime asset download is forbidden. The final container has no
warmup step. Network policy must deny general egress and allow only the
translation gateway recorded in the trusted provider secret.

## Reproducible inputs

- `UPSTREAM.lock` records the git commit, tree, original source-file hashes,
  dependency versions, base-image digest, and lock/patch hashes.
- `patches/0001-papertrans-safe-dependencies.patch` is verified before it is
  applied to the exact source.
- `requirements.lock` and `build-requirements.lock` contain hashes for every
  Python distribution selected for Python 3.12/Linux. They were generated with
  uv 0.11.3; regenerating them is an explicit dependency update, not a routine
  build step.
- `source-artifacts.lock` maps the Linux BabelDOC/PyMuPDF wheels to the exact
  official BabelDOC 0.6.4 and PyMuPDF 1.26.7 sdists and the MuPDF 1.26.12
  source selected by PyMuPDF. The source fetcher validates sizes, hashes,
  archive paths, and license files before embedding all three archives.
- `scripts/verify_installed_source_mapping.py` verifies the installed wheels'
  complete `RECORD`s, pip's observed wheel URLs and hashes, the
  BabelDOC/PyMuPDF wheel-to-sdist payload correspondence, and PyMuPDF's
  recorded MuPDF/SWIG inputs. Its result is covered by the complete
  Corresponding Source manifest.
- `build-manifest.json` is embedded into the image and has a digest compiled
  into the adapter's readiness policy.
- `scripts/generate_runtime_metadata.py` creates the internal CycloneDX 1.6
  SBOM, asset manifest, and provenance document from installed/baked bytes.
- The base image is the multi-platform digest
  `python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7`.

BuildKit still needs network access in the `upstream`/`builder` stages to fetch
the exact git object, hashed wheels, and assets. A release build should run
behind an allowlisting proxy, retain BuildKit provenance, and store the final
image by digest:

```sh
docker buildx build \
  --build-arg PAPERTRANS_REVISION="$(git rev-parse HEAD)" \
  --provenance=mode=max \
  --sbom=true \
  --tag papertrans-babeldoc:2.9.0-papertrans.1 \
  --load \
  workers/babeldoc
```

The embedded SBOM is a deterministic inventory of the Python environment.
`--sbom=true` adds an independent image-level SBOM/attestation. Release gates
must scan both, verify the provenance predicate, record the pushed image digest,
and reject any critical/high issue according to PaperTrans policy. Do not
silently regenerate the locks to make a scanner pass.

The build context includes the AGPL-3.0 and Apache-2.0 texts,
`THIRD_PARTY_NOTICES.md`, and the conditional `SOURCE-OFFER.md`; the final
image stores them below `/opt/papertrans/licenses/`. Exact BabelDOC, PyMuPDF,
and MuPDF source archives are embedded below
`/opt/papertrans/corresponding-source/upstream-archives/`; the complete source
directory also contains the patched engine, adapter source, build recipe,
locks, patch, and legal files in the same layout as this build context, with a
full manifest at
`/opt/papertrans/corresponding-source.manifest.json`. This does not clear the
image for distribution: final-image source extraction/retention, publication
as matching release assets, and the PyMuPDF licensing decision remain explicit
release blockers.

Validate without installing engine dependencies:

```sh
git -C /path/to/exact-upstream apply --check \
  /path/to/PaperTrans/workers/babeldoc/patches/0001-papertrans-safe-dependencies.patch

PYTHONPATH=workers/babeldoc/worker/src \
  uv run --extra test pytest -p no:cacheprovider workers/babeldoc/worker/tests -q
uv run --frozen python workers/babeldoc/scripts/fetch_source_artifacts.py \
  --lock workers/babeldoc/source-artifacts.lock \
  --requirements-lock workers/babeldoc/requirements.lock \
  --upstream-lock workers/babeldoc/UPSTREAM.lock \
  --output /path/to/empty/source-output
python3 /path/to/extracted/corresponding-source/scripts/verify_tree_manifest.py \
  --root /path/to/extracted/corresponding-source \
  --manifest /path/to/extracted/corresponding-source.manifest.json \
  --revision f8dffcf4c3a33b254391d43514439b975ce8d966
docker buildx build --check workers/babeldoc
```

The tests use metadata fakes. They do not install or execute pdf2zh-next,
BabelDOC, or PyMuPDF.

The 2026-08-30 local checkpoint built adapter `0.1.1` as
`papertrans-babeldoc@sha256:4f2761829b3f3f191f9e5e4eef1b407d622e9804aeab8a13e6f0d171f15b6905`,
passed exact isolated health with embedded SBOM SHA-256
`8c0f18d500210d5cc26ceb86ac385cd01bdc0bcaf3dc7d8d0b8111721264539f`,
and completed a controlled one-page dual E2E through
`workers/deterministic-gateway`. Its page map is source page 1 to adjacent
output pages `[1, 2]`. That run proves the container/process/artifact path only;
its fixed marker is not semantic translation and is never promotion-eligible.

## Process contract

The only commands are:

```text
papertrans-pdf-worker health
papertrans-pdf-worker run --request /input/request.json --source /input/source.pdf --output /output
```

Unknown CLI options, JSON fields, duplicate JSON keys, paths, output roles,
profiles, language pairs, or limits fail closed. `/output` must be empty at
start. The request schema is the common PaperTrans schema:

```json
{
  "schemaVersion": 1,
  "runId": "pdf-babeldoc-01jexample",
  "source": {
    "mediaType": "application/pdf",
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "bytes": 1234567
  },
  "translation": {
    "sourceLanguage": "en",
    "targetLanguage": "ja",
    "profileId": "evaluation-ja-v1",
    "providerId": "openai-compatible-local",
    "modelId": "recorded-model-id",
    "promptRevision": "papertrans-pdf-ja-v1",
    "glossarySha256": null
  },
  "outputs": ["translated_mono_pdf", "translated_dual_pdf"],
  "limits": {
    "maxPages": 300,
    "maxOutputBytes": 524288000,
    "deadlineSeconds": 1500
  }
}
```

The provider URL and API key are never accepted in a request, argv, or
environment variable. They come only from the fixed secret file. The mounted
profile must match `profileId`, `providerId`, and `modelId` in the request.
`provider.example.json` documents that separate trusted schema; never commit a
real secret.

`run` writes only newline-delimited JSON to stdout. Every event has
`schemaVersion`, `runId`, monotonically increasing `sequence`, RFC 3339 `time`,
and one of `started`, `stage`, `progress`, `warning`, `artifact`, `completed`,
or `failed`. Progress contains only a normalized stage plus integer
`completed`/`total`; upstream messages, paper text, credentials, absolute paths,
and tracebacks are never emitted. The adapter retains a dedicated protocol fd
before readiness begins and redirects fd 1/2 to `/dev/null` around public-API
imports, native initialization, PDF inspection, and engine execution. This also
applies to inherited child processes, preventing third-party output from
corrupting NDJSON/health JSON or leaking document text. `health` emits exactly
one JSON line.

Successful staging output is:

```text
/output/
  worker-result.json
  artifacts/
    translated-mono.pdf       # if requested
    translated-dual.pdf       # if requested
    backend-report.json
```

Every artifact is regular, non-symlink, contained in staging, SHA-256 indexed,
bounded by the request, and emitted under its fixed common-contract role/path.
`worker-result.json` has exactly the five backend-neutral fields
`schemaVersion`, `runId`, `sourceSha256`, `artifacts`, and `pageMaps`.
Backend-specific versions and upstream revision are kept in
`artifacts/backend-report.json` instead of extending the shared result schema.
The worker verifies PDF magic, parseability, encryption status, and page count.
Mono output must map one-to-one. Dual output uses BabelDOC's alternating-page
mode, so each source page maps to its adjacent original/translated pair
`[2p-1, 2p]`. PaperTrans must still run independent `qpdf`,
active-content, layout, glyph, protected-token, and visual QA before promotion.

Exit codes are: 0 completed; 2 invalid/unsupported request; 3 readiness or
security refusal; 4 provider/engine failure; 5 PDF/input failure; 6 deadline,
resource limit, or cancellation; 70 internal adapter error. Exit zero is never
sufficient without a valid terminal event and host verification.

## Required runtime isolation

Use PaperTrans's host supervisor rather than constructing a raw `docker run`
command. It resolves an immutable image, validates the private one-gateway
topology, compares the live gateway process config with its immutable image,
rejects runtime mounts/devices/host namespaces/custom DNS/published ports, and
copies the provider profile over stdin into an ephemeral Docker
volume as UID/GID 65532 mode `0400`, creates a quota-backed local-driver tmpfs
volume for `/output`, and starts a restricted keeper. Before executing the
worker, it verifies the live `/proc/self/mountinfo` entry for `/output`.

The effective controls are:

```text
--read-only
--user 65532:65532
--cap-drop=ALL
--security-opt=no-new-privileges
--pids-limit=256
--memory=4g
--cpus=4
--tmpfs /tmp:rw,noexec,nosuid,nodev,size=512m,uid=65532,gid=65532,mode=0700
--tmpfs /opt/papertrans/home/.config:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532,mode=0700
--tmpfs /opt/papertrans/home/.cache/babeldoc:rw,noexec,nosuid,nodev,size=128m,uid=65532,gid=65532,mode=0700
--tmpfs /opt/papertrans/home/.cache/pdf2zh_next:rw,noexec,nosuid,nodev,size=128m,uid=65532,gid=65532,mode=0700
--mount type=bind,src=<one-job-input>,dst=/input,readonly
--mount type=volume,src=<quota-tmpfs-output-volume>,dst=/output,volume-nocopy
--mount type=volume,src=<ephemeral-provider-volume>,dst=/run/secrets,volume-nocopy,readonly
--detach --entrypoint /bin/sleep <image-by-digest> infinity
--env PAPERTRANS_WORKER_SOURCE_REVISION=68c5805dfd311f597817643ca0143339e6ba77984a5519b8524a024eea93d9c3
--env PAPERTRANS_WORKER_BUILD_DIGEST=sha256:<resolved-image-digest>
--env PAPERTRANS_WORKER_IMAGE_DIGEST=sha256:<resolved-image-digest>
--env PAPERTRANS_WORKER_SBOM_SHA256=<sha256-of-embedded-sbom.cdx.json>
--env PAPERTRANS_WORKER_LOCK_SHA256=0fc3f23dc5fa0b0ebb239acaf1b5c24a14cd29b41fe48cfd3d1915dfb832366f
```

The output volume is created with
`type=tmpfs,device=tmpfs,o=size=<bounded>,uid=65532,gid=65532,mode=0700,noexec,nosuid,nodev`.
The supervisor then uses shell-free `docker exec --user 65532:65532` with the
fixed `/opt/venv/bin/papertrans-pdf-worker` executable. It copies artifacts
while the keeper remains alive, rejects literal credential retention in every
event and staged file, and deletes the keeper, secret volume, and output volume
in `finally` cleanup. Secret-bearing stderr is never persisted.

The configured translation-cache budget is split between the two engine cache
mounts; `128m` + `128m` above is the default 256 MiB total.

The source revision above is the SHA-256 of `UPSTREAM.lock`, which in turn pins
Git commit `f8dffcf4c3a33b254391d43514439b975ce8d966`, its tree, and original
source-file hashes. The health `forkRevision` is the approved patch SHA-256
`002297dac1447b3ec3e020c4495f7c0b40670677168bdef84d866e6bf296828f`.
Record that value in the host's approved-fork list. Extract and hash the SBOM
from the built image before launch; do not substitute a source-tree estimate.

The asset byte checks do not authorize redistribution. The 182 baked font,
CMap, DocLayout, and tiktoken files still use mutable upstream URLs and have an
open license-notice inventory described in `THIRD_PARTY_NOTICES.md`. Pin those
origins and close that gate in addition to the Corresponding Source and
PyMuPDF gates before publishing an image.

Also use the default seccomp profile, no Docker socket/repository/home mounts,
and a private network whose only permitted destination is the translation
gateway. Keep the supervisor deadline at or below 1500 seconds; the selected
upstream high-level implementation has a longer internal callback timeout.

## Licensing and source

pdf2zh-next, BabelDOC, and PyMuPDF are AGPL/copyleft components (PyMuPDF may
also be available under a commercial license). Isolation is a security control,
not a license exception. Read and preserve `SOURCE-OFFER.md` before distributing
an image or offering this worker over a network. The final image embeds the
patched pdf2zh-next source, patch, upstream lock, and dependency locks under
`/opt/papertrans/corresponding-source/`, and readiness refuses if that source is
missing or altered. Obtain legal review for the intended distribution model.
