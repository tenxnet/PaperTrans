# Academic PDF structure rules

## Evidence priority

1. Use page imagery for columns, spacing, visual grouping, object boundaries, and caption proximity.
2. Use PDF coordinates and font metadata for exact alignment, size, and repeated furniture.
3. Use language and scholarly conventions to resolve paragraph continuation and hierarchy.
4. Never let one signal alone override clear contradictory evidence from the others.

## Reading order and paragraphs

- Read a full-width title or heading before the columns below it.
- In a two-column region, finish the left column before the right column unless a full-width object interrupts both columns.
- A paragraph may be split into several PDF text blocks. Join blocks when the first ends without terminal punctuation, the next begins as a grammatical continuation, indentation and font agree, or a column/page break intervenes.
- Start a new paragraph for visible indentation, vertical paragraph spacing, list markers, a heading boundary, or a completed thought followed by a new topic.
- A paragraph crossing a page retains one `paragraphId`; the later page assignment sets `continuesFrom` to the preceding block ID.

## Sections

- Major numbered headings such as `4 Evaluation` are level 1.
- Decimal headings such as `4.2 Results` are level 2; `4.2.1` headings are level 3.
- `Abstract`, `Acknowledgments`, `References`, and lettered appendices are genuine top-level sections.
- Bold lead-in sentences, enumerated experimental settings, page numbers, algorithm titles, and broken prose are not sections unless the page's typography and surrounding structure clearly make them headings.

## Page furniture and notes

- Repeated top/bottom text, proceedings names, running titles, author names in margins, and bare page numbers are `header`, `footer`, or `page_number` and hidden.
- Footnotes remain visible but separate from body paragraphs and retain their marker.
- Author names, affiliations, dates, and repository identifiers belong to front matter, not body headings.

## Visual objects and crops

- `figure`: plots, diagrams, screenshots, or multi-panel illustrations.
- `table`: the complete ruled or aligned tabular body; include row and column labels.
- `equation`: a displayed mathematical expression, including its equation number, but not surrounding explanation.
- `algorithm`: the full algorithm box or listing, including line numbers, but not nearby prose.
- The normalized bounding box order is `[x0, y0, x1, y1]` relative to the rendered page, with top-left origin.
- Make the crop tight but include every mark belonging to the object. Exclude captions when exact caption blocks exist so the HTML can render searchable original caption text below the image.
- For multi-panel figures with one caption, return one bounding box covering every panel unless each panel has an independent caption and reference label.
- `insertAfterBlockId` identifies the body block after which the object should appear. Prefer the last explanatory block before the object; use `null` only for front-matter objects with no meaningful anchor.

## Relationships

- Copy citation markers byte-for-byte into `citations` for their containing block.
- Treat `Figure 3`, `Table 2`, `Eq. (4)`, and similar mentions as `objectReferences` using the visible label.
- Bibliography entries use role `reference`; retain their printed number or label in `referenceLabel` when present.
