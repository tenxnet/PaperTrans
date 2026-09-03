#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import tarfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

BABELDOC_VERSION = "0.6.4"
PYMUPDF_VERSION = "1.26.7"
MUPDF_VERSION = "1.26.12"

BABELDOC_PAYLOAD_FILES = 350
BABELDOC_PAYLOAD_INVENTORY_SHA256 = (
    "1c297d90628a3ec1f95e261a72216fb56c92a6d867a38d7a95c7fe7be1fd9ef3"
)
PYMUPDF_DIRECT_SOURCE_INVENTORY_SHA256 = (
    "c0d659ccc04978f27afcc528ec0f2b0449d1a7f9e76c49d52dfbba5343d45ace"
)
PYMUPDF_BUILD_METADATA_SHA256 = (
    "cbc07581332a1b30f6ae7cb3c20b4d2aa191393a50d6beefdebe72d66593a319"
)
PYMUPDF_WHEEL_LICENSE_SHA256 = (
    "40e60697600535eabfb5ae05f72829d88cfe8d02dd4792f5a754f6f51dabe55b"
)

BABELDOC_WHEEL_SHA256 = (
    "e7dcdd5b8213f657af1df68e329f92d0534c9b94c53fcd82e9e04c52060cb7d0"
)
BABELDOC_WHEEL = {
    "filename": "babeldoc-0.6.4-py3-none-any.whl",
    "url": "https://files.pythonhosted.org/packages/3e/b1/7036b4a5ec6fda008e161950ac619a6e989730a05f3a90fcf1e437f07dec/babeldoc-0.6.4-py3-none-any.whl",
    "sha256": BABELDOC_WHEEL_SHA256,
}
PYMUPDF_WHEEL_BY_MACHINE = {
    "x86_64": {
        "filename": "pymupdf-1.26.7-cp310-abi3-manylinux_2_28_x86_64.whl",
        "url": "https://files.pythonhosted.org/packages/2a/6b/3de1714d734ff949be1e90a22375d0598d3540b22ae73eb85c2d7d1f36a9/pymupdf-1.26.7-cp310-abi3-manylinux_2_28_x86_64.whl",
        "sha256": "69dfc78f206a96e5b3ac22741263ebab945fdf51f0dbe7c5757c3511b23d9d72",
    },
    "aarch64": {
        "filename": "pymupdf-1.26.7-cp310-abi3-manylinux_2_28_aarch64.whl",
        "url": "https://files.pythonhosted.org/packages/65/e7/47af26f3ac76be7ac3dd4d6cc7ee105948a8355d774e5ca39857bf91c11c/pymupdf-1.26.7-cp310-abi3-manylinux_2_28_aarch64.whl",
        "sha256": "e419b609996434a14a80fa060adec72c434a1cca6a511ec54db9841bc5d51b3c",
    },
}

PYMUPDF_DIRECT_SOURCE_PATHS = {
    "pymupdf/__init__.py": "src/__init__.py",
    "pymupdf/__main__.py": "src/__main__.py",
    "pymupdf/_apply_pages.py": "src/_apply_pages.py",
    "pymupdf/_wxcolors.py": "src/_wxcolors.py",
    "pymupdf/pymupdf.py": "src/pymupdf.py",
    "pymupdf/table.py": "src/table.py",
    "pymupdf/utils.py": "src/utils.py",
    "fitz/__init__.py": "src/fitz___init__.py",
    "fitz/table.py": "src/fitz_table.py",
    "fitz/utils.py": "src/fitz_utils.py",
}
PYMUPDF_GENERATED_FILES = (
    "pymupdf/_build.py",
    "pymupdf/extra.py",
    "pymupdf/mupdf.py",
    "pymupdf/_extra.so",
    "pymupdf/_mupdf.so",
)
PYMUPDF_NATIVE_LIBRARIES = (
    "pymupdf/libmupdf.so.26.12",
    "pymupdf/libmupdfcpp.so.26.12",
)
EXPECTED_BUILD_METADATA = {
    "mupdf_location": (
        "https://mupdf.com/downloads/archive/mupdf-1.26.12-source.tar.gz"
    ),
    "pymupdf_version": PYMUPDF_VERSION,
    "pymupdf_version_tuple": (1, 26, 7),
    "pymupdf_git_sha": "8264a4b3798d06ec44af2e0e9d2a13abbc94e97d",
    "pymupdf_git_diff": "",
    "pymupdf_git_branch": "main",
    "swig_version": "4.4.0",
    "swig_version_tuple": (4, 4, 0),
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_inventory_sha256(inventory: dict[str, str]) -> str:
    encoded = json.dumps(
        inventory,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as target:
        json.dump(
            value, target, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def _load_lock(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("rb") as source:
        value = tomllib.load(source)
    if (
        set(value) != {"schema_version", "artifacts"}
        or value.get("schema_version") != 1
        or not isinstance(value.get("artifacts"), list)
    ):
        raise SystemExit("invalid source-artifact lock")
    by_id = {
        item.get("id"): item
        for item in value["artifacts"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if set(by_id) != {"babeldoc", "pymupdf", "mupdf"}:
        raise SystemExit("unexpected source-artifact set")
    return by_id


def _regular_distribution_file(
    distribution: importlib.metadata.Distribution, relative: str
) -> Path:
    files = {str(item): item for item in distribution.files or ()}
    package_path = files.get(relative)
    if package_path is None:
        raise SystemExit(f"installed distribution file is absent: {relative}")
    path = Path(distribution.locate_file(package_path))
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"installed distribution file is unsafe: {relative}")
    return path


def _verify_wheel_record(
    distribution: importlib.metadata.Distribution,
    expected_wheel_files: int,
    *,
    console_scripts: tuple[str, ...] = (),
) -> int:
    files = list(distribution.files or ())
    record_entries = [item for item in files if str(item).endswith(".dist-info/RECORD")]
    if len(record_entries) != 1:
        raise SystemExit("installed wheel RECORD is absent or ambiguous")
    dist_info = PurePosixPath(str(record_entries[0])).parent.as_posix()
    installer_generated = {
        f"{dist_info}/INSTALLER",
        f"{dist_info}/REQUESTED",
        f"{dist_info}/direct_url.json",
        *(f"../../../bin/{name}" for name in console_scripts),
    }
    generated = {str(item) for item in files if str(item) in installer_generated}
    wheel_files = len(files) - len(generated)
    if wheel_files != expected_wheel_files:
        raise SystemExit(
            "installed wheel RECORD source-entry count mismatch: "
            f"{wheel_files} != {expected_wheel_files}; generated={sorted(generated)}"
        )
    for item in files:
        path = _regular_distribution_file(distribution, str(item))
        if item == record_entries[0]:
            if item.hash is not None or item.size is not None:
                raise SystemExit("wheel RECORD self-entry must not carry a digest")
            continue
        if item.hash is None or item.hash.mode != "sha256" or item.size is None:
            raise SystemExit(f"wheel RECORD entry lacks SHA-256 or size: {item}")
        if path.stat().st_size != item.size:
            raise SystemExit(f"installed wheel RECORD size mismatch: {item}")
        encoded = base64.urlsafe_b64encode(bytes.fromhex(_sha256_file(path))).decode(
            "ascii"
        )
        if encoded.rstrip("=") != item.hash.value:
            raise SystemExit(f"installed wheel RECORD digest mismatch: {item}")
    return wheel_files


def _archive_members(path: Path, top_level: str) -> dict[str, bytes]:
    prefix = f"{top_level}/"
    result: dict[str, bytes] = {}
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                member_path = PurePosixPath(member.name)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or not member.name.startswith(prefix)
                ):
                    raise SystemExit(f"unsafe source archive member: {path.name}")
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise SystemExit(f"unreadable source archive member: {member.name}")
                result[member.name.removeprefix(prefix)] = extracted.read()
    except (OSError, tarfile.TarError) as error:
        raise SystemExit(f"invalid source archive: {path.name}: {error}") from error
    return result


def _literal_assignments(source: bytes, label: str) -> dict[str, Any]:
    try:
        tree = ast.parse(source, filename=label)
    except (SyntaxError, ValueError) as error:
        raise SystemExit(f"invalid Python build metadata: {label}: {error}") from error
    assignments: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            assignments[target.id] = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
    return assignments


def _normalize_machine() -> str:
    machine = platform.machine().lower()
    aliases = {"amd64": "x86_64", "arm64": "aarch64"}
    machine = aliases.get(machine, machine)
    if machine not in PYMUPDF_WHEEL_BY_MACHINE:
        raise SystemExit(
            f"unsupported worker architecture for source mapping: {machine}"
        )
    return machine


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _load_install_report(path: Path, machine: str) -> dict[str, dict[str, str]]:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 16 * 1024 * 1024
    ):
        raise SystemExit("pip install report is absent, unsafe, or too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid pip install report: {error}") from error
    installs = value.get("install") if isinstance(value, dict) else None
    if not isinstance(installs, list):
        raise SystemExit("invalid pip install report schema")
    expected = {
        "babeldoc": {**BABELDOC_WHEEL, "version": BABELDOC_VERSION},
        "pymupdf": {**PYMUPDF_WHEEL_BY_MACHINE[machine], "version": PYMUPDF_VERSION},
    }
    observed: dict[str, dict[str, str]] = {}
    for entry in installs:
        if not isinstance(entry, dict) or not isinstance(entry.get("metadata"), dict):
            raise SystemExit("invalid pip install report entry")
        name = entry["metadata"].get("name")
        if not isinstance(name, str):
            raise SystemExit("pip install report entry has no distribution name")
        normalized = _normalize_distribution_name(name)
        if normalized not in expected:
            continue
        if normalized in observed:
            raise SystemExit(f"duplicate pip install report entry: {normalized}")
        download = entry.get("download_info")
        archive = download.get("archive_info") if isinstance(download, dict) else None
        hashes = archive.get("hashes") if isinstance(archive, dict) else None
        actual = {
            "version": entry["metadata"].get("version"),
            "filename": Path(download.get("url", "")).name
            if isinstance(download, dict)
            else "",
            "url": download.get("url") if isinstance(download, dict) else None,
            "sha256": hashes.get("sha256") if isinstance(hashes, dict) else None,
        }
        if actual != expected[normalized] or not actual["filename"].endswith(".whl"):
            raise SystemExit(
                f"pip did not install the audited {normalized} wheel: {actual}"
            )
        observed[normalized] = actual
    if set(observed) != set(expected):
        raise SystemExit("pip install report lacks an audited runtime wheel")
    return observed


def _verify_babeldoc(
    artifact: dict[str, Any], archive: Path, installed_artifact: dict[str, str]
) -> dict[str, Any]:
    distribution = importlib.metadata.distribution("BabelDOC")
    if (
        distribution.version != BABELDOC_VERSION
        or artifact.get("version") != BABELDOC_VERSION
    ):
        raise SystemExit("BabelDOC installed/source version mismatch")
    record_files = _verify_wheel_record(
        distribution, 355, console_scripts=("babeldoc",)
    )
    source = _archive_members(archive, artifact["top_level"])
    source_payload = {
        name.removeprefix("babeldoc/"): value
        for name, value in source.items()
        if name.startswith("babeldoc/")
    }
    installed_paths = sorted(
        str(item)
        for item in distribution.files or ()
        if str(item).startswith("babeldoc/")
    )
    source_paths = sorted(f"babeldoc/{name}" for name in source_payload)
    if installed_paths != source_paths:
        raise SystemExit("BabelDOC wheel/sdist payload path mismatch")
    inventory: dict[str, str] = {}
    for relative in installed_paths:
        installed = _regular_distribution_file(distribution, relative).read_bytes()
        expected = source_payload[relative.removeprefix("babeldoc/")]
        if installed != expected:
            raise SystemExit(f"BabelDOC wheel/sdist payload mismatch: {relative}")
        inventory[relative] = _sha256_bytes(installed)
    if (
        len(inventory) != BABELDOC_PAYLOAD_FILES
        or _canonical_inventory_sha256(inventory) != BABELDOC_PAYLOAD_INVENTORY_SHA256
    ):
        raise SystemExit("BabelDOC audited payload inventory mismatch")
    license_path = next(
        (
            str(item)
            for item in distribution.files or ()
            if str(item).endswith(".dist-info/licenses/LICENSE")
        ),
        None,
    )
    if license_path is None:
        raise SystemExit("BabelDOC wheel license is absent")
    license_sha256 = _sha256_file(
        _regular_distribution_file(distribution, license_path)
    )
    if (
        license_sha256 != artifact["license_sha256"]
        or distribution.metadata.get("License-Expression") != "AGPL-3.0"
        or artifact["runtime_artifact_sha256"] != [installed_artifact["sha256"]]
    ):
        raise SystemExit("BabelDOC wheel/source license or artifact mapping mismatch")
    return {
        "version": BABELDOC_VERSION,
        "sourceArchiveSha256": artifact["sha256"],
        "runtimeArtifactSha256": installed_artifact["sha256"],
        "runtimeArtifactUrl": installed_artifact["url"],
        "payloadFiles": len(inventory),
        "payloadInventorySha256": _canonical_inventory_sha256(inventory),
        "recordFiles": record_files,
        "recordVerified": True,
        "wheelLicenseSha256": license_sha256,
    }


def _verify_pymupdf(
    artifact: dict[str, Any],
    archive: Path,
    mupdf_artifact: dict[str, Any],
    machine: str,
    installed_artifact: dict[str, str],
) -> dict[str, Any]:
    distribution = importlib.metadata.distribution("PyMuPDF")
    if (
        distribution.version != PYMUPDF_VERSION
        or artifact.get("version") != PYMUPDF_VERSION
    ):
        raise SystemExit("PyMuPDF installed/source version mismatch")
    record_files = _verify_wheel_record(distribution, 112)
    source = _archive_members(archive, artifact["top_level"])
    inventory: dict[str, str] = {}
    for installed_name, source_name in PYMUPDF_DIRECT_SOURCE_PATHS.items():
        installed = _regular_distribution_file(
            distribution, installed_name
        ).read_bytes()
        expected = source.get(source_name)
        if expected is None or installed != expected:
            raise SystemExit(f"PyMuPDF wheel/sdist payload mismatch: {installed_name}")
        inventory[installed_name] = _sha256_bytes(installed)
    if _canonical_inventory_sha256(inventory) != PYMUPDF_DIRECT_SOURCE_INVENTORY_SHA256:
        raise SystemExit("PyMuPDF audited direct-source inventory mismatch")

    build_path = _regular_distribution_file(distribution, "pymupdf/_build.py")
    build_bytes = build_path.read_bytes()
    if _sha256_bytes(build_bytes) != PYMUPDF_BUILD_METADATA_SHA256:
        raise SystemExit("PyMuPDF wheel build metadata mismatch")
    build_metadata = _literal_assignments(build_bytes, "pymupdf/_build.py")
    if any(
        build_metadata.get(key) != value
        for key, value in EXPECTED_BUILD_METADATA.items()
    ):
        raise SystemExit("PyMuPDF wheel build inputs differ from audited values")

    setup = source.get("setup.py")
    if setup is None:
        raise SystemExit("PyMuPDF sdist setup.py is absent")
    setup_metadata = _literal_assignments(setup, "PyMuPDF setup.py")
    if (
        setup_metadata.get("version_p") != PYMUPDF_VERSION
        or setup_metadata.get("version_mupdf") != MUPDF_VERSION
        or "src/extra.i" not in source
    ):
        raise SystemExit("PyMuPDF sdist build inputs differ from wheel metadata")

    for relative in (*PYMUPDF_GENERATED_FILES, *PYMUPDF_NATIVE_LIBRARIES):
        _regular_distribution_file(distribution, relative)

    license_path = next(
        (
            str(item)
            for item in distribution.files or ()
            if str(item).endswith(".dist-info/COPYING")
        ),
        None,
    )
    if license_path is None:
        raise SystemExit("PyMuPDF wheel license declaration is absent")
    license_sha256 = _sha256_file(
        _regular_distribution_file(distribution, license_path)
    )
    if (
        license_sha256 != PYMUPDF_WHEEL_LICENSE_SHA256
        or distribution.metadata.get("License")
        != "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License"
    ):
        raise SystemExit("PyMuPDF wheel license declaration mismatch")

    expected_runtime_hash = installed_artifact["sha256"]
    if expected_runtime_hash not in artifact["runtime_artifact_sha256"]:
        raise SystemExit("PyMuPDF architecture wheel is absent from source mapping")
    if (
        mupdf_artifact.get("version") != MUPDF_VERSION
        or mupdf_artifact.get("url") != EXPECTED_BUILD_METADATA["mupdf_location"]
        or mupdf_artifact.get("builds_for") != f"PyMuPDF=={PYMUPDF_VERSION}"
    ):
        raise SystemExit("MuPDF source archive does not match PyMuPDF build metadata")
    return {
        "version": PYMUPDF_VERSION,
        "sourceArchiveSha256": artifact["sha256"],
        "runtimeArtifactSha256": expected_runtime_hash,
        "runtimeArtifactUrl": installed_artifact["url"],
        "architecture": machine,
        "directSourceFiles": len(inventory),
        "directSourceInventorySha256": _canonical_inventory_sha256(inventory),
        "recordFiles": record_files,
        "recordVerified": True,
        "generatedFiles": list(PYMUPDF_GENERATED_FILES),
        "nativeLibraries": list(PYMUPDF_NATIVE_LIBRARIES),
        "buildMetadataSha256": PYMUPDF_BUILD_METADATA_SHA256,
        "pymupdfGitSha": EXPECTED_BUILD_METADATA["pymupdf_git_sha"],
        "swigVersion": EXPECTED_BUILD_METADATA["swig_version"],
        "wheelLicenseDeclarationSha256": license_sha256,
        "mupdf": {
            "version": MUPDF_VERSION,
            "sourceArchiveSha256": mupdf_artifact["sha256"],
            "sourceUrl": mupdf_artifact["url"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--source-artifacts", required=True, type=Path)
    parser.add_argument("--install-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    artifacts = _load_lock(args.lock)
    machine = _normalize_machine()
    installed = _load_install_report(args.install_report, machine)
    babeldoc_archive = args.source_artifacts / artifacts["babeldoc"]["filename"]
    pymupdf_archive = args.source_artifacts / artifacts["pymupdf"]["filename"]
    for archive, artifact in (
        (babeldoc_archive, artifacts["babeldoc"]),
        (pymupdf_archive, artifacts["pymupdf"]),
    ):
        if (
            archive.is_symlink()
            or not archive.is_file()
            or archive.stat().st_size != artifact["bytes"]
            or _sha256_file(archive) != artifact["sha256"]
        ):
            raise SystemExit(f"source archive differs from lock: {artifact['id']}")
    _atomic_json(
        args.output,
        {
            "schemaVersion": 1,
            "babeldoc": _verify_babeldoc(
                artifacts["babeldoc"], babeldoc_archive, installed["babeldoc"]
            ),
            "pymupdf": _verify_pymupdf(
                artifacts["pymupdf"],
                pymupdf_archive,
                artifacts["mupdf"],
                machine,
                installed["pymupdf"],
            ),
        },
    )


if __name__ == "__main__":
    main()
