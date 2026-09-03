# Releasing PaperTrans

This runbook is the maintainer gate for source releases. It does not authorize
publishing by itself: every unchecked item in
[`docs/oss-release-checklist.md`](docs/oss-release-checklist.md) that is marked
required for the target release remains a hard stop.

PaperTrans release tags use Semantic Versioning, for example
`v0.2.0-rc.2`. Python metadata uses the equivalent PEP 440 spelling,
`0.2.0rc2`. A release is made from one reviewed commit on `main`; never tag a
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
that exact SHA. Make the clean/full-history assertion executable rather than
relying on visual inspection:

```bash
release_commit="$(git rev-parse HEAD)"
python3 scripts/check_release_checkout.py \
  --commit "$release_commit" --branch main --require-full-history
```

For an RC, synchronize all of these values in one pull request:

| File | Required value for `v0.2.0-rc.2` |
| --- | --- |
| `package.json` | `0.2.0-rc.2` |
| `pyproject.toml` | `0.2.0rc2` |
| `src/papertrans/__init__.py` (`__release__`) | `0.2.0-rc.2` |
| `src/papertrans/__init__.py` (`__version__`) | `0.2.0rc2` |
| `docling-models.lock.json` (`release`) | `v0.2.0-rc.2` |
| `CHANGELOG.md` | move `[Unreleased]` entries to a dated `## [0.2.0-rc.2]` section |

`tests/test_release_metadata.py` enforces this mapping. Update `uv.lock` and
`pnpm-lock.yaml` only when their inputs actually changed; never regenerate a
lock merely to make CI pass.

## 2. Run the repository gates

Run these commands from the repository root on a clean release checkout:

```bash
release_commit="$(git rev-parse HEAD)"
python3 scripts/check_release_checkout.py \
  --commit "$release_commit" --branch main --require-full-history
git diff --check
git diff --check v0.2.0-rc.1..HEAD
uv lock --check
uv sync --frozen --extra test --extra mcp --group docling
uv run --frozen --extra test --group docling pytest -q
PAPERTRANS_RELEASE_TAG=v0.2.0-rc.2 \
  uv run --frozen --extra test --group docling pytest -q \
  tests/test_release_metadata.py::test_release_tag_matches_public_metadata_when_provided
release_dist="$(mktemp -d "${TMPDIR:-/tmp}/papertrans-release-dist.XXXXXX")"
uv build --out-dir "$release_dist"
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
pnpm build
PYTHONPATH=workers/babeldoc/worker/src uv run --frozen --extra test pytest -p no:cacheprovider workers/babeldoc/worker/tests -q
uv run --frozen python -m py_compile \
  workers/babeldoc/scripts/fetch_source_artifacts.py \
  workers/babeldoc/scripts/generate_runtime_metadata.py \
  workers/babeldoc/scripts/generate_tree_manifest.py \
  workers/babeldoc/scripts/validate_source.py \
  workers/babeldoc/scripts/verify_installed_source_mapping.py \
  workers/babeldoc/scripts/verify_tree_manifest.py \
  workers/deterministic-gateway/gateway.py
docker buildx build --check workers/babeldoc
docker buildx build --check workers/harumi
cargo +1.88.0 test --locked --manifest-path workers/harumi/Cargo.toml
python3 scripts/check_release_checkout.py \
  --commit "$release_commit" --branch main --require-full-history
```

The last command also rejects untracked, non-ignored output. The four CI jobs
run the same exact-SHA/clean-tree check both before and after validation. Their
stable required-check contexts are:

- `Python tests and release metadata`
- `Web typecheck and build`
- `BabelDOC worker contract tests`
- `Harumi worker tests`

Before relying on local worker results, check the execution engines themselves:

```bash
docker info
docker buildx version
rustup toolchain install 1.88.0 --profile minimal
cargo +1.88.0 --version
```

Having only the Docker client is insufficient; `docker info` must reach a
running daemon. If a local engine or toolchain is unavailable, the matching
clean GitHub Actions job on the exact candidate SHA remains mandatory and the
local result must be recorded as unavailable, never as passed.

### Dependency vulnerability evidence

Audit the complete root Python dependency graph exported from the frozen lock,
and audit production JavaScript dependencies. Keep the JSON file with the
release evidence for the exact candidate commit:

```bash
audit_dir="$(mktemp -d "${TMPDIR:-/tmp}/papertrans-release-audit.XXXXXX")"
uv export --quiet --frozen --all-extras --all-groups --no-dev --no-emit-project \
  --output-file "$audit_dir/root-all-extras-requirements.txt"
for python_version in 3.10 3.11 3.12; do
  uvx --python "$python_version" --from pip-audit==2.10.1 pip-audit \
    --requirement "$audit_dir/root-all-extras-requirements.txt" \
    --disable-pip --strict --format json \
    --output "$audit_dir/pip-audit-python-$python_version.json"
done
pnpm audit --prod
```

Always run and retain all three unfiltered Python audits first. Python 3.10,
3.11, and 3.12 select every currently distinct version-conditioned branch from
the universal lock (`>=3.12` shares one branch). A nonzero result in any audit
is a release stop unless every reported advisory is fixed or covered by a
reviewed exception record. Do not silently add
`--ignore-vuln`. An exception record must
identify the advisory/CVE, exact locked package and version, affected optional
extra or execution path, reachability evidence, compensating controls, owner,
approval decision, review/expiry date, and candidate commit SHA. Commit that
record with the release or retain it as immutable release evidence linked from
the release review.

The `docling` dependency group must resolve `transformers` to the exact tested
security-fixed version. Verify both the lock and the upstream path-traversal
regression before accepting the audit result:

```bash
uv run --frozen --group docling python -c \
  'import importlib.metadata as m; assert m.version("transformers") == "5.10.4"'
uv run --frozen --extra test --group docling pytest -q \
  tests/test_transformers_security.py
```

Do not add an advisory ignore for `GHSA-xrqw-3rrv-vx5w` / `CVE-2026-9856`.
Any reappearance of `transformers<5.10.0`, any failed malicious-template test,
or any unapproved audit finding is a hard stop. Docling is intentionally a uv
dependency group for the complete source-checkout application rather than a
published wheel extra; ordinary pip metadata cannot express the tested macOS
override safely.

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
- confirm the `main` ruleset requires pull requests and the current four CI
  jobs, and blocks force pushes and branch deletion; while the repository has
  only one write-capable maintainer, the checked-in policy intentionally
  requires zero approvals so it cannot deadlock every maintenance change;
- review `SECURITY.md` and confirm its supported-version statement;
- review `CHANGELOG.md` and copy all known limitations into the pre-release
  notes;
- record a maintainer decision for the PyMuPDF licensing options and separately
  review the exact Docling model licenses used by the lock;
- verify the dependency audit is current for both lockfiles;
- review the Git diff and history for credentials, private paper material, and
  generated artifacts.

The repository carries the reviewed intended policy in
`.github/main-ruleset.json`. It is an API request body, not proof of the live
setting. The current solo-maintainer policy requires a pull request and all four
CI jobs, blocks deletion and force pushes, has no bypass actors, and sets the
approval count to zero because GitHub does not allow authors to approve their
own pull requests. Raise the count to one before granting a second trusted user
write access. If no existing ruleset needs to be preserved or updated, a
repository administrator may review and create it with:

```bash
gh api --method POST repos/tenxnet/PaperTrans/rulesets \
  --input .github/main-ruleset.json
```

Do not repeat that command after a partial or successful creation. Inspect the
live rulesets first, and use the read-only checker after configuration and
again after exact-SHA CI completes:

```bash
gh api repos/tenxnet/PaperTrans/rulesets
python3 scripts/check_release_governance.py \
  --repository tenxnet/PaperTrans --branch main --commit "$release_commit"
```

The checker fails if `main` is not the default branch, deletion or force pushes
remain possible, pull requests are not required, the live approval count is
below the checked-in policy, a bypass can avoid the rules, strict status
checking is disabled, a context is missing or is not pinned to the GitHub
Actions app, or no successful CI push run contains all four jobs for the exact
commit.

`docs/dependency-licenses.md` is an inventory and risk note, not a legal
conclusion. Do not describe PyMuPDF, the Docling models, or an application
distribution as cleared merely because the automated tests pass.

## 5. Tag and publish the pre-release

After every gate above is recorded against the same commit, create an annotated
tag and verify its target before pushing it:

```bash
git tag -a v0.2.0-rc.2 <release-commit-sha> -m "PaperTrans v0.2.0-rc.2"
git show --no-patch --decorate v0.2.0-rc.2
git push origin v0.2.0-rc.2
```

The tag push starts the same four CI jobs. The Python job fetches full history
and rejects a tag whose commit is not reachable from `origin/main`. Wait for
that tag run to pass and confirm it reports the recorded commit before creating
the GitHub release.

Prepare and review `/tmp/papertrans-v0.2.0-rc.2-release-notes.md` containing the
matching `CHANGELOG.md` section and all known limitations. Then create a GitHub
**pre-release**, not a stable release, either in the Releases UI or with GitHub
CLI:

```bash
gh release create v0.2.0-rc.2 \
  --verify-tag \
  --prerelease \
  --title "PaperTrans v0.2.0-rc.2" \
  --notes-file /tmp/papertrans-v0.2.0-rc.2-release-notes.md
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
git tag -d v0.2.0-rc.2
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
