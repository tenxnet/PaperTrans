# OSS release checklist

This checklist tracks the remaining work before making PaperTrans public as a v0.1 preview.

## Required before public visibility

- [x] Select and add the Apache License 2.0 project license.
- [x] Add and approve the root `SECURITY.md` policy.
- [ ] Enable GitHub Private Vulnerability Reporting when the repository becomes public.
- [x] Keep `.env*`, `data/`, `output/`, browser traces, caches, and local databases out of Git.
- [x] Add reproducible Python and Node validation in GitHub Actions.
- [x] Add contribution guidance, a code of conduct, issue forms, and a pull-request template.
- [x] Document official arXiv HTML plus MCP translation as the supported v1 path; mark PDF and Codex CLI paths experimental.
- [x] Document copyright responsibility and the need to verify generated translations.
- [x] Review the complete Git history for credentials and accidentally committed papers before changing repository visibility (2026-08-30; no candidates found by filename, blob-size, PDF/archive, or common-secret-pattern checks).
- [x] Review installed and declared dependency licenses and record known copyleft/data-license obligations in `docs/dependency-licenses.md` (2026-08-30).
- [x] Run CI successfully from a clean GitHub checkout on `main` ([CI run 33266533245](https://github.com/tenxnet/PaperTrans/actions/runs/33266533245), 2026-08-30).

## Recommended for the first tagged release

- [x] Establish `main` as the public release branch and GitHub default branch.
- [ ] Create a `v0.1.0` tag and short release notes with known limitations.
- [ ] Add a small redistributable fixture or synthetic demo instead of a third-party paper.
- [ ] Add screenshots that contain no private paths, credentials, or copyrighted full-paper content.
- [ ] Add repository topics and a concise GitHub description.
- [ ] Configure Dependabot or Renovate after the initial dependency baseline is stable.
- [ ] Decide whether a `CITATION.cff` file is useful and confirm the preferred author names.
- [ ] Before the first tagged release, decide whether to retain, replace, or further isolate the AGPL/commercial PyMuPDF dependency used by experimental PDF and PDF-figure conversion paths.

## Explicitly deferred beyond v1

- Hosted multi-user service and authentication.
- General PDF support as a guaranteed path.
- Direct translation-provider or model API integrations outside the MCP client boundary.
- Translation targets beyond Japanese.
- Public artifact hosting or automatic redistribution of translated papers.
