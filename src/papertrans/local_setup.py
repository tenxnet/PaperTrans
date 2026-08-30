"""Deterministic local dependency and Docling model preparation.

This module deliberately uses only the Python standard library.  The root
launcher can therefore report actionable setup failures before importing the
heavier PDF or MCP stacks.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence


MINIMUM_NODE_MAJOR = 22
STATE_SCHEMA_VERSION = 1
MODEL_LOCK_FILENAME = "docling-models.lock.json"
INJECTION_ENVIRONMENT_KEYS = {
    "BROWSER",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PYTHONHOME",
    "PYTHONPATH",
}
MODEL_SECRET_KEYS = {
    "CLOUDFLARE_API_TOKEN",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "OPENAI_API_KEY",
    "PAPERTRANS_TUNNEL_API_KEY",
}
RUNTIME_SECRET_NAME = re.compile(
    r"(?:^|_)(?:API_?KEY|CREDENTIALS?|PASSWORD|PRIVATE_?KEY|SECRET|TOKENS?)(?:_|$)",
    re.IGNORECASE,
)


class SetupError(RuntimeError):
    """A safe, user-actionable local setup failure."""


@dataclass(frozen=True)
class LocalPaths:
    repo_root: Path
    data_root: Path
    output_root: Path
    model_root: Path
    runtime_root: Path
    log_root: Path
    state_path: Path
    model_lock_path: Path

    @classmethod
    def create(
        cls,
        repo_root: Path,
        data_root: Path | None = None,
        output_root: Path | None = None,
        model_root: Path | None = None,
    ) -> "LocalPaths":
        canonical_repo = repo_root.expanduser().resolve()
        # Keep the managed path itself unresolved so a data/output symlink can
        # be detected and rejected before a write. The launcher already
        # canonicalizes the repository root itself.
        canonical_data = Path(
            os.path.abspath((data_root or canonical_repo / "data").expanduser())
        )
        canonical_output = Path(
            os.path.abspath((output_root or canonical_repo / "output").expanduser())
        )
        canonical_model = (
            model_root or canonical_data / "models" / "docling"
        ).expanduser()
        canonical_model = Path(os.path.abspath(canonical_model))
        runtime_root = canonical_data / "runtime"
        return cls(
            repo_root=canonical_repo,
            data_root=canonical_data,
            output_root=canonical_output,
            model_root=canonical_model,
            runtime_root=runtime_root,
            log_root=canonical_data / "logs",
            state_path=runtime_root / "setup-state.json",
            model_lock_path=canonical_repo / MODEL_LOCK_FILENAME,
        )


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int


def _is_regular_non_symlink(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(value.st_mode) and not stat.S_ISLNK(value.st_mode)


def validate_repository(paths: LocalPaths) -> None:
    if os.name != "posix":
        raise SetupError("the one-command launcher currently supports macOS and Linux only")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SetupError("do not run PaperTrans as root or with sudo")
    for name in ("pyproject.toml", "uv.lock", "package.json", "pnpm-lock.yaml"):
        candidate = paths.repo_root / name
        if not _is_regular_non_symlink(candidate):
            raise SetupError(f"required lock/config file must be a regular non-symlink: {candidate}")
    if not _is_regular_non_symlink(paths.model_lock_path):
        raise SetupError(f"Docling model lock is missing or unsafe: {paths.model_lock_path}")
    virtual_environment = paths.repo_root / ".venv"
    if virtual_environment.is_symlink():
        raise SetupError(f"refusing to use a symlinked virtual environment: {virtual_environment}")


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise SetupError(f"refusing to use a symlink for a managed directory: {path}")


def ensure_private_directory(path: Path) -> None:
    """Create a launcher-owned directory and reject a symlink at its root."""

    _reject_symlink(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink(path)
    if not path.is_dir():
        raise SetupError(f"managed path is not a directory: {path}")
    path.chmod(0o700)


def prepare_directories(paths: LocalPaths) -> None:
    for path in (
        paths.data_root,
        paths.output_root,
        paths.model_root.parent,
        paths.runtime_root,
        paths.log_root,
    ):
        ensure_private_directory(path)


def sanitized_environment(*, model_download: bool = False, runtime: bool = False) -> dict[str, str]:
    environment = os.environ.copy()
    for key in INJECTION_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    if model_download or runtime:
        for key in MODEL_SECRET_KEYS:
            environment.pop(key, None)
    if model_download:
        environment.update(
            {
                "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
            }
        )
    if runtime:
        for key in tuple(environment):
            if RUNTIME_SECRET_NAME.search(key):
                environment.pop(key, None)
        environment.update(
            {
                "HF_DATASETS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
    return environment


def _absolute_executable(name: str) -> str | None:
    executable = shutil.which(name)
    # Preserve an executable symlink's basename. Corepack uses the `pnpm`
    # shim name to select pnpm; resolving it to `corepack` changes behavior.
    return os.path.abspath(executable) if executable else None


def node_version() -> tuple[str, int]:
    executable = _absolute_executable("node")
    if executable is None:
        raise SetupError("Node.js 22 or newer is required")
    try:
        result = subprocess.run(
            [executable, "-p", "process.versions.node"],
            cwd="/",
            env=sanitized_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SetupError(f"could not run Node.js: {error}") from error
    version = result.stdout.strip()
    match = re.fullmatch(r"(\d+)(?:\.\d+){1,2}", version)
    if result.returncode != 0 or match is None:
        raise SetupError("could not determine the installed Node.js version")
    major = int(match.group(1))
    if major < MINIMUM_NODE_MAJOR:
        raise SetupError(f"Node.js {MINIMUM_NODE_MAJOR}+ is required (detected: {version})")
    return executable, major


def pnpm_command() -> tuple[str, ...]:
    executable = _absolute_executable("pnpm")
    if executable:
        return (executable,)
    corepack = _absolute_executable("corepack")
    if corepack:
        return (corepack, "pnpm")
    raise SetupError("pnpm 11 is required; enable Corepack or install pnpm")


def command_version(argv: Sequence[str], *, cwd: Path = Path("/")) -> str:
    try:
        result = subprocess.run(
            [*argv, "--version"],
            cwd=cwd,
            env=sanitized_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SetupError(f"could not run {' '.join(argv)}: {error}") from error
    version = result.stdout.strip()
    if result.returncode != 0 or not version:
        raise SetupError(f"could not determine {' '.join(argv)} version")
    return version


def validated_pnpm_version(argv: Sequence[str], repo_root: Path) -> str:
    version = command_version(argv, cwd=repo_root)
    if re.fullmatch(r"11(?:\.\d+){1,2}", version) is None:
        raise SetupError(f"pnpm 11 is required (detected: {version})")
    return version


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    timeout: float | None = None,
) -> CommandResult:
    if not argv or not Path(argv[0]).is_absolute():
        raise SetupError("internal error: child executable must be an absolute path")
    printable = " ".join(Path(value).name if index == 0 else value for index, value in enumerate(argv))
    print(f"[PaperTrans] {printable}", flush=True)
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=environment or sanitized_environment(),
            stdin=None,
            stdout=None,
            stderr=None,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SetupError(f"command failed: {printable}: {error}") from error
    if result.returncode != 0:
        raise SetupError(f"command failed with exit code {result.returncode}: {printable}")
    return CommandResult(tuple(argv), result.returncode)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_tree(paths: LocalPaths) -> str:
    digest = hashlib.sha256()
    roots = [
        paths.repo_root / "package.json",
        paths.repo_root / "pnpm-lock.yaml",
        paths.repo_root / "next.config.ts",
        paths.repo_root / "next.config.js",
        paths.repo_root / "tsconfig.json",
        paths.repo_root / "app",
        paths.repo_root / "components",
        paths.repo_root / "lib",
        paths.repo_root / "public",
    ]
    files: list[Path] = []
    for root in roots:
        if root.is_file() and not root.is_symlink():
            files.append(root)
        elif root.is_dir() and not root.is_symlink():
            files.extend(
                candidate
                for candidate in root.rglob("*")
                if candidate.is_file() and not candidate.is_symlink()
            )
    for candidate in sorted(files):
        relative = candidate.relative_to(paths.repo_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_hash_file(candidate).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _dependency_fingerprint(paths: LocalPaths, node_major: int, pnpm_version: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"node:{node_major}\npnpm:{pnpm_version}\n".encode("ascii"))
    for name in ("package.json", "pnpm-lock.yaml"):
        candidate = paths.repo_root / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_hash_file(candidate).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _build_fingerprint(paths: LocalPaths, dependency_fingerprint: str) -> str:
    digest = hashlib.sha256()
    digest.update(dependency_fingerprint.encode("ascii"))
    digest.update(b"\0")
    digest.update(_hash_tree(paths).encode("ascii"))
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


@contextlib.contextmanager
def setup_lock(paths: LocalPaths) -> Iterator[None]:
    ensure_private_directory(paths.runtime_root)
    lock_path = paths.runtime_root / "setup.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise SetupError(f"could not open setup lock: {error}") from error
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SetupError("another PaperTrans setup is already running") from error
        yield
    finally:
        os.close(descriptor)


def _load_model_lock(path: Path) -> dict[str, Any]:
    lock = _load_json(path)
    files = lock.get("files")
    if lock.get("schemaVersion") != 1 or not isinstance(files, list) or not files:
        raise SetupError(f"invalid Docling model lock: {path}")
    return lock


def verify_models(model_root: Path, lock_path: Path) -> tuple[bool, list[str]]:
    lock = _load_model_lock(lock_path)
    failures: list[str] = []
    _reject_symlink(model_root)
    for entry in lock["files"]:
        if not isinstance(entry, dict):
            failures.append("invalid model lock entry")
            continue
        relative = entry.get("path")
        expected_size = entry.get("size")
        expected_digest = entry.get("sha256")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(expected_size, int)
            or expected_size <= 0
            or not isinstance(expected_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None
        ):
            failures.append("invalid model lock entry")
            continue
        parts = Path(relative).parts
        current = model_root
        unsafe_parent = False
        for component in parts[:-1]:
            current = current / component
            try:
                info = current.lstat()
            except FileNotFoundError:
                unsafe_parent = True
                break
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                unsafe_parent = True
                break
        candidate = model_root.joinpath(*parts)
        if unsafe_parent or not _is_regular_non_symlink(candidate):
            failures.append(f"missing or unsafe: {relative}")
            continue
        actual_size = candidate.stat().st_size
        if actual_size != expected_size:
            failures.append(f"size mismatch: {relative}")
            continue
        if _hash_file(candidate) != expected_digest:
            failures.append(f"digest mismatch: {relative}")
    return not failures, failures


def ensure_models(paths: LocalPaths, *, offline: bool) -> None:
    ready, failures = verify_models(paths.model_root, paths.model_lock_path)
    if ready:
        print("[PaperTrans] Docling models: verified", flush=True)
        return
    if offline:
        detail = failures[0] if failures else "models are unavailable"
        raise SetupError(f"Docling models are not ready in offline mode ({detail})")

    parent = paths.model_root.parent
    ensure_private_directory(parent)
    staging = parent / f".docling-download-{uuid.uuid4().hex}"
    backup = parent / f".docling-invalid-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    ensure_private_directory(staging)
    try:
        _run(
            [
                os.path.abspath(sys.executable),
                "-m",
                "papertrans.local_model_download",
                "--lock",
                str(paths.model_lock_path),
                "--output-dir",
                str(staging),
            ],
            cwd=paths.repo_root,
            environment=sanitized_environment(model_download=True),
            timeout=1_800,
        )
        ready, failures = verify_models(staging, paths.model_lock_path)
        if not ready:
            detail = "; ".join(failures[:3])
            raise SetupError(f"downloaded Docling models do not match the release lock ({detail})")
        moved_existing = False
        if paths.model_root.exists():
            _reject_symlink(paths.model_root)
            os.replace(paths.model_root, backup)
            moved_existing = True
        try:
            os.replace(staging, paths.model_root)
        except BaseException:
            if moved_existing and not paths.model_root.exists():
                os.replace(backup, paths.model_root)
            raise
        if moved_existing:
            print(f"[PaperTrans] Previous invalid models retained at {backup}", flush=True)
        print("[PaperTrans] Docling models: downloaded and verified", flush=True)
    finally:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)


def _state_is_current(state: dict[str, Any], key: str, fingerprint: str) -> bool:
    section = state.get(key)
    return (
        isinstance(section, dict)
        and section.get("fingerprint") == fingerprint
        and section.get("ok") is True
    )


def _node_dependencies_installed(paths: LocalPaths) -> bool:
    node_modules = paths.repo_root / "node_modules"
    if not (node_modules / ".pnpm").is_dir():
        return False
    metadata_files = (
        node_modules / ".modules.yaml",
        node_modules / ".pnpm" / "lock.yaml",
    )
    if not all(path.is_file() and not path.is_symlink() for path in metadata_files):
        return False
    package = _load_json(paths.repo_root / "package.json")
    package_names: set[str] = set()
    for section_name in ("dependencies", "devDependencies"):
        section = package.get(section_name)
        if isinstance(section, dict):
            package_names.update(
                name for name in section if isinstance(name, str) and name
            )
    for name in package_names:
        if not node_modules.joinpath(*name.split("/")).is_dir():
            return False
    required_bins = {
        "next": "next",
        "typescript": "tsc",
    }
    return all(
        package_name not in package_names
        or (
            (node_modules / ".bin" / executable).is_file()
            and os.access(node_modules / ".bin" / executable, os.X_OK)
        )
        for package_name, executable in required_bins.items()
    )


def _python_package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def setup_status(paths: LocalPaths, *, dev: bool = False) -> dict[str, Any]:
    state = _load_json(paths.state_path)
    try:
        node_path, node_major = node_version()
        node_ok = True
        node_detail = f"{node_path} (major {node_major})"
    except SetupError as error:
        node_ok = False
        node_major = 0
        node_detail = str(error)
    try:
        pnpm = pnpm_command()
        pnpm_version = validated_pnpm_version(pnpm, paths.repo_root)
        pnpm_detail = f"{' '.join(pnpm)} ({pnpm_version})"
        pnpm_ok = True
    except SetupError as error:
        pnpm = ()
        pnpm_version = "unavailable"
        pnpm_detail = str(error)
        pnpm_ok = False
    dependency_fingerprint = _dependency_fingerprint(paths, node_major, pnpm_version)
    build_fingerprint = _build_fingerprint(paths, dependency_fingerprint)
    dependencies_ok = (
        pnpm_ok
        and _node_dependencies_installed(paths)
        and _state_is_current(state, "node", dependency_fingerprint)
    )
    build_ok = dev or (
        (paths.repo_root / ".next" / "BUILD_ID").is_file()
        and _state_is_current(state, "webBuild", build_fingerprint)
    )
    try:
        models_ok, model_failures = verify_models(paths.model_root, paths.model_lock_path)
    except SetupError as error:
        models_ok, model_failures = False, [str(error)]
    python_versions = {
        name: _python_package_version(name) for name in ("papertrans", "mcp", "docling")
    }
    python_ok = all(version != "missing" for version in python_versions.values())
    return {
        "schemaVersion": STATE_SCHEMA_VERSION,
        "ready": all((node_ok, pnpm_ok, python_ok, dependencies_ok, build_ok, models_ok)),
        "node": {"ok": node_ok, "detail": node_detail},
        "pnpm": {"ok": pnpm_ok, "detail": pnpm_detail},
        "python": {
            "ok": python_ok,
            **python_versions,
        },
        "nodeDependencies": {"ok": dependencies_ok},
        "webBuild": {"ok": build_ok, "mode": "development" if dev else "production"},
        "doclingModels": {"ok": models_ok, "failures": model_failures[:5]},
        "statePath": str(paths.state_path),
    }


def ensure_setup(paths: LocalPaths, *, offline: bool, dev: bool = False) -> dict[str, Any]:
    validate_repository(paths)
    prepare_directories(paths)
    with setup_lock(paths):
        _, node_major = node_version()
        pnpm = pnpm_command()
        pnpm_version = validated_pnpm_version(pnpm, paths.repo_root)
        dependency_fingerprint = _dependency_fingerprint(paths, node_major, pnpm_version)
        build_fingerprint = _build_fingerprint(paths, dependency_fingerprint)
        state = _load_json(paths.state_path)

        node_modules_ready = (
            _node_dependencies_installed(paths)
            and _state_is_current(state, "node", dependency_fingerprint)
        )
        if not node_modules_ready:
            install_argv = [*pnpm, "install", "--frozen-lockfile", "--ignore-scripts"]
            if offline:
                install_argv.append("--offline")
            _run(install_argv, cwd=paths.repo_root)
            if not _node_dependencies_installed(paths):
                # pnpm can regard an interrupted linker state as current even
                # when root package links or .bin entries are missing. Force a
                # relink once, preserving the same frozen/offline policy.
                _run([*install_argv, "--force"], cwd=paths.repo_root)
                if not _node_dependencies_installed(paths):
                    raise SetupError(
                        "pnpm install completed without a supported linked dependency layout"
                    )
            state["node"] = {
                "fingerprint": dependency_fingerprint,
                "ok": True,
                "pnpm": pnpm_version,
            }
            atomic_write_json(paths.state_path, state)
        else:
            print("[PaperTrans] Node dependencies: current", flush=True)

        ensure_models(paths, offline=offline)
        state = _load_json(paths.state_path)
        state["models"] = {
            "docling": _load_model_lock(paths.model_lock_path).get("release"),
            "ok": True,
        }
        atomic_write_json(paths.state_path, state)

        if not dev:
            build_ready = (
                (paths.repo_root / ".next" / "BUILD_ID").is_file()
                and _state_is_current(state, "webBuild", build_fingerprint)
            )
            if not build_ready:
                _run([*pnpm, "build"], cwd=paths.repo_root)
                state = _load_json(paths.state_path)
                state["webBuild"] = {"fingerprint": build_fingerprint, "ok": True}
                atomic_write_json(paths.state_path, state)
            else:
                print("[PaperTrans] Web build: current", flush=True)
        else:
            print("[PaperTrans] Development mode: production Web build skipped", flush=True)

        state = _load_json(paths.state_path)
        state.update(
            {
                "schemaVersion": STATE_SCHEMA_VERSION,
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        atomic_write_json(paths.state_path, state)
    return setup_status(paths, dev=dev)
