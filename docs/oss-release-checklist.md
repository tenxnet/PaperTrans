# OSS release checklist

This checklist tracks the PaperTrans v0.2.0-rc.1 **source release**. Evidence is
valid only for the exact commit named by the tag. An older successful CI run or
a test from a dirty checkout does not satisfy a current gate. The command order
and evidence to retain are defined in [`RELEASING.md`](../RELEASING.md).

## Repository and public-reporting baseline

- [x] Select and add the Apache License 2.0 project license.
- [x] Add the root `SECURITY.md` policy.
- [ ] Enable and test GitHub Private Vulnerability Reporting. If the repository
  is already visible, complete this before announcing the RC.
- [ ] Configure a `main` ruleset that blocks force pushes/deletion and requires
  pull-request review plus all four CI jobs before merge.
- [x] Keep `.env*`, `data/`, `output/`, browser traces, caches, local databases,
  and generated paper artifacts out of Git.
- [x] Define reproducible root Python, release-metadata, Web typecheck/build,
  BabelDOC-adapter, and Harumi-worker checks in GitHub Actions.
- [x] Add contribution guidance, a code of conduct, issue forms, and a
  pull-request template.
- [x] Document official arXiv HTML plus MCP translation as the supported v1
  path and mark Docling PDF import experimental.
- [x] Document MCP registration for direct local clients and ChatGPT through
  Secure MCP Tunnel.
- [x] Document copyright responsibility and the need to verify generated
  translations.
- [x] Document data retention, backup, update, paper removal, and uninstall
  behavior for the source-checkout release.
- [ ] Repeat the repository-history check for credentials, accidentally
  committed papers, archives, and unusually large blobs at the final release
  commit.

## Required for v0.2.0-rc.1

- [x] Establish `main` as the public release branch and GitHub default branch.
- [x] Add an executable release-metadata synchronization test for npm, Python,
  `papertrans.__release__`/`__version__`, the Docling model lock, and the
  changelog heading.
- [x] Add the maintainer release runbook covering a clean tree, ordered tests,
  clean-OS checks, tagging, a GitHub pre-release, and rollback.
- [x] Bound experimental Docling PDF imports and reject non-local Markdown
  media before publication.
- [ ] Merge the intended RC changes into `main` through reviewed pull requests;
  record the resulting full commit SHA and a clean `git status`.
- [ ] Pass every required GitHub Actions job on that exact `main` commit.
- [ ] Verify `./papertrans setup`, `doctor`, startup, health, and shutdown from
  a clean macOS checkout with no reused project or model state.
- [ ] Verify the same flow from a clean Linux checkout with no reused project
  or model state.
- [ ] On both systems, verify the real first Docling model download and hash
  check, then disconnect/deny network access and complete a subsequent
  `./papertrans start --offline --no-browser` restart.
- [ ] Confirm the distribution decision and required notices for the
  AGPL/commercial PyMuPDF dependency. Do not infer a legal conclusion from the
  dependency inventory.
- [ ] Review the exact locked Docling model licenses and record any
  redistribution restrictions separately from the Python package license.
- [ ] Re-run the locked dependency-license audit on the final release commit.
- [ ] Review the `CHANGELOG.md` RC section and copy all known limitations into
  the GitHub release notes.
- [ ] Create an annotated `v0.2.0-rc.1` tag pointing to the verified commit and
  pass the tag-triggered CI run, then publish a GitHub **pre-release** and verify
  the source archive and tag target.

## Recommended before the first stable tagged release

- [ ] Add a small redistributable fixture or synthetic demo instead of a
  third-party paper.
- [ ] Add screenshots containing no private paths, credentials, or copyrighted
  full-paper content.
- [ ] Add repository topics and a concise GitHub description.
- [ ] Configure Dependabot or Renovate after the initial dependency baseline is
  stable.
- [ ] Decide whether a `CITATION.cff` file is useful and confirm the preferred
  author names.
- [ ] Before distributing application binaries, decide whether to retain,
  replace, or further isolate the AGPL/commercial PyMuPDF dependency used by
  experimental PDF and PDF-figure conversion paths.

## Historical evidence (not a current release gate)

- The 2026-08-30 filename/blob-size/PDF/archive/common-secret-pattern history
  review reported no candidates at the then-current commit. New commits still
  require the final review above.
- [CI run 33266533245](https://github.com/tenxnet/PaperTrans/actions/runs/33266533245)
  passed from a clean `main` checkout on 2026-08-30. It predates the current RC
  changes and cannot authorize the new tag.
- The dependency inventory in `dependency-licenses.md` was reviewed on
  2026-08-30. It records risks and declared licenses, not legal clearance.

## Explicitly deferred beyond v1

- Hosted multi-user service and authentication.
- General PDF support as a guaranteed path.
- Direct translation-provider or model API integrations outside the MCP client
  boundary.
- Translation targets beyond Japanese.
- Public artifact hosting or automatic redistribution of translated papers.
- Signed desktop packaging, automatic updates, and removal of the
  source-checkout runtime prerequisites.
