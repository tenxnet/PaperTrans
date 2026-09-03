# Contributing to PaperTrans

Thank you for helping improve PaperTrans. The project is currently developing the unpublished v0.2.0-rc.2 release candidate. Official arXiv HTML is the stable input path; Docling PDF import is experimental.

## Before opening an issue

- Search existing issues first.
- Remove paper text, unpublished material, credentials, private paths, and personal data from logs and screenshots.
- Do not upload source PDFs or generated translations unless you have the right to redistribute them.
- Report vulnerabilities through the private channel described in `SECURITY.md`; do not publish actionable vulnerability details in an issue or pull request.

## Development setup

```bash
uv sync --extra test --group docling
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
uv lock --check
uv run --frozen --extra test --group docling pytest -q
pnpm typecheck
pnpm test
pnpm build
```

Changes to an experimental PDF worker also require its isolated checks:

```bash
PYTHONPATH=workers/babeldoc/worker/src uv run --frozen --extra test pytest -p no:cacheprovider workers/babeldoc/worker/tests -q
docker buildx build --check workers/babeldoc
docker buildx build --check workers/harumi
cargo +1.88.0 test --locked --manifest-path workers/harumi/Cargo.toml
```

The BabelDOC command tests the adapter with metadata fakes and does not install
or execute the evaluation engine. Changes to a repository Skill should also
pass its quick validation where applicable.

Documentation changes should keep the Japanese and English root READMEs aligned and avoid duplicating detailed setup that belongs under `docs/`.

Maintainers preparing a tag must follow [RELEASING.md](RELEASING.md). A green
run for an older commit, a setup using an existing model cache, or `--offline`
without an actual network denial does not satisfy the RC release gate.

## Pull requests

- Keep changes focused and explain the user-visible outcome.
- Add or update tests for behavior changes.
- Preserve equations, citations, links, block identifiers, and protected terminology in translation-related changes.
- Do not commit `data/`, `output/`, `.env*`, local databases, browser traces, or generated paper artifacts.
- Document new environment variables in `.env.example` without real values.
- Note any behavior that is specific to Chromium, Firefox, macOS, or Linux.

Unless you explicitly state otherwise, contributions intentionally submitted for inclusion in PaperTrans are provided under the [Apache License 2.0](LICENSE).
