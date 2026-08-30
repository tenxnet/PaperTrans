# Releasing PaperTrans

This runbook is the maintainer gate for source releases. It does not authorize
publishing by itself: every unchecked item in
[`docs/oss-release-checklist.md`](docs/oss-release-checklist.md) that is marked
required for the target release remains a hard stop.

PaperTrans release tags use Semantic Versioning, for example
`v0.2.0-rc.1`. Python metadata uses the equivalent PEP 440 spelling,
`0.2.0rc1`. A release is made from one reviewed commit on `main`; never tag a
feature branch, an uncommitted worktree, or a commit different from the one CI
tested.

## 1. Choose and freeze the release commit

Merge focused pull requests into `main`. Do not combine release cleanup with a
late feature change. Then update the local release checkout without rewriting
history:

```bash
git fetch origin --prune --tags
git switch main
git pull --ff-only origin main
git status --short --branch
git rev-parse HEAD
```

The status output must contain only the `main` branch header. Record the full
commit SHA; all later evidence, the tag, and the GitHub release must identify
that exact SHA.

For an RC, synchronize all of these values in one pull request:

| File | Required value for `v0.2.0-rc.1` |
| --- | --- |
| `package.json` | `0.2.0-rc.1` |
| `pyproject.toml` | `0.2.0rc1` |
| `src/papertrans/__init__.py` (`__release__`) | `0.2.0-rc.1` |
| `src/papertrans/__init__.py` (`__version__`) | `0.2.0rc1` |
| `docling-models.lock.json` (`release`) | `v0.2.0-rc.1` |
| `CHANGELOG.md` | a `## [0.2.0-rc.1]` section |

`tests/test_release_metadata.py` enforces this mapping. Update `uv.lock` and
`pnpm-lock.yaml` only when their inputs actually changed; never regenerate a
lock merely to make CI pass.

## 2. Run the repository gates

Run these commands from the repository root on a clean release checkout:

```bash
git diff --check
uv sync --frozen --extra test
uv run --frozen --extra test pytest -q
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test:pdf-import-admission
pnpm build
PYTHONPATH=workers/babeldoc/worker/src uv run --frozen --extra test pytest -p no:cacheprovider workers/babeldoc/worker/tests -q
docker buildx build --check workers/babeldoc
docker buildx build --check workers/harumi
cargo +1.88.0 test --locked --manifest-path workers/harumi/Cargo.toml
git status --short --branch
```

The final status must still be clean. The BabelDOC tests exercise the isolated
adapter contract with metadata fakes; they do not install or execute
pdf2zh-next, BabelDOC, or PyMuPDF. The Harumi job is also an evaluation-worker
gate, not evidence that Harumi output is suitable for publication. Container
builds and PDF-corpus qualification remain separate promotion gates documented
under `workers/`.

Push the candidate commit and wait for every required GitHub Actions job to
pass on that exact SHA. A successful run for an older `main` commit is not
release evidence.

## 3. Verify clean macOS and Linux checkouts

Use one macOS host and one Linux host that do not share this repository's
`.venv`, `node_modules`, `.next`, `data`, `output`, uv cache, pnpm store, or
Docling model directory. Record the OS version, architecture, `git`, `uv`,
`node`, and `pnpm` versions, date, and release commit SHA.

On each host, clone normally from GitHub and check out the recorded commit:

```bash
git clone https://github.com/tenxnet/PaperTrans.git PaperTrans-rc-check
cd PaperTrans-rc-check
git checkout <release-commit-sha>
git status --short --branch
./papertrans --version
./papertrans setup
./papertrans doctor
```

The checkout must be clean before setup. Setup must perform the real first
download of the release-pinned Docling models and finish with `doctor` ready.
Use empty, test-only `--data-root` and `--output-root` arguments if the host has
existing PaperTrans data.

Start the services and verify both loopback endpoints from another terminal.
Before stopping, confirm that no PDF import remains in `preparing`. Then stop
with `Ctrl-C` and require `papertrans status` to show Web/MCP `stopped`, PDF
import `idle` or `stale`, and Artifact maintenance `safe`:

```bash
./papertrans start --no-browser
curl --fail http://127.0.0.1:3000/api/system/health
./papertrans status
```

Next, actually disconnect the host from the network or apply an outbound deny
rule, and repeat with the same checkout and data/model roots:

```bash
./papertrans start --offline --no-browser
curl --fail http://127.0.0.1:3000/api/system/health
./papertrans status
```

`--offline` without a real network denial is not sufficient evidence for the
network-free restart gate. Save only redacted command results: do not attach
papers, translations, credentials, home-directory paths, or service logs that
contain private data. The RC does not yet ship a redistributable paper fixture,
so setup/start validation must not be reported as translation-corpus coverage.

## 4. Complete policy and release-note review

Before tagging:

- enable and test GitHub Private Vulnerability Reporting;
- confirm the `main` ruleset requires review and the current four CI jobs and
  blocks force pushes and branch deletion;
- review `SECURITY.md` and confirm its supported-version statement;
- review `CHANGELOG.md` and copy all known limitations into the pre-release
  notes;
- record a maintainer decision for the PyMuPDF licensing options and separately
  review the exact Docling model licenses used by the lock;
- verify the dependency audit is current for both lockfiles;
- review the Git diff and history for credentials, private paper material, and
  generated artifacts.

`docs/dependency-licenses.md` is an inventory and risk note, not a legal
conclusion. Do not describe PyMuPDF, the Docling models, or an application
distribution as cleared merely because the automated tests pass.

## 5. Tag and publish the pre-release

After every gate above is recorded against the same commit, create an annotated
tag and verify its target before pushing it:

```bash
git tag -a v0.2.0-rc.1 <release-commit-sha> -m "PaperTrans v0.2.0-rc.1"
git show --no-patch --decorate v0.2.0-rc.1
git push origin v0.2.0-rc.1
```

The tag push starts the same four CI jobs. Wait for that tag run to pass and
confirm it reports the recorded commit before creating the GitHub release.

Prepare and review `/tmp/papertrans-v0.2.0-rc.1-release-notes.md` containing the
matching `CHANGELOG.md` section and all known limitations. Then create a GitHub
**pre-release**, not a stable release, either in the Releases UI or with GitHub
CLI:

```bash
gh release create v0.2.0-rc.1 \
  --verify-tag \
  --prerelease \
  --title "PaperTrans v0.2.0-rc.1" \
  --notes-file /tmp/papertrans-v0.2.0-rc.1-release-notes.md
```

State that this is a macOS/Linux source checkout, mark Docling PDF import
experimental, and link the MCP setup, data lifecycle, security,
dependency-license, and troubleshooting documentation. Do not attach local
models, papers, translations, `.env` files, or unverified application binaries.
Confirm the displayed tag resolves to the recorded commit and that a fresh
download of the source archive contains none of those files.

## 6. Rollback and supersession

If a problem is found before the tag is pushed, delete only the local tag, fix
the issue through a pull request, and restart this runbook from the new commit:

```bash
git tag -d v0.2.0-rc.1
```

Once a tag is public, never silently move it to different code and never force
push `main`. Mark the GitHub release as withdrawn or add a prominent warning,
revert the affected commits through a reviewed pull request when appropriate,
and publish a new RC tag after all gates pass again. For a credential leak or a
security issue, also follow `SECURITY.md`, revoke affected credentials, and
remove downloadable assets when continued distribution would cause harm.

Application-data rollback is separate from source rollback. Users should make
the backup described in
[`docs/local-data-lifecycle.md`](docs/local-data-lifecycle.md) before updating;
the project does not promise that data written by a newer RC can be read by an
older checkout.
