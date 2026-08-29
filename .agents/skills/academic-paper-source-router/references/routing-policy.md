# Source routing policy

Use this reference when building or reviewing a candidate manifest.

## 1. Identity and version fidelity

Normalize identifiers without an LLM:

- Strip URL wrappers from arXiv IDs while retaining an explicit `vN` suffix.
- Normalize DOI prefixes and URL forms to the bare, case-insensitive DOI.
- Record the user-requested artifact: arXiv revision, version of record, accepted manuscript, or unspecified best available version.
- Use title, authors, and year only as corroborating evidence, not as a replacement for a conflicting identifier.

Candidate `identityMatch` values mean:

- `exact`: the requested identifier and revision/version match.
- `equivalent`: a related preprint or manuscript is believed to contain the same work but is not the requested artifact.
- `unknown`: identity or version could not be established.
- `mismatch`: evidence shows that it is a different work or revision.

Reject `mismatch`. Reject `equivalent` unless `policy.allowEquivalentPreprint` is true. Keep a warning whenever an equivalent artifact is selected.

## 2. Candidate discovery order

Discovery order is not selection order; inexpensive probes may run in parallel.

For an arXiv identifier:

1. Official versioned arXiv HTML.
2. Versioned ar5iv HTML.
3. Matching arXiv TeX source archive.
4. Matching arXiv PDF.

For a DOI, publisher URL, or local publisher PDF:

1. Authorized JATS or other structured full-text XML for the requested version.
2. Authorized semantic full-text HTML for the requested version.
3. Explicitly permitted equivalent preprint, using the arXiv order above.
4. The requested PDF.

Do not scrape a metadata page and classify it as full text. Do not use an API result beyond its access scope.

## 3. Deterministic candidate validation

Set `validation` from observable checks:

- `passed`: full text is parseable; main sections exist; references and declared assets resolve; no fatal conversion marker is present.
- `degraded`: full text is usable but some non-critical assets, cross-references, or conversion nodes need repair.
- `failed`: metadata-only content, missing main body, fatal parser/converter error, unusable encoding, or material asset loss.
- `not_run`: not yet inspected. It cannot be selected.

Useful HTML/XML checks include:

- non-empty title and body;
- multiple coherent paragraphs or sections rather than navigation/abstract alone;
- counts of figures, tables, equations, citations, and bibliography entries;
- all local fragment targets used by citations and cross-references exist;
- required image and stylesheet URLs resolve;
- no LaTeXML fatal/error nodes affecting the main body.

Do not use an LLM to decide whether these mechanical checks passed.

## 4. Resource budgets by selected route

### Semantic HTML/XML

- Structure model: disabled.
- OCR: disabled.
- Vision: disabled.
- Translation: section chunks containing only headings, paragraphs, list items, footnotes, and eligible captions according to product policy.
- Preserve: MathML, SVG, images, tables, IDs, anchors, references, bibliography, code, and metadata.

### TeX source

- Convert once with LaTeXML and cache the result.
- Structure model, OCR, and vision remain disabled unless conversion is materially incomplete and a PDF fallback is selected for the missing region.
- Never send the entire TeX project to the translation model. Translate eligible text extracted from the converted semantic DOM.

### Text-layer PDF

- Extract text and coordinates once.
- Use GROBID or equivalent deterministic scholarly parsing for document hierarchy and citations.
- Render only figures, tables, displayed equations, and low-confidence QA regions rather than every page at high resolution.
- Structure model and vision are `low_confidence_only`; OCR is disabled for validated text pages.

### Scanned or damaged PDF

- Classify pages individually. OCR only pages whose usable-text ratio is below threshold.
- Use page images at the lowest resolution that meets OCR or crop verification requirements.
- Invoke vision on unresolved pages or objects, not the complete document.
- Cache OCR and visual decisions by PDF hash and page number.

## 5. Translation cost controls

- Translate by semantic section, not page and not isolated visual text block.
- Send a compact paper glossary plus the current section; do not repeat full-document source in every request.
- Translate independent sections concurrently within the caller's configured rate and cost limits.
- Cache by source text hash, glossary version, target language, model, and translation-policy version.
- Run deterministic checks first: block completeness, protected token equality, citation anchors, MathML count, figure/table count, and unresolved links.
- Use a model review only for failed checks, terminology conflicts, or explicitly requested quality review.

## 6. Fallback boundaries

A fallback is local whenever possible:

- Missing HTML figure: recover that figure from TeX assets or PDF; do not replace the entire document route.
- Broken equation: recover that equation from TeX or a PDF crop; do not OCR every page.
- Unresolved citation: repair the affected anchor or parse the bibliography; do not retranslate the section.
- Failed translation chunk: retry that section only.

Escalate the whole document to the next route only when the primary source lacks a material portion of the body or its identity cannot be verified.
