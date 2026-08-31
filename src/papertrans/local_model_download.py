"""Download the Docling model subset pinned by the PaperTrans release lock."""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Any, Callable, Sequence


REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
REVISION = re.compile(r"[0-9a-f]{40}")
DIRECTORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ModelDownloadError(RuntimeError):
    """The release model lock or destination is unsafe."""


def _regular_non_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _safe_pattern(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = Path(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return None
    return value


def _sources(lock_path: Path) -> list[dict[str, Any]]:
    if not _regular_non_symlink(lock_path):
        raise ModelDownloadError(f"model lock is missing or unsafe: {lock_path}")
    try:
        value = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelDownloadError(f"could not read model lock: {error}") from error
    raw_sources = value.get("sources") if isinstance(value, dict) else None
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ModelDownloadError("model lock has no pinned sources")
    sources: list[dict[str, Any]] = []
    seen_directories: set[str] = set()
    for source in raw_sources:
        if not isinstance(source, dict):
            raise ModelDownloadError("model lock contains an invalid source")
        repository = source.get("repository")
        revision = source.get("revision")
        directory = source.get("directory")
        patterns = source.get("allowPatterns")
        safe_patterns = (
            [_safe_pattern(pattern) for pattern in patterns]
            if isinstance(patterns, list)
            else []
        )
        if (
            not isinstance(repository, str)
            or REPOSITORY.fullmatch(repository) is None
            or not isinstance(revision, str)
            or REVISION.fullmatch(revision) is None
            or not isinstance(directory, str)
            or DIRECTORY.fullmatch(directory) is None
            or directory in seen_directories
            or not safe_patterns
            or any(pattern is None for pattern in safe_patterns)
        ):
            raise ModelDownloadError("model lock contains an unsafe or unpinned source")
        seen_directories.add(directory)
        sources.append(
            {
                "repository": repository,
                "revision": revision,
                "directory": directory,
                "allowPatterns": safe_patterns,
            }
        )
    return sources


def download_locked_models(
    lock_path: Path,
    output_dir: Path,
    *,
    downloader: Callable[..., object] | None = None,
) -> None:
    try:
        output_info = output_dir.lstat()
    except FileNotFoundError as error:
        raise ModelDownloadError(f"model output directory does not exist: {output_dir}") from error
    if not stat.S_ISDIR(output_info.st_mode) or stat.S_ISLNK(output_info.st_mode):
        raise ModelDownloadError(f"model output directory is unsafe: {output_dir}")
    if downloader is None:
        from huggingface_hub import snapshot_download

        downloader = snapshot_download
    for source in _sources(lock_path):
        destination = output_dir / source["directory"]
        downloader(
            repo_id=source["repository"],
            revision=source["revision"],
            local_dir=destination,
            allow_patterns=source["allowPatterns"],
            token=False,
            max_workers=4,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download release-pinned Docling models")
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        download_locked_models(args.lock, args.output_dir)
    except ModelDownloadError as error:
        print(f"PaperTrans: {error}")
        return 10
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
