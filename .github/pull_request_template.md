## Outcome

Describe the user-visible result.

## Scope

- [ ] The change is focused and avoids unrelated generated files.
- [ ] No paper PDFs, full-paper translations, credentials, private paths, or local state are included.
- [ ] New environment variables are documented with empty/example values only.

## Validation

- [ ] `uv lock --check && uv run --frozen --extra test --group docling pytest -q`
- [ ] `pnpm typecheck`
- [ ] `pnpm test`
- [ ] `pnpm build`
- [ ] Experimental-worker checks are not applicable or passed as documented in `CONTRIBUTING.md`.
- [ ] Relevant Firefox/Chromium behavior was checked when UI or generated HTML changed.

## Translation fidelity

- [ ] Not applicable
- [ ] Equations, citations, links, block IDs, figures, tables, and protected terms remain intact.
