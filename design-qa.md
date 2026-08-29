# PaperTrans V1 Design QA

## Evidence

- Source visual truth: `design/reference-option-1.png`
- Library implementation: `design/implementation-v1-library.png`
- Reader implementation: `design/implementation-v1-reader.png`
- Full-view comparisons:
  - `design/design-qa-v1-comparison.jpg`
  - `design/design-qa-v1-reader-comparison.jpg`
- Viewport: 1440 × 1024 CSS px
- Source pixels: 1487 × 1058, normalized to 1440 × 1024 for comparison
- Implementation pixels: 1440 × 1024
- Density normalization: 1 CSS px to 1 output px for implementation; source resized once to the same comparison size
- States: five-paper library and completed-paper reader with QA inspector and one saved tag

## Findings

No actionable P0, P1, or P2 differences remain.

- Fonts and typography: the reference's serif paper titles and compact sans-serif controls are retained. List titles and reader headings preserve the same hierarchy and do not clip at the target viewport.
- Spacing and layout rhythm: the fixed left library rail, 68 px top bar, centered content region, restrained borders, and compact right inspector follow the selected visual direction. The library view intentionally replaces the reference paper body with a paper list.
- Colors and visual tokens: warm paper surfaces, neutral gray borders, muted metadata, and the blue primary state match the reference palette and maintain readable contrast.
- Image quality and asset fidelity: paper figures remain original assets inside the generated HTML iframe. UI icons use Phosphor's vector icon set; no raster placeholders or improvised icon drawings are used.
- Copy and content: controls use concise Japanese labels. arXiv IDs, provider names, QA counts, and original paper titles remain identifiable. Display-only cleanup removes one raw overline LaTeX fragment from a library title without changing stored source data.

The right column differs intentionally: the reference uses a paper table of contents, while the V1 app uses job status, structural QA, tags, and source actions. The generated paper retains its own table of contents when opened in a separate tab.

## Focused Region Comparison

No additional cropped comparison was required. The source and both implementation captures were opened at their original readable resolution; the left navigation, top search bar, paper row metadata, reader header, and right inspector were each visually legible in the full-size inputs.

## Comparison History

1. Initial reader capture showed two PaperTrans headers: the app reader header and the standalone artifact header. This was a P2 loss of vertical reading space.
2. Added an embed-only rendering mode that hides the standalone artifact header while preserving it for separate-tab and offline use.
3. Recaptured the reader at 1440 × 1024. The duplicate header is gone, the paper begins directly below the reader controls, and no P0/P1/P2 issue remains.

## Interactions and Runtime Checks

- Search narrowed five papers to the matching arXiv ID.
- Status and tag filters changed the visible paper set.
- A tag was saved through the API and remained after reload.
- Paper selection opened the translated HTML and its QA inspector.
- Embedded HTML loaded with the standalone header hidden.
- Offline ZIP and HTML endpoints returned HTTP 200 with the correct content types.
- New-translation guidance modal opened correctly.
- Browser console errors and warnings checked: none.
- `pnpm typecheck`, `pnpm build`, and all 42 Python tests passed.

## Follow-up Polish

- A future reader pass can promote the translated paper's table of contents into the app inspector after the V1 library workflow is stable.

final result: passed
