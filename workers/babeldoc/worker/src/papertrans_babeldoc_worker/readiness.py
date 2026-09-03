from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import inspect
import json
import os
import platform
import re
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    ADAPTER_VERSION,
    ASSET_MANIFEST_PATH,
    ASSET_ROOT,
    BABELDOC_ASSET_INVENTORY_SHA256,
    BABELDOC_VERSION,
    BUILD_MANIFEST_PATH,
    BUILD_MANIFEST_SHA256,
    BUILD_REQUIREMENTS_PATH,
    BUILD_REQUIREMENTS_SHA256,
    COMPLETE_SOURCE_MANIFEST_PATH,
    COMPLETE_SOURCE_ROOT,
    ENGINE_VERSION,
    FORK_PATCH_PATH,
    FORK_PATCH_SHA256,
    INPUT_ROOT,
    OUTPUT_ROOT,
    PROVIDER_SECRET_PATH,
    PROVENANCE_PATH,
    PYMUPDF_VERSION,
    PYTHON_VERSION,
    RUNTIME_BABELDOC_CACHE_ROOT,
    RUNTIME_PDF2ZH_CACHE_ROOT,
    RUNTIME_REQUIREMENTS_PATH,
    RUNTIME_REQUIREMENTS_SHA256,
    RUNTIME_SOURCE_MAPPING_PATH,
    SBOM_PATH,
    SOURCE_MANIFEST_PATH,
    SOURCE_ROOT,
    SOURCE_ARTIFACTS_LOCK_PATH,
    SOURCE_ARTIFACTS_LOCK_SHA256,
    SOURCE_ARTIFACTS_MANIFEST_PATH,
    SOURCE_ARTIFACTS_ROOT,
    UPSTREAM_LOCK_PATH,
    UPSTREAM_LOCK_SHA256,
    UPSTREAM_REVISION,
)
from .contract import load_json_object, parse_provider_profile
from .errors import ContractError
from .stdio import silence_process_stdio

IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CONFIG_ROOT = Path("/opt/papertrans/home/.config")


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: tuple[dict[str, Any], ...]
    versions: dict[str, str | None]
    source_revision: str | None
    build_digest: str | None
    image_digest: str | None
    sbom_sha256: str | None
    lock_sha256: str | None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _load_plain_json(path: Path, max_bytes: int = 16 * 1024 * 1024) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("metadata file is absent, unsafe, or too large")
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_ok(path: Path) -> bool:
    value = _load_plain_json(path)
    return bool(
        isinstance(value, dict)
        and value.get("schemaVersion") == 1
        and value.get("adapterVersion") == ADAPTER_VERSION
        and value.get("engine", {}).get("version") == ENGINE_VERSION
        and value.get("engine", {}).get("upstreamRevision") == UPSTREAM_REVISION
        and value.get("dependencies", {}).get("BabelDOC") == BABELDOC_VERSION
        and value.get("dependencies", {}).get("PyMuPDF") == PYMUPDF_VERSION
        and value.get("dependencies", {}).get("Python") == PYTHON_VERSION
        and value.get("engine", {}).get("patchSha256") == FORK_PATCH_SHA256
        and value.get("locks", {}).get("requirements.lock")
        == RUNTIME_REQUIREMENTS_SHA256
        and value.get("locks", {}).get("build-requirements.lock")
        == BUILD_REQUIREMENTS_SHA256
        and value.get("locks", {}).get("UPSTREAM.lock") == UPSTREAM_LOCK_SHA256
        and value.get("locks", {}).get("source-artifacts.lock")
        == SOURCE_ARTIFACTS_LOCK_SHA256
        and _hash_file(path) == BUILD_MANIFEST_SHA256
    )


def _sbom_ok(path: Path) -> bool:
    value = _load_plain_json(path)
    if not isinstance(value, dict) or value.get("bomFormat") != "CycloneDX":
        return False
    components = value.get("components")
    if not isinstance(components, list):
        return False
    versions = {
        str(component.get("name", "")).lower(): component.get("version")
        for component in components
        if isinstance(component, dict)
    }
    return (
        versions.get("pdf2zh-next") == ENGINE_VERSION
        and versions.get("babeldoc") == BABELDOC_VERSION
        and versions.get("pymupdf") == PYMUPDF_VERSION
        and versions.get("papertrans-babeldoc-worker") == ADAPTER_VERSION
    )


def _asset_manifest_ok(path: Path, asset_root: Path) -> bool:
    value = _load_plain_json(path)
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "inventorySha256", "files"}
        or value.get("schemaVersion") != 2
        or value.get("inventorySha256") != BABELDOC_ASSET_INVENTORY_SHA256
    ):
        return False
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        return False
    root = asset_root.resolve(strict=True)
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "sha256",
            "sha3_256",
            "bytes",
        }:
            return False
        relative = entry["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith(("/", "."))
        ):
            return False
        if relative in seen:
            return False
        seen.add(relative)
        unresolved = root / relative
        if unresolved.is_symlink():
            return False
        candidate = unresolved.resolve(strict=True)
        if candidate.parent != root and root not in candidate.parents:
            return False
        if not candidate.is_file():
            return False
        if (
            type(entry["bytes"]) is not int
            or candidate.stat().st_size != entry["bytes"]
        ):
            return False
        if (
            not isinstance(entry["sha256"], str)
            or _hash_file(candidate) != entry["sha256"]
            or not isinstance(entry["sha3_256"], str)
            or len(entry["sha3_256"]) != 64
            or any(
                character not in "0123456789abcdef" for character in entry["sha3_256"]
            )
        ):
            return False
        digest = hashlib.sha3_256()
        with candidate.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != entry["sha3_256"]:
            return False
    actual: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            return False
        if candidate.is_file():
            actual.add(candidate.relative_to(root).as_posix())
    inventory = {
        entry["path"]: entry["sha3_256"] for entry in entries if isinstance(entry, dict)
    }
    inventory_digest = hashlib.sha256(
        json.dumps(
            inventory,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    return actual == seen and inventory_digest == BABELDOC_ASSET_INVENTORY_SHA256


def _materialize_runtime_assets(
    manifest_path: Path,
    asset_root: Path,
    runtime_cache_root: Path,
    *,
    allow_mutable_cache: bool = False,
) -> bool:
    """Expose verified immutable assets inside an empty writable cache tmpfs."""

    value = _load_plain_json(manifest_path)
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 2
        or value.get("inventorySha256") != BABELDOC_ASSET_INVENTORY_SHA256
    ):
        return False
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        return False
    immutable_root = asset_root.resolve(strict=True)
    if (
        runtime_cache_root.is_symlink()
        or not runtime_cache_root.is_dir()
        or not os.access(runtime_cache_root, os.W_OK)
    ):
        return False

    expected: dict[str, Path] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "sha256",
            "sha3_256",
            "bytes",
        }:
            return False
        relative = entry["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith(("/", "."))
            or ".." in Path(relative).parts
            or relative in expected
        ):
            return False
        source = immutable_root / relative
        if source.is_symlink():
            return False
        resolved_source = source.resolve(strict=True)
        if (
            immutable_root not in resolved_source.parents
            or not resolved_source.is_file()
        ):
            return False
        expected[relative] = resolved_source

    def links_match() -> bool:
        actual_links: set[str] = set()
        expected_directories = {
            parent.as_posix()
            for relative in expected
            for parent in Path(relative).parents
            if parent != Path(".")
        }
        mutable_cache_files = {
            "cache.v1.db",
            "cache.v1.db-shm",
            "cache.v1.db-wal",
        }
        for candidate in runtime_cache_root.rglob("*"):
            relative = candidate.relative_to(runtime_cache_root).as_posix()
            if candidate.is_dir() and not candidate.is_symlink():
                if relative not in expected_directories:
                    return False
                continue
            if allow_mutable_cache and relative in mutable_cache_files:
                try:
                    metadata = candidate.lstat()
                except OSError:
                    return False
                if candidate.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                    return False
                continue
            target = expected.get(relative)
            if target is None or not candidate.is_symlink():
                return False
            actual_links.add(relative)
            try:
                if candidate.resolve(strict=True) != target:
                    return False
            except OSError:
                return False
        return actual_links == set(expected)

    # Health checks and the subsequent one-job run share the same cache tmpfs.
    # Once the exact links exist, validate them rather than rejecting a second
    # readiness call. Any partial, replaced, or additional entry fails closed.
    if any(runtime_cache_root.iterdir()):
        return links_match()

    created_links: list[Path] = []
    created_directories: list[Path] = []
    try:
        for relative, source in expected.items():
            destination = runtime_cache_root / relative
            missing_parents: list[Path] = []
            parent = destination.parent
            while parent != runtime_cache_root and not parent.exists():
                missing_parents.append(parent)
                parent = parent.parent
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            created_directories.extend(reversed(missing_parents))
            destination.symlink_to(source)
            created_links.append(destination)
        return links_match()
    except OSError:
        for link in reversed(created_links):
            try:
                link.unlink()
            except OSError:
                pass
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        return False


def _provider_ok(path: Path) -> bool:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        return False
    parse_provider_profile(load_json_object(path, max_bytes=16 * 1024))
    return True


def _tree_manifest_ok(path: Path, root_path: Path) -> bool:
    value = _load_plain_json(path)
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 1
        or value.get("upstreamRevision") != UPSTREAM_REVISION
        or not isinstance(value.get("files"), list)
        or not value["files"]
    ):
        return False
    root = root_path.resolve(strict=True)
    expected_paths: set[str] = set()
    for entry in value["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            return False
        relative = entry["path"]
        if not isinstance(relative, str) or not relative or relative.startswith("/"):
            return False
        if relative in expected_paths or ".." in Path(relative).parts:
            return False
        expected_paths.add(relative)
        unresolved = root / relative
        if unresolved.is_symlink():
            return False
        candidate = unresolved.resolve(strict=True)
        if root not in candidate.parents or not candidate.is_file():
            return False
        if (
            candidate.stat().st_size != entry["bytes"]
            or _hash_file(candidate) != entry["sha256"]
        ):
            return False
    actual_paths: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            return False
        if candidate.is_file():
            actual_paths.add(candidate.relative_to(root).as_posix())
    return actual_paths == expected_paths


def _source_artifacts_ok(lock_path: Path, root_path: Path, manifest_path: Path) -> bool:
    if (
        lock_path.is_symlink()
        or not lock_path.is_file()
        or lock_path.stat().st_size > 1024 * 1024
        or _hash_file(lock_path) != SOURCE_ARTIFACTS_LOCK_SHA256
    ):
        return False
    with lock_path.open("rb") as source:
        lock = tomllib.load(source)
    if (
        not isinstance(lock, dict)
        or set(lock) != {"schema_version", "artifacts"}
        or lock.get("schema_version") != 1
        or not isinstance(lock.get("artifacts"), list)
        or not lock["artifacts"]
    ):
        return False
    if root_path.is_symlink() or not root_path.is_dir():
        return False
    manifest = _load_plain_json(manifest_path, max_bytes=1024 * 1024)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schemaVersion", "lockSha256", "files"}
        or manifest.get("schemaVersion") != 1
        or manifest.get("lockSha256") != SOURCE_ARTIFACTS_LOCK_SHA256
        or not isinstance(manifest.get("files"), list)
    ):
        return False
    root = root_path.resolve(strict=True)
    if manifest_path.resolve(strict=True).parent != root:
        return False
    required_lock_fields = {
        "id",
        "name",
        "version",
        "kind",
        "filename",
        "url",
        "sha256",
        "bytes",
        "top_level",
        "license_declaration",
        "license_path",
        "license_sha256",
        "runtime_artifact_sha256",
    }
    manifest_fields = {
        "id",
        "name",
        "version",
        "path",
        "sha256",
        "bytes",
        "licenseDeclaration",
        "licensePath",
        "licenseSha256",
    }
    manifest_by_id: dict[str, dict[str, Any]] = {}
    for entry in manifest["files"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != manifest_fields
            or not isinstance(entry.get("id"), str)
            or entry["id"] in manifest_by_id
        ):
            return False
        manifest_by_id[entry["id"]] = entry
    expected_paths = {manifest_path.name}
    seen_ids: set[str] = set()
    for artifact in lock["artifacts"]:
        if (
            not isinstance(artifact, dict)
            or not required_lock_fields <= set(artifact)
            or set(artifact) - required_lock_fields - {"builds_for"}
        ):
            return False
        identifier = artifact["id"]
        filename = artifact["filename"]
        digest = artifact["sha256"]
        size = artifact["bytes"]
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier in seen_ids
            or not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
            or filename.startswith(".")
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or type(size) is not int
            or size <= 0
            or not isinstance(artifact["license_declaration"], str)
            or not isinstance(artifact["top_level"], str)
            or not isinstance(artifact["license_path"], str)
            or not isinstance(artifact["license_sha256"], str)
        ):
            return False
        seen_ids.add(identifier)
        expected_paths.add(filename)
        expected_manifest = {
            "id": identifier,
            "name": artifact["name"],
            "version": artifact["version"],
            "path": filename,
            "sha256": digest,
            "bytes": size,
            "licenseDeclaration": artifact["license_declaration"],
            "licensePath": f"{artifact['top_level']}/{artifact['license_path']}",
            "licenseSha256": artifact["license_sha256"],
        }
        if manifest_by_id.get(identifier) != expected_manifest:
            return False
        candidate = root / filename
        if candidate.is_symlink():
            return False
        metadata = candidate.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size != size
            or _hash_file(candidate) != digest
        ):
            return False
    actual_paths: set[str] = set()
    for candidate in root.iterdir():
        if candidate.is_symlink() or not candidate.is_file():
            return False
        actual_paths.add(candidate.name)
    return seen_ids == set(manifest_by_id) and actual_paths == expected_paths


def _runtime_source_mapping_ok(path: Path) -> bool:
    value = _load_plain_json(path, max_bytes=64 * 1024)
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "babeldoc",
        "pymupdf",
    }:
        return False
    if value.get("schemaVersion") != 1:
        return False
    babeldoc = value.get("babeldoc")
    if babeldoc != {
        "version": BABELDOC_VERSION,
        "sourceArchiveSha256": "dbd2a69ccaf6678c34089f8c422a38a0fa170f5fa88ee1313b4235103421a875",
        "runtimeArtifactSha256": "e7dcdd5b8213f657af1df68e329f92d0534c9b94c53fcd82e9e04c52060cb7d0",
        "runtimeArtifactUrl": "https://files.pythonhosted.org/packages/3e/b1/7036b4a5ec6fda008e161950ac619a6e989730a05f3a90fcf1e437f07dec/babeldoc-0.6.4-py3-none-any.whl",
        "payloadFiles": 350,
        "payloadInventorySha256": "1c297d90628a3ec1f95e261a72216fb56c92a6d867a38d7a95c7fe7be1fd9ef3",
        "recordFiles": 355,
        "recordVerified": True,
        "wheelLicenseSha256": "afca41723b45e26069f68d485bf906a202f892f90c801d3052f8e6296bb41454",
    }:
        return False
    pymupdf = value.get("pymupdf")
    if not isinstance(pymupdf, dict):
        return False
    architecture = pymupdf.get("architecture")
    runtime_hashes = {
        "x86_64": "69dfc78f206a96e5b3ac22741263ebab945fdf51f0dbe7c5757c3511b23d9d72",
        "aarch64": "e419b609996434a14a80fa060adec72c434a1cca6a511ec54db9841bc5d51b3c",
    }
    runtime_urls = {
        "x86_64": "https://files.pythonhosted.org/packages/2a/6b/3de1714d734ff949be1e90a22375d0598d3540b22ae73eb85c2d7d1f36a9/pymupdf-1.26.7-cp310-abi3-manylinux_2_28_x86_64.whl",
        "aarch64": "https://files.pythonhosted.org/packages/65/e7/47af26f3ac76be7ac3dd4d6cc7ee105948a8355d774e5ca39857bf91c11c/pymupdf-1.26.7-cp310-abi3-manylinux_2_28_aarch64.whl",
    }
    expected = {
        "version": PYMUPDF_VERSION,
        "sourceArchiveSha256": "71add8bdc8eb1aaa207c69a13400693f06ad9b927bea976f5d5ab9df0bb489c3",
        "runtimeArtifactSha256": runtime_hashes.get(architecture),
        "runtimeArtifactUrl": runtime_urls.get(architecture),
        "architecture": architecture,
        "directSourceFiles": 10,
        "directSourceInventorySha256": "c0d659ccc04978f27afcc528ec0f2b0449d1a7f9e76c49d52dfbba5343d45ace",
        "recordFiles": 112,
        "recordVerified": True,
        "generatedFiles": [
            "pymupdf/_build.py",
            "pymupdf/extra.py",
            "pymupdf/mupdf.py",
            "pymupdf/_extra.so",
            "pymupdf/_mupdf.so",
        ],
        "nativeLibraries": [
            "pymupdf/libmupdf.so.26.12",
            "pymupdf/libmupdfcpp.so.26.12",
        ],
        "buildMetadataSha256": "cbc07581332a1b30f6ae7cb3c20b4d2aa191393a50d6beefdebe72d66593a319",
        "pymupdfGitSha": "8264a4b3798d06ec44af2e0e9d2a13abbc94e97d",
        "swigVersion": "4.4.0",
        "wheelLicenseDeclarationSha256": "40e60697600535eabfb5ae05f72829d88cfe8d02dd4792f5a754f6f51dabe55b",
        "mupdf": {
            "version": "1.26.12",
            "sourceArchiveSha256": "6baf910928f404167ba49be6340195dec340795724722b331f5a2143f5aa0d01",
            "sourceUrl": "https://mupdf.com/downloads/archive/mupdf-1.26.12-source.tar.gz",
        },
    }
    runtime_machine = platform.machine().lower()
    runtime_machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(
        runtime_machine, runtime_machine
    )
    return (
        architecture in runtime_hashes
        and architecture == runtime_machine
        and pymupdf == expected
    )


def _provenance_ok(
    path: Path,
    *,
    build_manifest_path: Path,
    sbom_path: Path,
    asset_manifest_path: Path,
    source_manifest_path: Path,
    complete_source_manifest_path: Path,
    runtime_requirements_path: Path,
    build_requirements_path: Path,
    fork_patch_path: Path,
    upstream_lock_path: Path,
) -> bool:
    value = _load_plain_json(path)
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != 1
        or value.get("upstreamRevision") != UPSTREAM_REVISION
        or not isinstance(value.get("digests"), dict)
    ):
        return False
    actual = {
        "buildManifest": _hash_file(build_manifest_path),
        "runtimeRequirementsLock": _hash_file(runtime_requirements_path),
        "buildRequirementsLock": _hash_file(build_requirements_path),
        "upstreamLock": _hash_file(upstream_lock_path),
        "forkPatch": _hash_file(fork_patch_path),
        "sbom": _hash_file(sbom_path),
        "assetManifest": _hash_file(asset_manifest_path),
        "sourceManifest": _hash_file(source_manifest_path),
        "completeSourceManifest": _hash_file(complete_source_manifest_path),
    }
    return (
        value["digests"] == actual
        and actual["buildManifest"] == BUILD_MANIFEST_SHA256
        and actual["runtimeRequirementsLock"] == RUNTIME_REQUIREMENTS_SHA256
        and actual["buildRequirementsLock"] == BUILD_REQUIREMENTS_SHA256
        and actual["forkPatch"] == FORK_PATCH_SHA256
        and actual["upstreamLock"] == UPSTREAM_LOCK_SHA256
    )


def _linux_sandbox_checks() -> dict[str, bool]:
    result = {
        "non_root": os.geteuid() != 0,
        "no_new_privileges": False,
        "capabilities_dropped": False,
        "seccomp_enabled": False,
        "root_read_only": False,
        "input_read_only": False,
        "output_writable": False,
        "tmp_isolated": False,
        "config_isolated": False,
        "babeldoc_cache_isolated": False,
        "pdf2zh_cache_isolated": False,
    }
    try:
        fields = {}
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value.strip()
        result["no_new_privileges"] = fields.get("NoNewPrivs") == "1"
        result["capabilities_dropped"] = int(fields.get("CapEff", "-1"), 16) == 0
        result["seccomp_enabled"] = fields.get("Seccomp") == "2"
    except (OSError, ValueError):
        pass
    try:
        mounts = {}
        for line in (
            Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
        ):
            before, after = line.split(" - ", 1)
            before_fields = before.split()
            after_fields = after.split()
            if len(before_fields) >= 6 and after_fields:
                mounts[before_fields[4]] = {
                    "options": set(before_fields[5].split(",")),
                    "filesystem": after_fields[0],
                }
        result["root_read_only"] = "ro" in mounts.get("/", {}).get("options", set())
        input_directory_read_only = "ro" in mounts.get(str(INPUT_ROOT), {}).get(
            "options", set()
        ) and not os.access(INPUT_ROOT, os.W_OK)
        input_files_read_only = all(
            "ro" in mounts.get(str(path), {}).get("options", set())
            and not os.access(path, os.W_OK)
            for path in (INPUT_ROOT / "request.json", INPUT_ROOT / "source.pdf")
        )
        result["input_read_only"] = input_directory_read_only or input_files_read_only
        result["output_writable"] = "rw" in mounts.get(str(OUTPUT_ROOT), {}).get(
            "options", set()
        ) and os.access(OUTPUT_ROOT, os.W_OK)
        result["tmp_isolated"] = (
            mounts.get("/tmp", {}).get("filesystem") == "tmpfs"
            and "rw" in mounts.get("/tmp", {}).get("options", set())
            and os.access("/tmp", os.W_OK)
        )
        result["config_isolated"] = (
            mounts.get(str(CONFIG_ROOT), {}).get("filesystem") == "tmpfs"
            and "rw" in mounts.get(str(CONFIG_ROOT), {}).get("options", set())
            and os.access(CONFIG_ROOT, os.W_OK)
        )
        babeldoc_cache_mount = str(RUNTIME_BABELDOC_CACHE_ROOT)
        result["babeldoc_cache_isolated"] = (
            mounts.get(babeldoc_cache_mount, {}).get("filesystem") == "tmpfs"
            and "rw" in mounts.get(babeldoc_cache_mount, {}).get("options", set())
            and os.access(babeldoc_cache_mount, os.W_OK)
        )
        pdf2zh_cache_mount = str(RUNTIME_PDF2ZH_CACHE_ROOT)
        result["pdf2zh_cache_isolated"] = (
            mounts.get(pdf2zh_cache_mount, {}).get("filesystem") == "tmpfs"
            and "rw" in mounts.get(pdf2zh_cache_mount, {}).get("options", set())
            and os.access(pdf2zh_cache_mount, os.W_OK)
        )
    except OSError:
        pass
    return result


def check_readiness(
    *,
    build_manifest_path: Path = BUILD_MANIFEST_PATH,
    sbom_path: Path = SBOM_PATH,
    asset_manifest_path: Path = ASSET_MANIFEST_PATH,
    asset_root: Path = ASSET_ROOT,
    runtime_cache_root: Path = RUNTIME_BABELDOC_CACHE_ROOT,
    provider_path: Path = PROVIDER_SECRET_PATH,
    provenance_path: Path = PROVENANCE_PATH,
    source_root: Path = SOURCE_ROOT,
    source_manifest_path: Path = SOURCE_MANIFEST_PATH,
    complete_source_root: Path = COMPLETE_SOURCE_ROOT,
    complete_source_manifest_path: Path = COMPLETE_SOURCE_MANIFEST_PATH,
    source_artifacts_lock_path: Path = SOURCE_ARTIFACTS_LOCK_PATH,
    source_artifacts_root: Path = SOURCE_ARTIFACTS_ROOT,
    source_artifacts_manifest_path: Path = SOURCE_ARTIFACTS_MANIFEST_PATH,
    runtime_source_mapping_path: Path = RUNTIME_SOURCE_MAPPING_PATH,
    runtime_requirements_path: Path = RUNTIME_REQUIREMENTS_PATH,
    build_requirements_path: Path = BUILD_REQUIREMENTS_PATH,
    fork_patch_path: Path = FORK_PATCH_PATH,
    upstream_lock_path: Path = UPSTREAM_LOCK_PATH,
    source_revision: str | None = None,
    build_digest: str | None = None,
    image_digest: str | None = None,
    sbom_sha256: str | None = None,
    lock_sha256: str | None = None,
    require_linux_sandbox: bool = True,
    for_run: bool = False,
) -> ReadinessReport:
    versions = {
        "pdf2zh-next": _distribution_version("pdf2zh-next"),
        "BabelDOC": _distribution_version("BabelDOC"),
        "PyMuPDF": _distribution_version("PyMuPDF"),
        "Python": platform.python_version(),
        "papertrans-babeldoc-worker": _distribution_version(
            "papertrans-babeldoc-worker"
        ),
    }
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool) -> None:
        checks.append({"name": name, "passed": bool(passed)})

    add("pdf2zh_next_version", versions["pdf2zh-next"] == ENGINE_VERSION)
    add("babeldoc_patched_version", versions["BabelDOC"] == BABELDOC_VERSION)
    add("pymupdf_approved_version", versions["PyMuPDF"] == PYMUPDF_VERSION)
    add("python_version", versions["Python"] == PYTHON_VERSION)
    add("adapter_version", versions["papertrans-babeldoc-worker"] == ADAPTER_VERSION)
    try:
        add("build_manifest", _manifest_ok(build_manifest_path))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        add("build_manifest", False)
    try:
        add("sbom", _sbom_ok(sbom_path))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        add("sbom", False)
    try:
        add("baked_assets", _asset_manifest_ok(asset_manifest_path, asset_root))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        add("baked_assets", False)
    try:
        add(
            "corresponding_source", _tree_manifest_ok(source_manifest_path, source_root)
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        add("corresponding_source", False)
    try:
        add(
            "complete_corresponding_source",
            _tree_manifest_ok(complete_source_manifest_path, complete_source_root),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        add("complete_corresponding_source", False)
    try:
        add(
            "source_artifacts_lock",
            _hash_file(source_artifacts_lock_path) == SOURCE_ARTIFACTS_LOCK_SHA256,
        )
    except OSError:
        add("source_artifacts_lock", False)
    try:
        add(
            "source_artifacts",
            _source_artifacts_ok(
                source_artifacts_lock_path,
                source_artifacts_root,
                source_artifacts_manifest_path,
            ),
        )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
    ):
        add("source_artifacts", False)
    try:
        add(
            "runtime_source_mapping",
            _runtime_source_mapping_ok(runtime_source_mapping_path),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        add("runtime_source_mapping", False)
    try:
        add(
            "build_provenance",
            _provenance_ok(
                provenance_path,
                build_manifest_path=build_manifest_path,
                sbom_path=sbom_path,
                asset_manifest_path=asset_manifest_path,
                source_manifest_path=source_manifest_path,
                complete_source_manifest_path=complete_source_manifest_path,
                runtime_requirements_path=runtime_requirements_path,
                build_requirements_path=build_requirements_path,
                fork_patch_path=fork_patch_path,
                upstream_lock_path=upstream_lock_path,
            ),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        add("build_provenance", False)
    if for_run:
        try:
            add("provider_secret", _provider_ok(provider_path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ContractError):
            add("provider_secret", False)

    resolved_source_revision = (
        source_revision
        if source_revision is not None
        else os.environ.get("PAPERTRANS_WORKER_SOURCE_REVISION")
    )
    resolved_build_digest = (
        build_digest
        if build_digest is not None
        else os.environ.get("PAPERTRANS_WORKER_BUILD_DIGEST")
    )
    resolved_image_digest = (
        image_digest
        if image_digest is not None
        else os.environ.get("PAPERTRANS_WORKER_IMAGE_DIGEST")
    )
    resolved_sbom_sha256 = (
        sbom_sha256
        if sbom_sha256 is not None
        else os.environ.get("PAPERTRANS_WORKER_SBOM_SHA256")
    )
    resolved_lock_sha256 = (
        lock_sha256
        if lock_sha256 is not None
        else os.environ.get("PAPERTRANS_WORKER_LOCK_SHA256")
    )
    # The common host contract uses a 64-hex source revision.  The digest of
    # UPSTREAM.lock commits to the exact Git SHA-1, tree, original source
    # hashes, dependency pins, and container inputs.
    add("source_revision_digest", resolved_source_revision == UPSTREAM_LOCK_SHA256)
    add(
        "immutable_build_digest",
        isinstance(resolved_build_digest, str)
        and IMAGE_DIGEST_RE.fullmatch(resolved_build_digest) is not None,
    )
    add(
        "immutable_image_digest",
        isinstance(resolved_image_digest, str)
        and IMAGE_DIGEST_RE.fullmatch(resolved_image_digest) is not None,
    )
    add("build_matches_image", resolved_build_digest == resolved_image_digest)
    try:
        add(
            "sbom_digest",
            isinstance(resolved_sbom_sha256, str)
            and resolved_sbom_sha256 == _hash_file(sbom_path),
        )
    except OSError:
        add("sbom_digest", False)
    add("lock_digest", resolved_lock_sha256 == RUNTIME_REQUIREMENTS_SHA256)

    if require_linux_sandbox:
        sandbox = _linux_sandbox_checks() if platform.system() == "Linux" else {}
        sandbox_checks = [
            "non_root",
            "no_new_privileges",
            "capabilities_dropped",
            "seccomp_enabled",
            "root_read_only",
            "tmp_isolated",
            "config_isolated",
            "babeldoc_cache_isolated",
            "pdf2zh_cache_isolated",
        ]
        if for_run:
            sandbox_checks.extend(["input_read_only", "output_writable"])
        for name in sandbox_checks:
            add(name, sandbox.get(name, False))

    runtime_assets_ok = False
    # Do not import executable engine code until every metadata, provenance,
    # secret, image and sandbox check above has passed.
    if all(check["passed"] for check in checks):
        try:
            runtime_assets_ok = _materialize_runtime_assets(
                asset_manifest_path,
                asset_root,
                runtime_cache_root,
                allow_mutable_cache=not for_run,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            runtime_assets_ok = False
    add("runtime_assets_materialized", runtime_assets_ok)

    api_ok = False
    if all(check["passed"] for check in checks):
        try:
            original_home = os.environ.get("HOME")
            original_tiktoken_cache = os.environ.get("TIKTOKEN_CACHE_DIR")
            # BabelDOC imports create cache.v1.db. Health runs in their own
            # process, so isolate that probe under /tmp and leave the shared
            # runtime cache as only the exact manifest-derived asset links.
            health_home = (
                tempfile.TemporaryDirectory(prefix="papertrans-health-", dir="/tmp")
                if not for_run
                else None
            )
            try:
                if health_home is not None:
                    os.environ["HOME"] = health_home.name
                    os.environ.pop("TIKTOKEN_CACHE_DIR", None)
                with silence_process_stdio():
                    module = importlib.import_module("pdf2zh_next.high_level")
                    function = getattr(module, "do_translate_async_stream")
                    parameters = list(inspect.signature(function).parameters)
                    api_ok = callable(function) and parameters == ["settings", "file"]
            finally:
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home
                if original_tiktoken_cache is None:
                    os.environ.pop("TIKTOKEN_CACHE_DIR", None)
                else:
                    os.environ["TIKTOKEN_CACHE_DIR"] = original_tiktoken_cache
                if health_home is not None:
                    health_home.cleanup()
        except (OSError, ImportError, AttributeError, TypeError, ValueError):
            api_ok = False
    add("documented_high_level_api", api_ok)

    return ReadinessReport(
        ready=all(check["passed"] for check in checks),
        checks=tuple(checks),
        versions=versions,
        source_revision=(
            resolved_source_revision
            if isinstance(resolved_source_revision, str)
            else None
        ),
        build_digest=(
            resolved_build_digest if isinstance(resolved_build_digest, str) else None
        ),
        image_digest=(
            resolved_image_digest if isinstance(resolved_image_digest, str) else None
        ),
        sbom_sha256=(
            resolved_sbom_sha256 if isinstance(resolved_sbom_sha256, str) else None
        ),
        lock_sha256=(
            resolved_lock_sha256 if isinstance(resolved_lock_sha256, str) else None
        ),
    )
