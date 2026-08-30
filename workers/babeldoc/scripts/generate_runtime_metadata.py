#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Any

EXPECTED = {
    "pdf2zh-next": "2.9.0+papertrans.1",
    "babeldoc": "0.6.4",
    "pymupdf": "1.26.7",
    "papertrans-babeldoc-worker": "0.1.1",
}


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def generate_sbom(path: Path) -> None:
    components = []
    observed: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        normalized = name.lower()
        version = distribution.version
        observed[normalized] = version
        component = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name.lower().replace('_', '-')}@{version}",
        }
        license_name = distribution.metadata.get("License")
        if license_name:
            component["licenses"] = [{"license": {"name": license_name}}]
        components.append(component)
    mismatches = {
        name: {"expected": expected, "actual": observed.get(name)}
        for name, expected in EXPECTED.items()
        if observed.get(name) != expected
    }
    if mismatches:
        raise SystemExit(f"refusing metadata generation: version mismatch: {mismatches}")
    components.sort(key=lambda item: (item["name"].lower(), item["version"]))
    atomic_json(
        path,
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "version": 1,
            "metadata": {"component": {"type": "application", "name": "papertrans-babeldoc-worker", "version": "0.1.1"}},
            "components": components,
        },
    )


def generate_asset_manifest(root: Path, path: Path) -> None:
    resolved_root = root.resolve(strict=True)
    entries = []
    for candidate in sorted(resolved_root.rglob("*")):
        if candidate.is_symlink():
            raise SystemExit(f"refusing symlink in asset cache: {candidate}")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(resolved_root).as_posix()
        entries.append(
            {"path": relative, "sha256": sha256(candidate), "bytes": candidate.stat().st_size}
        )
    if not entries:
        raise SystemExit("refusing empty BabelDOC asset cache")
    atomic_json(path, {"schemaVersion": 1, "files": entries})


def generate_provenance(args: argparse.Namespace) -> None:
    atomic_json(
        args.provenance,
        {
            "schemaVersion": 1,
            "upstreamRevision": "f8dffcf4c3a33b254391d43514439b975ce8d966",
            "digests": {
                "buildManifest": sha256(args.build_manifest),
                "runtimeRequirementsLock": sha256(args.requirements_lock),
                "buildRequirementsLock": sha256(args.build_requirements_lock),
                "upstreamLock": sha256(args.upstream_lock),
                "forkPatch": sha256(args.patch),
                "sbom": sha256(args.sbom),
                "assetManifest": sha256(args.asset_manifest),
                "sourceManifest": sha256(args.source_manifest),
            },
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--asset-root", required=True, type=Path)
    parser.add_argument("--asset-manifest", required=True, type=Path)
    parser.add_argument("--build-manifest", required=True, type=Path)
    parser.add_argument("--requirements-lock", required=True, type=Path)
    parser.add_argument("--build-requirements-lock", required=True, type=Path)
    parser.add_argument("--upstream-lock", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    args = parser.parse_args()
    generate_sbom(args.sbom)
    generate_asset_manifest(args.asset_root, args.asset_manifest)
    generate_provenance(args)


if __name__ == "__main__":
    main()
