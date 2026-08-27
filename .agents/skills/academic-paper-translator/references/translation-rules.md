# Academic translation rules

## Decision classes

- `preserve`: model and method names, datasets, benchmarks, acronyms, organizations, libraries, APIs, variables, mathematical symbols, units, identifiers, code, URLs, and DOI strings. Keep exact casing and punctuation.
- `bilingual-first`: established academic concepts where Japanese improves readability but the English term is important for literature search. Use `日本語（English）` once per document, then use the chosen Japanese term consistently.
- `translate`: ordinary exposition with a stable Japanese equivalent.

Paper-scoped glossary entries override global entries. For conflicting automatic decisions, prefer preservation and add a warning.

## Fidelity

- Keep citations such as `[12]`, `[3, 7]`, and `[4-6]` byte-for-byte.
- Keep equation references such as `(1)` and `Eq. (3)` traceable; translate surrounding prose only.
- Retain distinctions such as may/might/can, statistically significant, upper/lower bound, and correlation versus causation.
- Do not strengthen claims, normalize reported values, convert units, or add explanations.
- Translate headings as headings and paragraphs as paragraphs. Never convert either into bullets unless the source block is already a list.
- Leave reference-list entries in their original language.

## Japanese style

- Use concise `である` style unless the paper's genre clearly requires another scholarly register.
- Prefer established terminology from the relevant field over literal translation.
- Avoid excessive katakana when a stable Japanese term exists, but preserve search-critical English on first use.
- Keep sentence boundaries natural in Japanese without losing logical connectives or qualifiers.
