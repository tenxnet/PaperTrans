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

BABELDOC_ASSET_INVENTORY_SHA256 = (
    "aed82c0c1fe09f09f3dc5307c646e019948992e37ab62c99e780833220bb9320"
)
BABELDOC_ASSET_BYTES = 352_451_566
ASSET_GROUPS = frozenset({"fonts", "models", "tiktoken", "cmap"})

# Peewee 4.4.0 declares License-File but no License-Expression, License, or
# license classifier. The MIT text below is verified at runtime by its file
# digest. It matches the official immutable upstream revision:
# https://github.com/coleifer/peewee/blob/942d9c510ed7accce6fc65afee6023d25a69d5fa/LICENSE
# The exact py3-none-any wheel is pinned in requirements.lock as
# sha256:c4d6bc13d9a9b22fe691f695f5a5d716645b4bd5387fef27b9cb53ef14fae1e1.
LICENSE_METADATA_OVERRIDES = {
    ("peewee", "4.4.0"): {
        "expression": "MIT",
        "license_file": "licenses/LICENSE",
        "license_file_sha256": "3740096125b08735a247b8dd08cd82e0ba984d3bebd9221d378576637e5240da",
        "source": "https://github.com/coleifer/peewee/blob/942d9c510ed7accce6fc65afee6023d25a69d5fa/LICENSE",
        "wheel_sha256": "c4d6bc13d9a9b22fe691f695f5a5d716645b4bd5387fef27b9cb53ef14fae1e1",
    }
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


def sha3_256(path: Path) -> str:
    digest = hashlib.sha3_256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_asset_inventory_sha256(inventory: dict[str, str]) -> str:
    encoded = json.dumps(
        inventory,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def load_expected_babeldoc_assets() -> dict[str, str]:
    try:
        from babeldoc.assets.embedding_assets_metadata import CMAP_METADATA
        from babeldoc.assets.embedding_assets_metadata import (
            DOCLAYOUT_YOLO_DOCSTRUCTBENCH_IMGSZ1024ONNX_SHA3_256,
        )
        from babeldoc.assets.embedding_assets_metadata import EMBEDDING_FONT_METADATA
        from babeldoc.assets.embedding_assets_metadata import TIKTOKEN_CACHES
    except (ImportError, OSError) as error:
        raise SystemExit(
            f"refusing metadata generation: cannot load BabelDOC asset metadata: {error}"
        ) from error
    try:
        value = {
            "fonts": [
                {"name": name, "sha3_256": metadata["sha3_256"]}
                for name, metadata in EMBEDDING_FONT_METADATA.items()
            ],
            "cmap": [
                {"name": name, "sha3_256": metadata["sha3_256"]}
                for name, metadata in CMAP_METADATA.items()
            ],
            "tiktoken": [
                {"name": name, "sha3_256": digest}
                for name, digest in TIKTOKEN_CACHES.items()
            ],
            "models": [
                {
                    "name": "doclayout_yolo_docstructbench_imgsz1024.onnx",
                    "sha3_256": DOCLAYOUT_YOLO_DOCSTRUCTBENCH_IMGSZ1024ONNX_SHA3_256,
                }
            ],
        }
    except (KeyError, TypeError) as error:
        raise SystemExit(
            f"refusing metadata generation: invalid BabelDOC asset metadata: {error}"
        ) from error
    if not isinstance(value, dict) or set(value) != ASSET_GROUPS:
        raise SystemExit("refusing metadata generation: invalid BabelDOC asset groups")
    inventory: dict[str, str] = {}
    for group in sorted(ASSET_GROUPS):
        entries = value[group]
        if not isinstance(entries, list) or not entries:
            raise SystemExit(
                f"refusing metadata generation: invalid BabelDOC {group} inventory"
            )
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"name", "sha3_256"}:
                raise SystemExit(
                    f"refusing metadata generation: invalid BabelDOC {group} entry"
                )
            name = entry["name"]
            digest = entry["sha3_256"]
            if (
                not isinstance(name, str)
                or not name
                or Path(name).name != name
                or name.startswith(".")
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise SystemExit(
                    f"refusing metadata generation: unsafe BabelDOC {group} entry"
                )
            relative = f"{group}/{name}"
            if relative in inventory:
                raise SystemExit(
                    f"refusing metadata generation: duplicate BabelDOC asset: {relative}"
                )
            inventory[relative] = digest
    actual_digest = canonical_asset_inventory_sha256(inventory)
    if actual_digest != BABELDOC_ASSET_INVENTORY_SHA256:
        raise SystemExit(
            "refusing metadata generation: BabelDOC asset inventory differs from "
            f"the audited 0.6.4 inventory: {actual_digest}"
        )
    return inventory


def license_classifiers(metadata: Any) -> list[str]:
    get_all = getattr(metadata, "get_all", None)
    if callable(get_all):
        values = get_all("Classifier") or []
    else:
        value = metadata.get("Classifier")
        values = [value] if isinstance(value, str) else value or []
    return [
        classifier.strip()
        for classifier in values
        if isinstance(classifier, str)
        and classifier.strip().startswith("License ::")
        and classifier.rsplit("::", 1)[-1].strip().upper() != "UNKNOWN"
    ]


def audited_license_override(distribution: Any, name: str, version: str) -> dict[str, str] | None:
    override = LICENSE_METADATA_OVERRIDES.get((name, version))
    if override is None:
        return None
    license_text = distribution.read_text(override["license_file"])
    actual_digest = (
        hashlib.sha256(license_text.encode("utf-8")).hexdigest()
        if license_text is not None
        else None
    )
    if actual_digest != override["license_file_sha256"]:
        raise SystemExit(
            "refusing metadata generation: audited license override mismatch for "
            f"{name}=={version}: expected {override['license_file_sha256']}, "
            f"actual {actual_digest}"
        )
    return override


def generate_sbom(path: Path) -> None:
    components = []
    observed: dict[str, str] = {}
    missing_licenses: list[str] = []
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
        license_expression = distribution.metadata.get("License-Expression")
        license_name = distribution.metadata.get("License")
        classifiers = license_classifiers(distribution.metadata)
        if license_expression and license_expression.strip().upper() != "UNKNOWN":
            component["licenses"] = [{"expression": license_expression.strip()}]
        elif license_name and license_name.strip().upper() != "UNKNOWN":
            component["licenses"] = [{"license": {"name": license_name.strip()}}]
        elif classifiers:
            # Preserve the declared Trove values verbatim instead of guessing an
            # SPDX expression from a human-readable classifier.
            component["licenses"] = [
                {"license": {"name": classifier}} for classifier in classifiers
            ]
        else:
            override = audited_license_override(distribution, normalized, version)
            if override:
                component["licenses"] = [{"expression": override["expression"]}]
                component["properties"] = [
                    {
                        "name": "papertrans:license-override-source",
                        "value": override["source"],
                    },
                    {
                        "name": "papertrans:license-override-wheel-sha256",
                        "value": override["wheel_sha256"],
                    },
                    {
                        "name": "papertrans:license-override-license-file-sha256",
                        "value": override["license_file_sha256"],
                    },
                ]
            else:
                missing_licenses.append(f"{name}=={version}")
        components.append(component)
    if missing_licenses:
        raise SystemExit(
            "refusing metadata generation: missing dependency license metadata: "
            + ", ".join(sorted(missing_licenses, key=str.lower))
        )
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


def generate_asset_manifest(
    root: Path,
    path: Path,
    expected: dict[str, str],
    *,
    expected_bytes: int,
) -> None:
    resolved_root = root.resolve(strict=True)
    actual_paths: set[str] = set()
    for candidate in sorted(resolved_root.rglob("*")):
        if candidate.is_symlink():
            raise SystemExit(f"refusing symlink in asset cache: {candidate}")
        if candidate.is_file():
            actual_paths.add(candidate.relative_to(resolved_root).as_posix())
    if actual_paths != set(expected):
        missing = sorted(set(expected) - actual_paths)
        unexpected = sorted(actual_paths - set(expected))
        raise SystemExit(
            "refusing metadata generation: BabelDOC asset path inventory mismatch: "
            f"missing={missing[:3]}, unexpected={unexpected[:3]}"
        )
    entries = []
    total_bytes = 0
    for relative in sorted(expected):
        candidate = resolved_root / relative
        observed_sha3 = sha3_256(candidate)
        if observed_sha3 != expected[relative]:
            raise SystemExit(
                "refusing metadata generation: BabelDOC asset SHA3-256 mismatch: "
                f"{relative}"
            )
        size = candidate.stat().st_size
        total_bytes += size
        entries.append(
            {
                "path": relative,
                "sha256": sha256(candidate),
                "sha3_256": observed_sha3,
                "bytes": size,
            }
        )
    if total_bytes != expected_bytes:
        raise SystemExit(
            "refusing metadata generation: BabelDOC asset byte total mismatch: "
            f"expected {expected_bytes}, actual {total_bytes}"
        )
    atomic_json(
        path,
        {
            "schemaVersion": 2,
            "inventorySha256": canonical_asset_inventory_sha256(expected),
            "files": entries,
        },
    )


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
                "completeSourceManifest": sha256(args.complete_source_manifest),
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
    parser.add_argument("--complete-source-manifest", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    args = parser.parse_args()
    generate_sbom(args.sbom)
    generate_asset_manifest(
        args.asset_root,
        args.asset_manifest,
        load_expected_babeldoc_assets(),
        expected_bytes=BABELDOC_ASSET_BYTES,
    )
    generate_provenance(args)


if __name__ == "__main__":
    main()
