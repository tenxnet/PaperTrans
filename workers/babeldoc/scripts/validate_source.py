#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
import tomllib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--patched", action="store_true")
    args = parser.parse_args()
    lock = tomllib.loads(args.lock.read_text(encoding="utf-8"))
    source = args.source.resolve(strict=True)
    expected_revision = lock["upstream"]["revision"]
    actual_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual_revision != expected_revision:
        raise SystemExit("upstream revision mismatch")
    if not args.patched:
        for relative, expected in lock["source_file_sha256"].items():
            if sha256(source / relative) != expected:
                raise SystemExit(f"upstream source hash mismatch: {relative}")
        if args.patch is None or sha256(args.patch) != lock["fork"]["patch_sha256"]:
            raise SystemExit("fork patch hash mismatch")
    else:
        pyproject = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = set(pyproject["project"]["dependencies"])
        if pyproject["project"]["version"] != lock["fork"]["version"]:
            raise SystemExit("fork version mismatch")
        if "babeldoc==0.6.4" not in dependencies or "pymupdf==1.26.7" not in dependencies:
            raise SystemExit("safe dependency pins are absent")
        if any(item.lower().startswith("babeldoc") and item != "babeldoc==0.6.4" for item in dependencies):
            raise SystemExit("unexpected BabelDOC constraint")


if __name__ == "__main__":
    main()
