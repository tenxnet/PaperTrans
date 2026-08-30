# PaperTrans harumi layout-evaluation worker

This isolated proof-of-concept sidecar evaluates harumi's Japanese PDF write-back. It deliberately does **not** claim to translate meaning. Every translated segment is replaced by deterministic Japanese placeholder text beginning with `仮訳`; the backend report records `semanticTranslation: false` and an explicit disclaimer.

Pinned backend:

- `harumi = 1.19.0`
- `harumi-ai = 0.9.0`
- Rust 1.88 / edition 2024

The worker uses `TranslationMode::Overlay`, `QualityProfile::BestEffort`, `LayoutRepairMode::GeometryOnly`, `auto_skip_math(true)`, one page per batch, and one translation batch at a time. Best-effort is deliberate for this non-promotable comparison adapter: it retains Harumi's PDF and complete per-page issue report so PaperTrans can perform independent visual QA. It never configures a vision provider and does not invoke or ship Poppler.

## Evaluated result

The 2026-08-30 run on the 14-page arXiv 1409.1556 paper is a negative result.
Harumi emitted a PDF, but independent rendering found missing font and
ExtGState resource references. MuPDF records 754 parser/render warnings; the
backend report contains 893 unresolved layout issues, 299 collisions, 580
overflows, and 594 shrunk regions. The current host rejects those bytes with
`render_smoke_failed` before publication.

The historical candidate is retained only as immutable comparison evidence.
It is not a semantic translation, is never promotion-eligible, and must not be
shown in the Web library as a translated paper. See
[`../../docs/pdf-backend-comparison-2026-08-30.md`](../../docs/pdf-backend-comparison-2026-08-30.md).

## Commands and shared contract

Health writes one JSON line and exits 0 only when all immutable provenance variables are present and well formed:

```sh
papertrans-harumi-worker health
```

Required health provenance:

- `PAPERTRANS_WORKER_IMAGE_DIGEST=sha256:<64 lowercase hex>`
- `PAPERTRANS_WORKER_BUILD_DIGEST=sha256:<64 lowercase hex>`
- `PAPERTRANS_WORKER_SOURCE_REVISION=<64 lowercase hex>`
- `PAPERTRANS_WORKER_SBOM_SHA256=<64 lowercase hex>`
- `PAPERTRANS_WORKER_LOCK_SHA256=<64 lowercase hex>`
- `PAPERTRANS_HARUMI_FONT_SHA256=<64 lowercase hex>`

Missing or malformed provenance produces the same generic health shape with `ready: false`, a redacted diagnostic on stderr, and policy exit code 3. A run also fails closed when readiness is false.

Run accepts the backend-neutral PaperTrans request, a regular non-symlink PDF, and a fresh output directory:

```sh
papertrans-harumi-worker run \
  --request /input/request.json \
  --source /input/source.pdf \
  --output /output
```

The deterministic Phase 1 request profile is:

```json
{
  "schemaVersion": 1,
  "runId": "pdf-harumi-01",
  "source": {
    "mediaType": "application/pdf",
    "sha256": "<64 lowercase hex>",
    "bytes": 123456
  },
  "translation": {
    "sourceLanguage": "en",
    "targetLanguage": "ja",
    "profileId": "harumi-layout-eval-ja-v1",
    "providerId": "deterministic-local",
    "modelId": "deterministic-layout-v1",
    "promptRevision": "papertrans-pdf-layout-v1",
    "glossarySha256": null
  },
  "outputs": ["translated_mono_pdf"],
  "limits": {
    "maxPages": 300,
    "maxOutputBytes": 524288000,
    "deadlineSeconds": 1500
  }
}
```

Unknown or missing request fields are rejected. The worker verifies the host-owned source byte count and SHA-256, rejects encrypted or textless/scanned PDFs, applies the page/deadline/output budgets, and writes only:

- `artifacts/translated-mono.pdf`
- `artifacts/backend-report.json`
- `worker-result.json`

`worker-result.json` uses the common five-field contract: `schemaVersion`, `runId`, `sourceSha256`, `artifacts`, and `pageMaps`. Its PDF entry has role `translated_mono_pdf` and fixed path `artifacts/translated-mono.pdf`. The backend report is supporting, untrusted QA evidence; PaperTrans still performs independent PDF, hash, page-map, active-content, geometry, and rendering checks.

Run stdout is strict NDJSON only. Events contain the common `schemaVersion`, `runId`, increasing `sequence`, RFC 3339 `time`, and `type` fields, plus only the fields permitted for `started`, `stage`, `progress`, `artifact`, `completed`, or `failed`. Stderr is a redacted diagnostic stream.

Exit codes follow the shared adapter contract: 0 success, 2 invalid request, 3 policy refusal, 4 provider failure, 5 unsupported/PDF failure, 6 resource/deadline failure, and 70 internal error.

## Trusted font

The request cannot choose a font path. The worker reads:

1. `PAPERTRANS_HARUMI_FONT`, or
2. `/assets/NotoSansJP-wght.ttf` by default.

The path must be absolute and point to a regular non-symlink TrueType file no larger than 32 MiB. Harumi's subsetter rejects CFF-flavoured OpenType fonts, so the evaluated asset is Google Fonts' official `NotoSansJP[wght].ttf`, not `NotoSansCJKjp-Regular.otf`. Its actual digest must equal the required `PAPERTRANS_HARUMI_FONT_SHA256` provenance value. Noto fonts are distributed under OFL-1.1; retain the exact asset's license and attribution.

## Container and release notes

Both the Rust 1.88 builder and Debian Bookworm slim runtime are pinned to reviewed multi-arch manifest digests. The build uses `Cargo.lock` plus `--locked`. The image contains neither fonts nor Poppler; mount `/assets` read-only and run with network disabled, a read-only root filesystem, a bounded writable `/output`, and external CPU/memory/PID/time limits.

Before releasing an image:

1. Run `cargo test --locked` and `cargo build --release --locked` in the reviewed Rust 1.88 builder.
2. Generate an SBOM and third-party notice report from the committed lockfile.
3. Verify the supplied build, image, source, SBOM, lock, and font digests.
4. Run the shared PaperTrans host validator and the same ten-paper deterministic corpus against both candidates.
5. Treat this placeholder strategy as layout evaluation only; it is not an end-to-end translation result.

## License gate

Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before distribution. The upstream manifests declare `MIT OR Apache-2.0`, but the evaluated source and crates.io archives did not contain the corresponding license text files. This PoC is suitable for internal evaluation, not a production redistribution decision.
