# Docling PDF parser PoC — 2026-08-30

This report records one automated parser comparison. It is not a claim of
production PDF support or a semantic-quality score.

## Setup

- Corpus: 10 public digital arXiv PDFs, 216 pages total
- arXiv IDs: `1409.1556`, `1512.03385`, `1608.06993`, `1706.03762`,
  `1810.04805`, `2006.11239`, `2106.09685`, `2112.10752`, `2305.14314`,
  and `2405.20947`
- Runtime: macOS 26.6 arm64, Python 3.12.13
- Parsers: Docling 2.123.0 with ONNX Runtime 1.29.0 on CPU; PyMuPDF 1.28.2
- OCR: disabled
- Execution: one sequential local run with models already downloaded

The command was the repository's `papertrans pdf-benchmark` workflow with a
limit of 10. Source PDFs and parser work directories were kept outside Git.

## Result

Both parsers completed all 10 papers. Docling completed all 216 pages without a
worker crash or partial-success result.

| Metric | PyMuPDF deterministic | Docling |
| --- | ---: | ---: |
| Successful papers | 10/10 | 10/10 |
| Total parser time | 26.238 s | 754.844 s |
| Median time per paper | 2.172 s | 59.724 s |
| Extracted blocks | 6,717 | 5,524 |
| Sections | 276 | 332 |
| Figures | 89 | 99 |
| Tables | 73 | 109 |
| Equations | 138 | 70 |
| Algorithms | 0 | 6 |
| Structure warnings | 55 | 2 |

Docling was about 28.8 times slower by total wall time in this run. The run was
not isolated from other local workloads, so these timings are capacity-planning
signals rather than controlled performance measurements.

## Per-paper timing and structure counts

| arXiv | Pages | PyMuPDF seconds | Docling seconds | PyMuPDF sections | Docling sections |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1409.1556 | 14 | 1.002 | 32.874 | 14 | 24 |
| 1512.03385 | 12 | 1.326 | 46.201 | 16 | 22 |
| 1608.06993 | 9 | 1.048 | 27.089 | 12 | 12 |
| 1706.03762 | 15 | 1.487 | 33.774 | 27 | 30 |
| 1810.04805 | 16 | 1.894 | 39.388 | 34 | 34 |
| 2006.11239 | 25 | 3.040 | 76.072 | 26 | 23 |
| 2106.09685 | 26 | 5.662 | 83.872 | 19 | 40 |
| 2112.10752 | 45 | 4.530 | 139.818 | 49 | 59 |
| 2305.14314 | 26 | 2.449 | 73.246 | 31 | 32 |
| 2405.20947 | 28 | 3.800 | 202.510 | 48 | 56 |

## Interpretation and remaining gate

The PoC establishes that the adapter can map a varied digital-PDF corpus into
PaperTrans's existing evidence and structure contracts. It also exposes two
items that must remain in manual QA:

- section and table detection counts are often higher with Docling, but higher
  counts can include over-segmentation and are not evidence of higher accuracy;
- equation counts are materially lower with Docling on this corpus, so formula
  classification and visual-crop heuristics need targeted review before making
  Docling the default outside the import experiment.

The generated review sheet intentionally leaves scores blank. A follow-up blind
review should score reading order, section hierarchy, paragraph continuity,
visual crops, equations, captions, references, and page furniture on a 0–2 scale.
