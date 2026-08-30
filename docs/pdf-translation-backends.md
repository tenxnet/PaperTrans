# PDF translation backends: isolation and evaluation contract

Status: design proposal, not an implemented or supported PaperTrans path.

This document defines how to evaluate a layout-preserving PDF translation backend without making it part of PaperTrans's trusted process. The supported v1 path remains official arXiv HTML. PDF input, backend output, and backend logs are all untrusted.

## Decision summary

- Do not install or run the published `pdf2zh-next==2.9.0` dependency set. It resolves to a BabelDOC version affected by a high-severity arbitrary-code-execution advisory.
- If the BabelDOC family is evaluated, build an auditable PaperTrans fork of the pdf2zh-next adapter around a patched BabelDOC release, run one job per locked container, and expose only the narrow contract below.
- Evaluate `harumi` through the same contract. It is the preferred permissive candidate, but it is not accepted merely because its license is permissive.
- Never write a candidate result into `output/<slug>/html/` or replace an existing MCP/arXiv artifact. Candidate runs remain under `output/<slug>/pdf-runs/` until a separate promotion decision.
- Isolation is a security control, not a way to avoid AGPL obligations.

## Repository fit

PaperTrans is a local-first, single-user application. A PDF imported by the current application is stored at `data/papers/<slug>/source.pdf`, while generated state belongs under `output/<slug>/`. The backend supervisor should preserve that division:

- PaperTrans owns the source digest, job state, artifact index, validation, and promotion decision.
- A backend receives one read-only copy of one PDF and one constrained request.
- A backend writes only to a fresh per-run staging directory.
- Existing arXiv manifests, `work/`, `html/`, and HTML bundles are not backend inputs and are never mounted into a backend container.
- The current PDF library summary remains `work/papertrans-job.json` with `sourceType: "pdf"`. Candidate runs do not rewrite it. A later promotion may atomically map one validated `translated_mono_pdf` role to its `artifacts.translatedPdf` field.
- The browser and the Next.js request handler do not connect to a backend directly. A persistent local supervisor enqueues work and returns `202 Accepted`; it does not keep correctness-critical state only in an in-memory `Set`.

The first implementation should use the process contract. The HTTP contract is for a later private service or a non-local worker and must produce the same events and artifacts.

## Candidate versions and hard stops

The baseline below was checked on 2026-08-30. Every production build must use an immutable lockfile, image digest, and software bill of materials; ranges are policy checks, not installation instructions.

| Candidate | Evaluation baseline | License | Current decision |
| --- | --- | --- | --- |
| pdf2zh-next / BabelDOC | PaperTrans fork from pdf2zh-next `v2.9.0`; `BabelDOC==0.6.4`; an exact tested PyMuPDF `>=1.26.7`; Python `>=3.10,<3.14` | pdf2zh-next and BabelDOC: AGPL-3.0; PyMuPDF: AGPL-3.0 or commercial | Blocked until the fork passes dependency, security, regression, and legal gates |
| harumi | `harumi==1.19.0`, `harumi-ai==0.9.0`, Rust `1.88`, committed `Cargo.lock`, exact font hash | MIT OR Apache-2.0; font and optional tools separately licensed | Eligible for the parallel digital-PDF evaluation |
| Docling plus a writer | Repository lock `docling==2.123.0` and exact model revisions, with harumi as the PDF writer | Docling code: MIT; model licenses vary | Parser/OCR experiment only; Docling does not produce a translated PDF itself |

### Why the upstream pdf2zh-next release is blocked

The published [`pdf2zh-next` 2.9.0 metadata](https://raw.githubusercontent.com/PDFMathTranslate-next/PDFMathTranslate-next/v2.9.0/pyproject.toml) requires both `pymupdf<1.25.3` and `babeldoc>=0.6.2,<0.7.0`. BabelDOC 0.6.2 accepts PyMuPDF `>=1.25.1`, so it is the compatible resolution. The [BabelDOC advisory](https://github.com/funstory-ai/BabelDOC/security/advisories/GHSA-m8gf-v64p-gfmg) marks every version through 0.6.2 as affected by arbitrary code execution during CMap pickle deserialization and marks 0.6.3 as the first patched version.

The patched [BabelDOC 0.6.3](https://raw.githubusercontent.com/funstory-ai/BabelDOC/v0.6.3/pyproject.toml) and current [0.6.4](https://raw.githubusercontent.com/funstory-ai/BabelDOC/v0.6.4/pyproject.toml) require `pymupdf>=1.26.7`, which conflicts with pdf2zh-next 2.9.0. There is therefore no safe, satisfiable upstream 2.9.0 dependency set. An unlocked install is not an acceptable workaround.

The original [`pdf2zh` 1.9.11 metadata](https://raw.githubusercontent.com/PDFMathTranslate/PDFMathTranslate/v1.9.11/pyproject.toml) constrains BabelDOC to `<0.3.0`; that experimental BabelDOC route is also inside the affected range and must not be enabled. A mocked health response for an affected version may be used in contract tests, but an affected package must not be installed to test the rejection logic.

The safer experiment is an exact-source fork of the pdf2zh-next reference integration with the upper PyMuPDF constraint removed, BabelDOC pinned to 0.6.4, and an exact PyMuPDF version selected by regression testing. The fork revision, patch, dependency lock, container recipe, models, and fonts must be recorded and, where required by AGPL, published as Corresponding Source. Passing the known advisory gate does not establish that the stack has no other vulnerabilities.

The adapter should use pdf2zh-next's documented [`do_translate_async_stream`](https://raw.githubusercontent.com/PDFMathTranslate-next/PDFMathTranslate-next/main/docs/en/advanced/API/python.md) boundary rather than BabelDOC internals. BabelDOC explicitly treats direct APIs as internal and unsupported in its [README](https://raw.githubusercontent.com/funstory-ai/BabelDOC/main/README.md). The adapter must account for the current high-level implementation's approximately 30-minute subprocess callback timeout; either the fork makes it explicit and configurable or the supervisor deadline remains below it.

### Harumi evaluation profile

The [harumi repository](https://github.com/kent-tokyo/harumi) identifies `harumi` 1.19.0 and `harumi-ai` 0.9.0 as its current pair. Harumi offers positioned text extraction, CJK font embedding, overlay/in-place replacement, collision checks, and page quality reports. Its [`Translator` trait](https://docs.rs/harumi-ai/latest/harumi_ai/trait.Translator.html) also provides a clean adapter point for the same translation-provider profile used by the other candidate.

Phase 1 is digital PDF only and uses:

- overlay mode, because the official docs recommend it for tables and multi-column pages;
- explicit source language `en` and target language `ja`;
- math skipping enabled and protected-token checks performed by PaperTrans;
- geometry-only layout repair and no vision provider, so an undeclared Poppler runtime is not pulled into the permissive profile;
- an exact Noto CJK Japanese font file, hash, and OFL notice;
- normalized export of `TranslateOutput` quality information into `backend-report.json`.

Harumi is not an OCR engine. Although harumi-ai accepts pre-produced OCR JSON, its roadmap still calls out incomplete direct and multi-page OCR work. Scanned PDFs are an explicit negative/unsupported case in Phase 1, not a silent fallback to digital extraction.

## Trust boundaries

```text
Browser
  -> PaperTrans API / persistent supervisor          trusted application boundary
       -> host-owned request + source SHA-256
       -> fresh input copy (read-only)
       -> one-job backend sandbox                    untrusted execution boundary
            -> allowlisted translation endpoint      external confidentiality boundary
            -> staging output only
       -> independent artifact and PDF validation   trusted decision boundary
       -> immutable candidate run
       -> optional human-reviewed promotion
```

The source PDF, its metadata, embedded files and actions, all text sent to or returned by a translation provider, backend events, logs, reports, and output PDFs are untrusted. The local user, configured data/output roots, supervisor code, and approved build manifests are trusted under PaperTrans's documented single-user model.

The following controls are mandatory for the pdf2zh/BabelDOC candidate and recommended for every native PDF parser:

- One container or sandboxed process per job; non-root UID; read-only root filesystem; `no-new-privileges`; all Linux capabilities dropped; no Docker socket, home directory, repository, `.git`, SSH agent, or general output-root mount.
- One read-only input directory and one fresh isolated read-write staging directory. `/tmp` is a size-limited temporary filesystem. Model and font assets are read-only and identified by digest.
- PoC defaults: 100 MiB input, 300 pages, 500 MiB aggregate output, 4 GiB memory, 4 CPU cores, 256 PIDs, and 25 minutes wall time. The supervisor terminates the process after a ten-second graceful cancellation window. Limits may be lowered after corpus measurements, but not silently raised by a request.
- Models are baked into the image. Network egress is denied except to a configured translation gateway. DNS, IP, TLS name, redirect behavior, request size, concurrency, and retry budget are controlled by that gateway.
- Provider credentials arrive through a short-lived secret file or platform secret, never in argv, request JSON, environment dumps, manifests, events, or retained logs. A provider can receive paper text, so the user must be told which profile is selected.
- Paper text is data, not a prompt instruction. The provider adapter returns plain translated strings with stable cardinality; it cannot request tools, files, URLs, or backend options.
- Encrypted/password-protected PDFs and inputs above a limit fail before translation. Large-page raster budgets are bounded independently of byte and page counts.
- Backend output is never rendered inline or offered as successful until independent validation finishes. Active content such as JavaScript, launch actions, rich media, embedded files, additional actions, or unexpected external actions causes rejection or `needs_review`.
- Logs are structured, size-limited, and redacted. Full extracted paragraphs, provider payloads, tokens, local absolute paths, and stack dumps do not enter user-visible logs.

## Local process contract

The supervisor launches an adapter with an argv array, never a shell command:

```text
papertrans-pdf-worker run
  --request /input/request.json
  --source /input/source.pdf
  --output /output
```

`/output` is a backend-only staging directory. The backend cannot select a host path. The request is strict JSON; unknown fields fail closed.

```json
{
  "schemaVersion": 1,
  "runId": "pdf-babeldoc-01jexample",
  "source": {
    "mediaType": "application/pdf",
    "sha256": "<64 lowercase hex characters>",
    "bytes": 1234567
  },
  "translation": {
    "sourceLanguage": "en",
    "targetLanguage": "ja",
    "profileId": "evaluation-ja-v1",
    "providerId": "openai-compatible-local",
    "modelId": "<recorded model identifier>",
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

The backend may omit an unsupported optional role, such as Harumi's dual PDF, but it must declare that capability in health/provenance data before a run. It may not accept source URLs, arbitrary paths, raw provider keys, raw prompts, arbitrary CLI flags, or arbitrary translator base URLs.

Stdout is newline-delimited JSON. Every event has `schemaVersion`, `runId`, monotonically increasing `sequence`, RFC 3339 `time`, and `type`. Allowed types are `started`, `stage`, `progress`, `warning`, `artifact`, `completed`, and `failed`. A progress event contains a stable stage identifier, `completed`, and `total`; it contains no paper text. Stderr is a redacted diagnostic stream and is not a protocol channel.

Exit codes are stable across adapters:

| Code | Meaning |
| --- | --- |
| 0 | Worker completed and wrote `worker-result.json` atomically |
| 2 | Invalid or unsupported request |
| 3 | Security/version/policy refusal |
| 4 | Translation provider failed or exhausted its retry budget |
| 5 | PDF parse or unsupported-input failure |
| 6 | Resource limit, deadline, or cancellation |
| 70 | Internal adapter error |

Exit zero is necessary but not sufficient for success. The host verifies the result and artifacts before changing the run to `succeeded` or `needs_review`. A malformed event, sequence rollback, digest mismatch, timeout, signal, missing terminal event, or non-zero exit cannot publish artifacts.

## Private HTTP contract

HTTP is an alternative transport for the same request and result model, not an API that is exposed through the browser. Prefer a Unix socket or loopback binding. A non-local deployment requires TLS, authentication, authorization, tenant isolation, request limits, and an explicit data-retention policy.

| Method | Endpoint | Contract |
| --- | --- | --- |
| `GET` | `/v1/health` | Protocol version, backend ID, adapter/engine/dependency versions, build and image digests, capabilities, and policy readiness; no secrets |
| `POST` | `/v1/jobs` | Multipart `request` JSON plus one `source` PDF; `Idempotency-Key` required; returns `202` and a server job ID |
| `GET` | `/v1/jobs/{jobId}` | Current state and the same normalized progress fields as process events |
| `GET` | `/v1/jobs/{jobId}/artifacts` | Final worker result with role-based download links, only after completion |
| `GET` | `/v1/jobs/{jobId}/artifacts/{role}` | Download by an enumerated role; never by a client-supplied path |
| `DELETE` | `/v1/jobs/{jobId}` | Idempotent cancellation |

Job IDs and run IDs must match `^[a-z0-9][a-z0-9-]{0,63}$`. Repeating a request with the same idempotency key and request hash returns the original job and must not issue a second provider call. Reusing the key with different bytes returns `409 Conflict`.

`/v1/health` must fail readiness if an installed component differs from the approved lock. For the BabelDOC candidate it must, at minimum, reject BabelDOC `<=0.6.2`, PyMuPDF `<1.26.7`, an unknown pdf2zh-next fork revision, a mutable image tag without a digest, or a missing SBOM. Tests exercise this with fake health documents, not vulnerable installations.

## Host-owned artifact contract

Each candidate run is immutable after validation:

```text
output/<slug>/
  pdf-runs/<run-id>/
    request.json
    run.json
    events.ndjson
    worker.log
    artifact-index.json
    qa.json
    artifacts/
      translated-mono.pdf
      translated-dual.pdf        # optional capability
      backend-report.json
    <run-id>-pdf.zip
  pdf-backend-comparison.json    # compares runs with the same source/config hashes
```

The source PDF stays at `data/papers/<slug>/source.pdf` and is not duplicated in the distribution bundle by default. Failed and cancelled runs retain only host-authored state and redacted diagnostics; unvalidated staging files are removed or quarantined outside served roots.

`run.json` is the source of truth. Its state machine is:

```text
queued -> running -> validating -> succeeded | needs_review | failed
                   \-> cancelled
```

It records `schemaVersion`, `runId`, `slug`, `sourceType: "pdf"`, source SHA-256 and byte count, target language, backend ID, adapter/engine/dependency versions, source revision, image/build digest, model/profile/prompt/glossary hashes, font/model digests, state, progress, timestamps, sanitized error code, resource metrics, and the hash of `artifact-index.json`. It never stores credentials or absolute local paths. `succeeded` means the candidate is valid for evaluation; it does not mean selected or supported.

The worker writes `worker-result.json` in staging. PaperTrans treats it as untrusted, verifies it, then creates `artifact-index.json`. Every indexed artifact has exactly:

```json
{
  "role": "translated_mono_pdf",
  "path": "artifacts/translated-mono.pdf",
  "mediaType": "application/pdf",
  "sha256": "<64 lowercase hex characters>",
  "bytes": 2345678
}
```

Allowed roles are `translated_mono_pdf`, `translated_dual_pdf`, and `backend_report`. Paths are fixed by role. Absolute paths, `..`, alternate separators, symlinks, devices, FIFOs, sockets, duplicate roles, extra files, and hash/size mismatches fail validation. PDF roles must start with `%PDF-`, pass an independent structural checker such as `qpdf --check`, stay within the output limit, and have an explicit source-to-output page map. A monolingual result preserves page count, page dimensions, and rotation; a bilingual result may differ only as declared by its page map.

`qa.json` is generated by PaperTrans, not copied from the backend. It normalizes:

- structural PDF validation, page mapping, active-content scan, output size, and viewer smoke tests;
- source, translated, skipped, and failed segment counts;
- protected DOI, URL, citation, equation-label, and glossary-term recall;
- overflow, truncation, font shrink, collision, image overlap, and bounding-box drift counts;
- figure/table region image similarity outside translated text masks;
- Japanese glyph coverage and missing-glyph/tofu findings;
- backend-native quality data, clearly labeled as untrusted/supporting evidence;
- manual review status and notes without private paper text.

The bundle contains `artifact-index.json`, `qa.json`, notices, and validated translated PDF artifacts. It does not contain credentials, logs, caches, intermediate extracted text, or the source paper unless the user explicitly requests a private source-inclusive export.

Publication is atomic: the worker writes to a unique staging directory, the host validates every regular file, writes host metadata, fsyncs as appropriate, and renames the complete directory to `pdf-runs/<run-id>`. Nothing under staging is served.

## Parallel evaluation protocol

Both candidates receive identical source bytes, source and target languages, glossary, provider/model, prompt revision, limits, and output intent. The comparison file refuses to compare runs whose source, profile, prompt, glossary, or model hashes differ.

Use two passes:

1. **Deterministic layout pass.** Route both candidates to a local, non-secret translation stub that deterministically returns cardinality-preserving CJK text with controlled expansion. This isolates extraction, mapping, CJK fonts, write-back, and layout behavior without provider variance.
2. **End-to-end translation pass.** Use the same approved provider profile and deterministic settings where supported. Blind the backend identity during manual review. Different segmentation is part of the backend result and must remain visible in coverage metrics.

Functional A/B runs may execute concurrently in separate sandboxes. Performance runs execute one at a time on the same idle machine because concurrent runs would measure resource contention rather than the backend. Cross-backend translation caches are disabled unless the exact cache contents and keying are fixed for both candidates.

The minimum corpus contains ten redistributable digital academic PDFs covering single and two columns, dense equations, tables, figures, footnotes, citations/DOIs, rotated content, CID/Type3 fonts, colored backgrounds, and at least one long paper. Add two scanned PDFs as negative cases in Phase 1: the worker must return a stable `unsupported_input` result before a provider call rather than claiming a successful blank or partial translation. Private or copyrighted test papers do not enter Git.

## Acceptance criteria

All blocker criteria must pass before quality is compared.

### Contract and security blockers

- Strict-schema tests reject unknown options, URLs, paths, invalid digests/language tags/run IDs, secret fields, oversized inputs, and unsupported encrypted PDFs.
- Mocked readiness tests reject BabelDOC `<=0.6.2`, PyMuPDF `<1.26.7`, unapproved revisions or digests, and missing SBOM/lock data. CI and developer setup contain no vulnerable BabelDOC package.
- An immutable build produces an SBOM and passes dependency/license review with no unwaived reachable Critical or High finding. The known CMap advisory is covered by a non-executing regression fixture run only against the patched sandbox.
- Sandbox probes cannot read the repository, home directory, host output root, secret outside the mounted secret file, or Docker socket; cannot write the input mount; and cannot reach a non-allowlisted network endpoint.
- A cancellation, crash, malformed/out-of-order event stream, provider timeout, resource kill, or supervisor restart never produces a successful run or served artifact. Restart recovery results in a truthful terminal/recoverable state.
- Duplicate idempotent requests make at most one provider call. Two simultaneous backend runs cannot see or modify one another's input, staging directory, secrets, logs, or artifacts.
- Artifact tests reject traversal, absolute paths, symlinks, special files, undeclared files, duplicate roles, hash/size mismatch, malformed PDFs, zip bombs, and oversized output.
- A secret sentinel is absent from argv captures, `request.json`, `run.json`, events, errors, logs, reports, bundles, and UI responses.
- The active-content scanner finds no newly introduced JavaScript, launch action, rich media, embedded file, additional action, or unexpected external action. A source that already contains active content is rejected or explicitly quarantined; preservation is not treated as safe.

### Automated functional gates

- Every monolingual PDF passes the structural checker and opens in at least Chromium/PDFium and macOS Preview in a smoke test. Page count, MediaBox/CropBox, and rotation match the source within 0.1 PDF point.
- At least 98% of non-protected source text regions are either translated or explicitly classified as skipped with an allowed reason. Silent omission is zero on the curated gold pages.
- DOI, URL, numbered citation, equation-label, and required glossary-term recall is 100% on the curated invariant set.
- There are zero truncated or overflowed placements, zero major collision/image-overlap findings, zero `ShrunkToMin` placements, and no translated body text below 6 pt. Any exception requires a page-level `needs_review`, not success.
- Figure, table, and equation image regions that should remain unchanged have SSIM at least 0.995 on 95% of corpus pages and no critical region below 0.98. Text masks and intended translated regions are excluded from this comparison.
- All Japanese characters used in translated text map through an embedded font. Missing glyphs and visible tofu count are zero on the gold pages.
- With the deterministic provider, a representative 25-page paper completes within 10 minutes, uses at most 4 GiB peak RSS, stays within the process limits, and produces no artifact larger than five times the source size or 500 MiB, whichever is smaller. The benchmark records hardware, OS, cold/warm state, and exact build digests.
- The long-paper case terminates before the configured 25-minute deadline or fails cleanly with `resource_limit`; it never hangs past supervisor cancellation.

### Manual quality gate

Two reviewers inspect backend-blinded output for every gold page and a stratified sample of the remaining pages. The median score must be at least 4/5 for reading order, Japanese readability, equation/citation integrity, and visual fidelity, with zero critical omission, wrong-page placement, unreadable formula, or figure/table obstruction.

Harumi is considered non-inferior when it passes every blocker and automated gate and is no more than five percentage points worse than the BabelDOC candidate on translation coverage, protected-token recall, and reviewer pass rate, with no additional critical defect. If both pass, prefer the permissive path. If only the AGPL candidate passes, adoption still requires the license gate. If neither passes, keep PDF translation experimental and retain official arXiv HTML as the product path.

## License decision gate

This is an engineering gate, not legal advice.

Before any image, service, or binary is distributed or made available over a network, record a decision covering deployment type, recipients/users, source modifications, combined components, build scripts, notices, source-offer location, and retention of third-party copyright notices.

- pdf2zh-next and BabelDOC are AGPL-3.0. PyMuPDF is AGPL-3.0 or available under an Artifex commercial license. A commercial PyMuPDF license does not relicense pdf2zh-next or BabelDOC.
- A separate process, container, or private HTTP hop is not automatically outside AGPL's scope. If the AGPL candidate is selected, legal review must decide the obligations, and the default engineering plan is to publish the complete corresponding adapter/fork source and reproducible build material with notices.
- Harumi's code is MIT OR Apache-2.0, but the translation client, fonts, OCR engine, optional Poppler/vision path, model files, and every transitive dependency still require an inventory. Phase 1 disables the Poppler-dependent vision path and pins an OFL font.
- PaperTrans currently declares PyMuPDF in its base Python dependencies. Choosing Harumi does not make a distributed PaperTrans runtime wholly permissive while that dependency remains. A permissive release profile must move PyMuPDF out of the shipped/runtime graph, replace it with an approved permissive component, or carry an appropriate commercial license.
- Docling code is MIT, but its model licenses are separate and it is a parser, not a layout-preserving translated-PDF renderer. It can feed a future permissive pipeline only after its model and writer dependencies pass the same gate.

No backend is silently selected based on availability. The approved backend ID and license mode are explicit configuration, recorded in every run, and fail closed when the build does not match the approved manifest.

## Implementation order

1. Implement schemas, persistent run state, staging validation, and fake-worker tests without either real backend.
2. Implement the Harumi adapter and deterministic digital-PDF pass first; it has the smaller license boundary and a provider-agnostic translation trait.
3. Build the patched pdf2zh-next/BabelDOC candidate only after source, dependency, container, security, and AGPL decisions are recorded.
4. Run the two-pass corpus evaluation and publish `pdf-backend-comparison.json` with hashes and blinded review results.
5. Add UI selection or promotion only after a backend passes all gates. Keep source acquisition, backend execution, artifact validation, and publication as separate states.

## Primary sources

- [pdf2zh v1 repository](https://github.com/PDFMathTranslate/PDFMathTranslate)
- [pdf2zh-next repository](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)
- [pdf2zh-next Python API](https://raw.githubusercontent.com/PDFMathTranslate-next/PDFMathTranslate-next/main/docs/en/advanced/API/python.md)
- [pdf2zh-next Docker documentation](https://raw.githubusercontent.com/PDFMathTranslate-next/PDFMathTranslate-next/main/docs/en/getting-started/INSTALLATION_docker.md)
- [BabelDOC repository and API support statement](https://github.com/funstory-ai/BabelDOC)
- [BabelDOC CMap deserialization advisory](https://github.com/funstory-ai/BabelDOC/security/advisories/GHSA-m8gf-v64p-gfmg)
- [harumi repository](https://github.com/kent-tokyo/harumi)
- [harumi 1.19.0 API documentation](https://docs.rs/harumi/latest/harumi/)
- [harumi-ai 0.9.0 translation options](https://docs.rs/harumi-ai/latest/harumi_ai/struct.TranslateOptions.html)
- [Docling supported formats](https://docling-project.github.io/docling/usage/supported_formats/)
- [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)
- [GNU Affero General Public License v3](https://www.gnu.org/licenses/agpl-3.0.html)
