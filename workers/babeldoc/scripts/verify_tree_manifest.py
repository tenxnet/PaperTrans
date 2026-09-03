#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path
from typing import Any


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path, revision: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise SystemExit("source-tree manifest is missing, unsafe, or too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid source-tree manifest: {error}") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "upstreamRevision", "files"}
        or value["schemaVersion"] != 1
        or value["upstreamRevision"] != revision
        or not isinstance(value["files"], list)
        or not value["files"]
    ):
        raise SystemExit("invalid source-tree manifest schema or revision")
    return value["files"]


def verify_tree(root_path: Path, manifest_path: Path, revision: str) -> None:
    if root_path.is_symlink() or not root_path.is_dir():
        raise SystemExit("source-tree root is missing or unsafe")
    root = root_path.resolve(strict=True)
    expected: set[str] = set()
    for entry in load_manifest(manifest_path, revision):
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            raise SystemExit("invalid source-tree manifest entry")
        relative = entry["path"]
        digest = entry["sha256"]
        size = entry["bytes"]
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or relative in expected
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or size < 0
        ):
            raise SystemExit("invalid source-tree manifest entry")
        expected.add(relative)
        candidate = root / relative
        try:
            metadata = candidate.lstat()
        except FileNotFoundError as error:
            raise SystemExit(f"source-tree file is missing: {relative}") from error
        if not stat.S_ISREG(metadata.st_mode) or candidate.is_symlink():
            raise SystemExit(f"source-tree file is unsafe: {relative}")
        if metadata.st_size != size or hash_file(candidate) != digest:
            raise SystemExit(f"source-tree file differs: {relative}")
    actual: set[str] = set()
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            raise SystemExit(f"source tree contains a symlink: {relative}")
        if candidate.is_file():
            actual.add(relative)
    if actual != expected:
        additions = sorted(actual - expected)
        raise SystemExit(
            "source tree contains unmanifested files: " + ", ".join(additions[:5])
        )


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    verify_tree(args.root, args.manifest, args.revision)


if __name__ == "__main__":
    main()
