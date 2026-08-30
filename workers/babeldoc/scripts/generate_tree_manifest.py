#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            raise SystemExit(f"source tree contains a symlink: {relative}")
        if path.is_file():
            entries.append(
                {"path": relative.as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size}
            )
    if not entries:
        raise SystemExit("source tree manifest cannot be empty")
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as target:
        json.dump(
            {"schemaVersion": 1, "upstreamRevision": args.revision, "files": entries},
            target,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, args.output)


if __name__ == "__main__":
    main()
