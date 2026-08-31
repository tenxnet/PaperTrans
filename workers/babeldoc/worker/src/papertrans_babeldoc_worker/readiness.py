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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    ADAPTER_VERSION,
    ASSET_MANIFEST_PATH,
    ASSET_ROOT,
    BABELDOC_VERSION,
    BUILD_MANIFEST_PATH,
    BUILD_MANIFEST_SHA256,
    BUILD_REQUIREMENTS_PATH,
    BUILD_REQUIREMENTS_SHA256,
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
    SBOM_PATH,
    SOURCE_MANIFEST_PATH,
    SOURCE_ROOT,
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
        and value.get("locks", {}).get("requirements.lock") == RUNTIME_REQUIREMENTS_SHA256
        and value.get("locks", {}).get("build-requirements.lock") == BUILD_REQUIREMENTS_SHA256
        and value.get("locks", {}).get("UPSTREAM.lock") == UPSTREAM_LOCK_SHA256
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
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        return False
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        return False
    root = asset_root.resolve(strict=True)
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            return False
        relative = entry["path"]
        if not isinstance(relative, str) or not relative or relative.startswith(("/", ".")):
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
        if type(entry["bytes"]) is not int or candidate.stat().st_size != entry["bytes"]:
            return False
        if not isinstance(entry["sha256"], str) or _hash_file(candidate) != entry["sha256"]:
            return False
    actual: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            return False
        if candidate.is_file():
            actual.add(candidate.relative_to(root).as_posix())
    return actual == seen


def _materialize_runtime_assets(
    manifest_path: Path,
    asset_root: Path,
    runtime_cache_root: Path,
) -> bool:
    """Expose verified immutable assets inside an empty writable cache tmpfs."""

    value = _load_plain_json(manifest_path)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        return False
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        return False
    immutable_root = asset_root.resolve(strict=True)
    if (
        runtime_cache_root.is_symlink()
        or not runtime_cache_root.is_dir()
        or not os.access(runtime_cache_root, os.W_OK)
        or any(runtime_cache_root.iterdir())
    ):
        return False
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            return False
        relative = entry["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith(("/", "."))
            or ".." in Path(relative).parts
            or relative in seen
        ):
            return False
        seen.add(relative)
        source = immutable_root / relative
        if source.is_symlink():
            return False
        resolved_source = source.resolve(strict=True)
        if immutable_root not in resolved_source.parents or not resolved_source.is_file():
            return False
        destination = runtime_cache_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.symlink_to(resolved_source)
    return True


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
        if candidate.stat().st_size != entry["bytes"] or _hash_file(candidate) != entry["sha256"]:
            return False
    actual_paths: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            return False
        if candidate.is_file():
            actual_paths.add(candidate.relative_to(root).as_posix())
    return actual_paths == expected_paths


def _provenance_ok(
    path: Path,
    *,
    build_manifest_path: Path,
    sbom_path: Path,
    asset_manifest_path: Path,
    source_manifest_path: Path,
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
    }
    return value["digests"] == actual and actual["buildManifest"] == BUILD_MANIFEST_SHA256 and actual[
        "runtimeRequirementsLock"
    ] == RUNTIME_REQUIREMENTS_SHA256 and actual["buildRequirementsLock"] == BUILD_REQUIREMENTS_SHA256 and actual[
        "forkPatch"
    ] == FORK_PATCH_SHA256 and actual["upstreamLock"] == UPSTREAM_LOCK_SHA256


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
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            before, after = line.split(" - ", 1)
            before_fields = before.split()
            after_fields = after.split()
            if len(before_fields) >= 6 and after_fields:
                mounts[before_fields[4]] = {
                    "options": set(before_fields[5].split(",")),
                    "filesystem": after_fields[0],
                }
        result["root_read_only"] = "ro" in mounts.get("/", {}).get("options", set())
        input_directory_read_only = (
            "ro" in mounts.get(str(INPUT_ROOT), {}).get("options", set())
            and not os.access(INPUT_ROOT, os.W_OK)
        )
        input_files_read_only = all(
            "ro" in mounts.get(str(path), {}).get("options", set())
            and not os.access(path, os.W_OK)
            for path in (INPUT_ROOT / "request.json", INPUT_ROOT / "source.pdf")
        )
        result["input_read_only"] = input_directory_read_only or input_files_read_only
        result["output_writable"] = (
            "rw" in mounts.get(str(OUTPUT_ROOT), {}).get("options", set())
            and os.access(OUTPUT_ROOT, os.W_OK)
        )
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
        "papertrans-babeldoc-worker": _distribution_version("papertrans-babeldoc-worker"),
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
        add("corresponding_source", _tree_manifest_ok(source_manifest_path, source_root))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        add("corresponding_source", False)
    try:
        add(
            "build_provenance",
            _provenance_ok(
                provenance_path,
                build_manifest_path=build_manifest_path,
                sbom_path=sbom_path,
                asset_manifest_path=asset_manifest_path,
                source_manifest_path=source_manifest_path,
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
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            runtime_assets_ok = False
    add("runtime_assets_materialized", runtime_assets_ok)

    api_ok = False
    if all(check["passed"] for check in checks):
        try:
            with silence_process_stdio():
                module = importlib.import_module("pdf2zh_next.high_level")
                function = getattr(module, "do_translate_async_stream")
                parameters = list(inspect.signature(function).parameters)
                api_ok = callable(function) and parameters == ["settings", "file"]
        except (ImportError, AttributeError, TypeError, ValueError):
            api_ok = False
    add("documented_high_level_api", api_ok)

    return ReadinessReport(
        ready=all(check["passed"] for check in checks),
        checks=tuple(checks),
        versions=versions,
        source_revision=(
            resolved_source_revision if isinstance(resolved_source_revision, str) else None
        ),
        build_digest=(resolved_build_digest if isinstance(resolved_build_digest, str) else None),
        image_digest=(resolved_image_digest if isinstance(resolved_image_digest, str) else None),
        sbom_sha256=(resolved_sbom_sha256 if isinstance(resolved_sbom_sha256, str) else None),
        lock_sha256=(resolved_lock_sha256 if isinstance(resolved_lock_sha256, str) else None),
    )
