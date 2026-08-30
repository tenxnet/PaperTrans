from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from papertrans import local_setup


def _fixture_paths(tmp_path: Path) -> local_setup.LocalPaths:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("pyproject.toml", "uv.lock", "pnpm-lock.yaml"):
        (repo / name).write_text(f"# {name}\n", encoding="utf-8")
    (repo / "package.json").write_text('{"name":"papertrans-test"}\n', encoding="utf-8")
    model_root = repo / "data" / "models" / "docling"
    model_root.mkdir(parents=True)
    model = model_root / "model.bin"
    model.write_bytes(b"pinned model")
    lock = {
        "schemaVersion": 1,
        "release": "test",
        "files": [
            {
                "path": "model.bin",
                "size": model.stat().st_size,
                "sha256": local_setup._hash_file(model),
            }
        ],
    }
    (repo / local_setup.MODEL_LOCK_FILENAME).write_text(
        json.dumps(lock), encoding="utf-8"
    )
    return local_setup.LocalPaths.create(repo)


def test_verify_models_requires_exact_size_and_digest(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    assert local_setup.verify_models(paths.model_root, paths.model_lock_path) == (True, [])

    (paths.model_root / "model.bin").write_bytes(b"tampered model")
    ready, failures = local_setup.verify_models(paths.model_root, paths.model_lock_path)

    assert not ready
    assert failures == ["size mismatch: model.bin"]


def test_verify_models_rejects_symlinked_file(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    model = paths.model_root / "model.bin"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(model.read_bytes())
    model.unlink()
    model.symlink_to(outside)

    ready, failures = local_setup.verify_models(paths.model_root, paths.model_lock_path)

    assert not ready
    assert failures == ["missing or unsafe: model.bin"]


def test_offline_missing_model_fails_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_paths(tmp_path)
    (paths.model_root / "model.bin").unlink()
    monkeypatch.setattr(
        local_setup,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("download must not run")
        ),
    )

    with pytest.raises(local_setup.SetupError, match="offline mode"):
        local_setup.ensure_models(paths, offline=True)


def test_atomic_state_is_private_and_complete(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "state.json"

    local_setup.atomic_write_json(target, {"ready": True, "value": "日本語"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "ready": True,
        "value": "日本語",
    }
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(target.parent.glob("*.tmp"))


def test_sanitized_runtime_environment_removes_injection_and_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NODE_OPTIONS", "--require=/tmp/evil.js")
    monkeypatch.setenv("PYTHONPATH", "/tmp/evil")
    monkeypatch.setenv("HF_TOKEN", "sentinel-secret")
    monkeypatch.setenv("PAPERTRANS_TUNNEL_API_KEY", "sentinel-tunnel")

    environment = local_setup.sanitized_environment(runtime=True)

    assert "NODE_OPTIONS" not in environment
    assert "PYTHONPATH" not in environment
    assert "HF_TOKEN" not in environment
    assert "PAPERTRANS_TUNNEL_API_KEY" not in environment
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"


def test_setup_uses_frozen_scriptless_offline_install_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_paths(tmp_path)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(local_setup, "validate_repository", lambda _paths: None)
    monkeypatch.setattr(local_setup, "node_version", lambda: ("/usr/bin/node", 22))
    monkeypatch.setattr(local_setup, "pnpm_command", lambda: ("/usr/bin/pnpm",))
    monkeypatch.setattr(local_setup, "validated_pnpm_version", lambda _argv, _root: "11.19.0")
    monkeypatch.setattr(local_setup, "ensure_models", lambda _paths, offline: None)
    monkeypatch.setattr(local_setup, "_python_package_version", lambda _name: "1.0")

    def fake_run(argv, *, cwd, environment=None, timeout=None):
        calls.append(tuple(argv))
        if "install" in argv:
            (paths.repo_root / "node_modules" / ".pnpm").mkdir(parents=True)
        if "build" in argv:
            (paths.repo_root / ".next").mkdir()
            (paths.repo_root / ".next" / "BUILD_ID").write_text("test\n", encoding="utf-8")
        return local_setup.CommandResult(tuple(argv), 0)

    monkeypatch.setattr(local_setup, "_run", fake_run)

    status_value = local_setup.ensure_setup(paths, offline=True)

    assert calls[0] == (
        "/usr/bin/pnpm",
        "install",
        "--frozen-lockfile",
        "--ignore-scripts",
        "--offline",
    )
    assert calls[1] == ("/usr/bin/pnpm", "build")
    assert status_value["ready"] is True
    state = json.loads(paths.state_path.read_text(encoding="utf-8"))
    assert state["node"]["ok"] is True
    assert state["webBuild"]["ok"] is True


def test_setup_lock_rejects_concurrent_owner(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)

    with local_setup.setup_lock(paths):
        with pytest.raises(local_setup.SetupError, match="already running"):
            with local_setup.setup_lock(paths):
                pass


def test_validate_repository_rejects_symlinked_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_paths(tmp_path)
    lock = paths.repo_root / "uv.lock"
    target = tmp_path / "real.lock"
    target.write_text("version = 1\n", encoding="utf-8")
    lock.unlink()
    lock.symlink_to(target)
    monkeypatch.setattr(os, "geteuid", lambda: 501)

    with pytest.raises(local_setup.SetupError, match="regular non-symlink"):
        local_setup.validate_repository(paths)


def test_pnpm_major_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_setup,
        "command_version",
        lambda _argv, *, cwd: "10.9.0",
    )

    with pytest.raises(local_setup.SetupError, match="pnpm 11"):
        local_setup.validated_pnpm_version(("/usr/bin/pnpm",), tmp_path)


def test_node_dependency_readiness_rejects_a_partial_install(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    (paths.repo_root / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "1", "react": "1"},
                "devDependencies": {"typescript": "1"},
            }
        ),
        encoding="utf-8",
    )
    node_modules = paths.repo_root / "node_modules"
    for relative in (".pnpm", "next", "react", "typescript"):
        (node_modules / relative).mkdir(parents=True, exist_ok=True)

    assert not local_setup._node_dependencies_installed(paths)

    binaries = node_modules / ".bin"
    binaries.mkdir()
    for name in ("next", "tsc"):
        executable = binaries / name
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)

    assert local_setup._node_dependencies_installed(paths)

    (node_modules / "react").rmdir()
    assert not local_setup._node_dependencies_installed(paths)


def test_setup_forces_one_relink_after_a_partial_pnpm_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_paths(tmp_path)
    (paths.repo_root / "package.json").write_text(
        json.dumps({"dependencies": {"next": "1"}}), encoding="utf-8"
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(local_setup, "validate_repository", lambda _paths: None)
    monkeypatch.setattr(local_setup, "node_version", lambda: ("/usr/bin/node", 22))
    monkeypatch.setattr(local_setup, "pnpm_command", lambda: ("/usr/bin/pnpm",))
    monkeypatch.setattr(
        local_setup, "validated_pnpm_version", lambda _argv, _root: "11.19.0"
    )
    monkeypatch.setattr(local_setup, "ensure_models", lambda _paths, offline: None)
    monkeypatch.setattr(local_setup, "_python_package_version", lambda _name: "1.0")

    def fake_run(argv, *, cwd, environment=None, timeout=None):
        calls.append(tuple(argv))
        node_modules = paths.repo_root / "node_modules"
        (node_modules / ".pnpm").mkdir(parents=True, exist_ok=True)
        if "--force" in argv:
            (node_modules / "next").mkdir()
            (node_modules / ".bin").mkdir()
            next_binary = node_modules / ".bin" / "next"
            next_binary.write_text("#!/bin/sh\n", encoding="utf-8")
            next_binary.chmod(0o755)
        if "build" in argv:
            (paths.repo_root / ".next").mkdir()
            (paths.repo_root / ".next" / "BUILD_ID").write_text(
                "test\n", encoding="utf-8"
            )
        return local_setup.CommandResult(tuple(argv), 0)

    monkeypatch.setattr(local_setup, "_run", fake_run)

    status_value = local_setup.ensure_setup(paths, offline=True)

    assert calls[1][-1] == "--force"
    assert status_value["ready"] is True


def test_ui_change_invalidates_build_but_not_node_install(tmp_path: Path) -> None:
    paths = _fixture_paths(tmp_path)
    app = paths.repo_root / "app"
    app.mkdir()
    page = app / "page.tsx"
    page.write_text("export default function Page() { return null }\n", encoding="utf-8")
    dependency_before = local_setup._dependency_fingerprint(paths, 22, "11.19.0")
    build_before = local_setup._build_fingerprint(paths, dependency_before)

    page.write_text("export default function Page() { return <main /> }\n", encoding="utf-8")
    dependency_after = local_setup._dependency_fingerprint(paths, 22, "11.19.0")
    build_after = local_setup._build_fingerprint(paths, dependency_after)

    assert dependency_after == dependency_before
    assert build_after != build_before
