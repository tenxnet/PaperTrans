from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.check_release_checkout import CheckoutError, verify_checkout


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


@pytest.fixture()
def clean_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.email", "release-test@example.invalid")
    _git(repository, "config", "user.name", "Release Test")
    (repository / "tracked.txt").write_text("release candidate\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "Create candidate")
    return repository, _git(repository, "rev-parse", "HEAD")


def test_clean_exact_checkout_passes(clean_repository: tuple[Path, str]) -> None:
    repository, commit = clean_repository
    assert verify_checkout(
        repository,
        expected_commit=commit,
        expected_branch="main",
        require_full_history=True,
    ) == (commit, "main")


@pytest.mark.parametrize("kind", ["tracked", "untracked"])
def test_dirty_checkout_fails(
    clean_repository: tuple[Path, str], kind: str
) -> None:
    repository, commit = clean_repository
    if kind == "tracked":
        (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    else:
        (repository / "untracked.txt").write_text("new\n", encoding="utf-8")

    with pytest.raises(CheckoutError, match="not clean"):
        verify_checkout(repository, expected_commit=commit)


def test_wrong_or_abbreviated_commit_fails(
    clean_repository: tuple[Path, str],
) -> None:
    repository, commit = clean_repository
    with pytest.raises(CheckoutError, match="full 40-character"):
        verify_checkout(repository, expected_commit=commit[:12])
    with pytest.raises(CheckoutError, match="HEAD is"):
        verify_checkout(repository, expected_commit="0" * 40)


def test_release_commit_must_be_reachable_from_required_branch(
    clean_repository: tuple[Path, str],
) -> None:
    repository, main_commit = clean_repository
    assert verify_checkout(
        repository,
        expected_commit=main_commit,
        require_ancestor_of="main",
    ) == (main_commit, "main")

    _git(repository, "switch", "--create", "unmerged")
    (repository / "unmerged.txt").write_text("not on main\n", encoding="utf-8")
    _git(repository, "add", "unmerged.txt")
    _git(repository, "commit", "-m", "Create unmerged release candidate")
    unmerged_commit = _git(repository, "rev-parse", "HEAD")

    with pytest.raises(CheckoutError, match="not reachable from main"):
        verify_checkout(
            repository,
            expected_commit=unmerged_commit,
            require_ancestor_of="main",
        )
