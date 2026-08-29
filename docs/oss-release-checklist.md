# OSS release checklist

This checklist tracks the remaining work before making PaperTrans public as a v0.1 preview.

## Required before public visibility

- [ ] Select and add the project license.
- [ ] Confirm the copyright holder or organization name used by the license and release metadata.
- [ ] Add and approve the root `SECURITY.md` policy and a private reporting channel.
- [x] Keep `.env*`, `data/`, `output/`, browser traces, caches, and local databases out of Git.
- [x] Add reproducible Python and Node validation in GitHub Actions.
- [x] Add contribution guidance, a code of conduct, issue forms, and a pull-request template.
- [x] Document the supported v1 scope and mark PDF and ChatGPT MCP paths experimental.
- [x] Document copyright responsibility and the need to verify generated translations.
- [ ] Review the complete Git history for credentials and accidentally committed papers before changing repository visibility.
- [ ] Run CI successfully from a clean clone.

## Recommended for the first tagged release

- [ ] Choose the public release branch and merge the current feature branch through a reviewed pull request.
- [ ] Create a `v0.1.0` tag and short release notes with known limitations.
- [ ] Add a small redistributable fixture or synthetic demo instead of a third-party paper.
- [ ] Add screenshots that contain no private paths, credentials, or copyrighted full-paper content.
- [ ] Add repository topics and a concise GitHub description.
- [ ] Configure Dependabot or Renovate after the initial dependency baseline is stable.
- [ ] Decide whether a `CITATION.cff` file is useful and confirm the preferred author names.

## Explicitly deferred beyond v1

- Hosted multi-user service and authentication.
- General PDF support as a guaranteed path.
- Translation providers other than the current Codex and experimental ChatGPT worker paths.
- Translation targets beyond Japanese.
- Public artifact hosting or automatic redistribution of translated papers.
