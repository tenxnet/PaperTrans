# Contributing to PaperTrans

Thank you for helping improve PaperTrans. The project is currently a v0.2.0-rc.1 release candidate. Official arXiv HTML is the stable input path; Docling PDF import is experimental.

## Before opening an issue

- Search existing issues first.
- Remove paper text, unpublished material, credentials, private paths, and personal data from logs and screenshots.
- Do not upload source PDFs or generated translations unless you have the right to redistribute them.
- Do not publish actionable vulnerability details until the repository has a configured private reporting channel. The release checklist tracks this requirement.

## Development setup

```bash
uv sync --extra test
pnpm install --frozen-lockfile
```

Start the local web app with an explicit loopback binding:

```bash
pnpm dev --hostname 127.0.0.1
```

To exercise the complete release-candidate runtime, including the Docling models, use the root launcher instead:

```bash
./papertrans setup
./papertrans start --dev
```

## Validation

Run the relevant checks before opening a pull request:

```bash
uv run pytest -q
pnpm typecheck
pnpm build
```

Changes to a repository Skill should also pass its quick validation where applicable.

Documentation changes should keep the Japanese and English root READMEs aligned and avoid duplicating detailed setup that belongs under `docs/`.

## Pull requests

- Keep changes focused and explain the user-visible outcome.
- Add or update tests for behavior changes.
- Preserve equations, citations, links, block identifiers, and protected terminology in translation-related changes.
- Do not commit `data/`, `output/`, `.env*`, local databases, browser traces, or generated paper artifacts.
- Document new environment variables in `.env.example` without real values.
- Note any behavior that is specific to Chromium, Firefox, macOS, or Linux.

Unless you explicitly state otherwise, contributions intentionally submitted for inclusion in PaperTrans are provided under the [Apache License 2.0](LICENSE).
