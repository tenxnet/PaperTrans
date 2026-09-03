# OSS release checklist

This checklist tracks the unpublished PaperTrans v0.2.0-rc.2 **source release**. Evidence is
valid only for the exact commit named by the tag. An older successful CI run or
a test from a dirty checkout does not satisfy a current gate. The command order
and evidence to retain are defined in [`RELEASING.md`](../RELEASING.md).

The public `v0.2.0-rc.1` tag remains fixed at its original commit and its tag CI
passed, but no GitHub Release was published. It did not complete the clean-OS,
license, and release-governance gates below, so it is not reused or silently
moved; corrections are being prepared under the new rc.2 identity.

## Repository and public-reporting baseline

- [x] Select and add the Apache License 2.0 project license.
- [x] Add the root `SECURITY.md` policy.
- [x] Enable and test GitHub Private Vulnerability Reporting. If the repository
  is already visible, complete this before announcing the RC.
- [x] Enable GitHub secret scanning, push protection, and dependency
  vulnerability alerts.
- [x] Activate `main` ruleset `22182639`, blocking force pushes/deletion and
  requiring pull requests plus all four CI jobs before merge. The current
  solo-maintainer policy uses zero required approvals; raise it to one when a
  second trusted write-capable maintainer is available.
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

## Required for v0.2.0-rc.2

- [x] Establish `main` as the public release branch and GitHub default branch.
- [x] Add an executable release-metadata synchronization test for npm, Python,
  `papertrans.__release__`/`__version__`, the Docling model lock, and the
  dated changelog heading; run the documented tag-environment check before the
  public tag is created.
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
- [x] Record the rc.2 distribution boundary: it is source checkout only and
  contains no PyMuPDF/Docling dependency wheel, Docling model weight, or worker
  image. The application nevertheless declares PyMuPDF as a required runtime
  dependency and `uv` acquires it during source setup, so retain the documented
  AGPL/commercial notice. This scope decision is not approval for a future
  binary, hosted service, or image distribution, and rc.2 is not published to
  PyPI.
- [x] Pin and fail-closed validate the official BabelDOC 0.6.4 and PyMuPDF
  1.26.7 sdists plus MuPDF 1.26.12 source, and configure evaluation-image
  builds to embed them with the patched pdf2zh-next tree and build inputs.
- [x] Review the exact locked Docling model licenses and record their immutable
  model-card evidence and redistribution conditions separately from the
  Python package license in `dependency-licenses.md`.
- [ ] Re-run the locked dependency-license audit on the final release commit.
- [x] Move Docling into the source-only dependency group, pin
  `transformers 5.10.4`, and verify every fully provisioned lock branch with
  `pip-audit`: 131 dependencies on Python 3.10 and 125 on each of Python 3.11
  and 3.12, with zero known vulnerabilities at this checkpoint.
- [ ] Review the `CHANGELOG.md` RC section and copy all known limitations into
  the GitHub release notes.
- [ ] Create an annotated `v0.2.0-rc.2` tag pointing to the verified commit and
  pass the tag-triggered CI run, then publish a GitHub **pre-release** and verify
  the source archive and tag target.

## Future artifact promotion gates (not rc.2 source-release blockers)

- [ ] Before publishing the BabelDOC evaluation image, extract its embedded
  source bundle and verify it against `source-artifacts.lock` and
  `runtime-source-map.json`; independently rebuild/review the native
  PyMuPDF/MuPDF path or approve documented commercial terms, resolve the
  upstream license-declaration inconsistency, publish/retain matching source as
  no-additional-charge release assets, and verify the immutable PaperTrans
  revision in its OCI labels.
- [ ] Pin the 182 BabelDOC build-time assets to immutable origin revisions,
  resolve the GoNoto binaries' OFL-1.1 declarations versus the asset README's
  Unlicense label, and embed/verify the applicable font, CMap, DocLayout model,
  and tiktoken license evidence and notices before publishing the evaluation
  image.
- [ ] If a future binary/archive bundles TableFormer, include the
  CDLA-Permissive-2.0 agreement text. If it bundles either Heron form, include
  Apache-2.0 and retain the exact locked model-card evidence.

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
