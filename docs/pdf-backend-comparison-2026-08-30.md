# PDF backend comparison checkpoint — 2026-08-30

This checkpoint records what was actually executed. It does not select a
translated-PDF backend.

## Result

| Backend | Evidence | Outcome | Promotion |
| --- | --- | --- | --- |
| Harumi 1.19.0 / harumi-ai 0.9.0 | arXiv 1409.1556, 14 pages, deterministic Japanese layout placeholders | Failed: generated PDF has missing font/ExtGState resources; MuPDF reports 754 parser/render warnings. Harumi also reports 893 unresolved issues, 299 collisions, 580 overflows, and 594 shrunk regions. | Prohibited by host policy (`layout_evaluation`, `promotionEligible: false`) |
| Patched pdf2zh-next / BabelDOC 0.6.4 | exact-source fork, dependency locks, embedded SBOM/source-offer material, 38 adapter tests, built image, exact isolated health, one-page deterministic-gateway dual E2E | Image, health, engine wiring, adjacent dual-page mapping, candidate publication, and render smoke gates passed. A semantic-provider run is not yet complete. | Blocked pending sandboxed host validation, real-gateway review, qpdf, semantic/layout/manual QA, corpus comparison, and AGPL/legal gate |
| Docling 2.123.0 | ten-paper parser PoC | Parser/semantic artifact path passed its release gate; Docling does not itself write translated PDFs. | Not applicable |

## Harumi artifact identity

- Source: `output/harumi-1409-eval/html/source.pdf`
- Run: `pdf-harumi-1409-layout-v5`
- Candidate PDF SHA-256:
  `1131fe0d2fc422cb85fd241f489755c24c6c831180adc91696068fa99f7d1fcd`
- Artifact-index SHA-256:
  `9703a50f1bcd907149db911a2a5641237ac869ef72ef9053d44ceebf21048c8e`
- Worker image:
  `papertrans-harumi@sha256:a185f358702abfd63b2fae67268e983ac569b97f2f0665ddf2b1fe90996e720a`
- Font SHA-256:
  `c2f3b4d463500a2ddcd3849cded1fceeb9fd6d1c32e6cbecd568453ba50fc68f`

The historical run was published before MuPDF warning capture was added and
therefore has `qa.status: passed`. It is retained unchanged as immutable
negative evidence. Revalidating the same bytes with the current host returns
`render_smoke_failed` and prevents publication. It must not be copied into the
shared manifest or shown in the Web library as a translated paper.

The arXiv 2405.20947 source was also tested as a candidate input and correctly
rejected before translation because it contains `/OpenAction`.

## BabelDOC provenance

- pdf2zh-next upstream commit:
  `f8dffcf4c3a33b254391d43514439b975ce8d966`
- PaperTrans fork version: `2.9.0+papertrans.1`
- Adapter version: `0.1.1`
- BabelDOC: `0.6.4`
- PyMuPDF: `1.26.7`
- Source lock digest:
  `579c5f301a44224b5bb577c4475fc9527e6ed8bd8c8754855037d2a315065f6b`
- Approved patch digest:
  `80b9d80a57356b7eee9fd7d3fb90333a8c70f499d349d60e461f967b2d634f4f`
- Runtime lock digest:
  `0fc3f23dc5fa0b0ebb239acaf1b5c24a14cd29b41fe48cfd3d1915dfb832366f`
- Evaluated worker image:
  `papertrans-babeldoc@sha256:4f2761829b3f3f191f9e5e4eef1b407d622e9804aeab8a13e6f0d171f15b6905`
- Extracted embedded SBOM SHA-256:
  `8c0f18d500210d5cc26ceb86ac385cd01bdc0bcaf3dc7d8d0b8111721264539f`

The image returned `ready: true` under the supervisor-equivalent health
profile: no network, read-only root, UID/GID 65532, no capabilities,
`no-new-privileges`, seccomp, bounded `/tmp`, isolated config, and separate
bounded BabelDOC/pdf2zh-next cache tmpfs mounts. The 352,451,566 bytes of baked
BabelDOC assets remain immutable in the image; the worker exposes them to the
ephemeral BabelDOC cache through manifest-derived links only after provenance
and sandbox checks pass.

## BabelDOC deterministic E2E

- Controlled source: one page, 738,514 bytes, no equations or active content
- Gateway image:
  `papertrans-deterministic-gateway@sha256:83707cd8dc4367f62830dedc75bb00bbde9dbc6024779505975af679ed56217d`
- Run: `pdf-babeldoc-deterministic-e2e-v4-dual`
- Candidate PDF SHA-256:
  `ab9350bfd72daa22179cb7fdb2e695ee98da9a2e2ee08d78c087a1ccd14fb988`
- Artifact-index SHA-256:
  `6a45c215a6d9c0f5c34e9878aa9607a8acb39ccd291292d539192b8d2041960c`
- QA SHA-256:
  `e457c1d46a5d7b1d4d58b91bc81fe8140326983178a96de863b874a7b9ead141`
- Engine time: 17.51 seconds; 50 upstream events; 23 normalized progress events
- Page map: source page 1 to adjacent output pages `[1, 2]`

The fixed dummy gateway and both of its Docker networks had no external route.
The supervisor staged the dummy credential over interactive stdin, verified
the one-gateway topology, ran the real pinned pdf2zh-next/BabelDOC engine,
copied the quota-backed output, scanned it for credential retention, and
published the common artifact/QA/run bundle. The rendered dual output contains
the original page followed by the fixed Japanese E2E marker page, preserves the
source image on the original page, and does not leak engine placeholder tags.

This proves wiring only. The gateway is not a translation model, the run has
`purpose: contract_evaluation`, `promotionEligible: false`, and
`qa.status: needs_review`; qpdf is unavailable. No real provider credential was
used. Semantic translation, quality scoring, and promotion remain blocked.

Isolation does not remove pdf2zh-next, BabelDOC, or PyMuPDF licensing and
corresponding-source obligations. A distribution or hosted-service decision
requires a separate legal gate.
