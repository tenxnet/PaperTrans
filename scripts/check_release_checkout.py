#!/usr/bin/env python3
"""Fail unless a checkout is clean and points at the expected commit."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


FULL_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")


class CheckoutError(RuntimeError):
    """The checkout cannot be used as release evidence."""


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git failed"
        raise CheckoutError(detail)
    return result.stdout.strip()


def verify_checkout(
    repository: Path,
    *,
    expected_commit: str,
    expected_branch: str | None = None,
    require_full_history: bool = False,
    require_ancestor_of: str | None = None,
) -> tuple[str, str | None]:
    """Return the verified commit and branch, or raise ``CheckoutError``."""

    if FULL_COMMIT_RE.fullmatch(expected_commit) is None:
        raise CheckoutError("--commit must be a full 40-character Git commit SHA")

    root = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
    if root != repository.resolve():
        raise CheckoutError(f"repository root is {root}, not {repository.resolve()}")

    actual_commit = _git(repository, "rev-parse", "--verify", "HEAD^{commit}").lower()
    if actual_commit != expected_commit.lower():
        raise CheckoutError(
            f"HEAD is {actual_commit}, expected release commit {expected_commit.lower()}"
        )

    branch_result = subprocess.run(
        ["git", "-C", str(repository), "symbolic-ref", "--quiet", "--short", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if branch_result.returncode not in (0, 1):
        raise CheckoutError(branch_result.stderr.strip() or "could not inspect branch")
    branch = branch_result.stdout.strip() or None
    if expected_branch is not None and branch != expected_branch:
        raise CheckoutError(
            f"checkout branch is {branch or 'detached HEAD'}, expected {expected_branch}"
        )

    if require_full_history:
        shallow = _git(repository, "rev-parse", "--is-shallow-repository")
        if shallow != "false":
            raise CheckoutError("release-history evidence requires a non-shallow checkout")

    if require_ancestor_of is not None:
        ancestor_target = _git(
            repository, "rev-parse", "--verify", f"{require_ancestor_of}^{{commit}}"
        )
        ancestry = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "merge-base",
                "--is-ancestor",
                actual_commit,
                ancestor_target,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if ancestry.returncode == 1:
            raise CheckoutError(
                f"release commit {actual_commit} is not reachable from "
                f"{require_ancestor_of} ({ancestor_target})"
            )
        if ancestry.returncode != 0:
            raise CheckoutError(
                ancestry.stderr.strip() or "could not verify release-branch ancestry"
            )

    status = _git(repository, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        paths = "\n".join(f"  {line}" for line in status.splitlines())
        raise CheckoutError(f"release checkout is not clean:\n{paths}")

    return actual_commit, branch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Expected full commit SHA")
    parser.add_argument("--branch", help="Expected branch; omit for detached CI checkouts")
    parser.add_argument(
        "--require-full-history",
        action="store_true",
        help="Reject a shallow checkout (required before a history audit)",
    )
    parser.add_argument(
        "--require-ancestor-of",
        metavar="REF",
        help="Require the release commit to be reachable from this Git ref",
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: current directory)",
    )
    arguments = parser.parse_args(argv)

    try:
        commit, branch = verify_checkout(
            arguments.repository,
            expected_commit=arguments.commit,
            expected_branch=arguments.branch,
            require_full_history=arguments.require_full_history,
            require_ancestor_of=arguments.require_ancestor_of,
        )
    except CheckoutError as error:
        print(f"release checkout gate failed: {error}", file=sys.stderr)
        return 1

    print(f"release checkout verified: {commit} ({branch or 'detached HEAD'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
