from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from papertrans import local_app
from papertrans.local_setup import LocalPaths


def test_no_arguments_defaults_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LocalPaths.create(tmp_path)
    observed: dict[str, object] = {}
    monkeypatch.setattr(local_app, "_paths", lambda _args: paths)
    monkeypatch.setattr(
        local_app,
        "ensure_setup",
        lambda _paths, offline, dev: {"ready": True},
    )
    monkeypatch.setattr(local_app, "validate_repository", lambda _paths: None)
    monkeypatch.setattr(local_app, "prepare_directories", lambda _paths: None)
    monkeypatch.setattr(local_app, "ensure_runtime_available", lambda _paths: None)

    class FakeSupervisor:
        def __init__(self, received_paths, options):
            observed["paths"] = received_paths
            observed["options"] = options

        def run(self):
            return 0

    monkeypatch.setattr(local_app, "LocalSupervisor", FakeSupervisor)

    assert local_app.main([]) == 0
    assert observed["paths"] == paths
    assert observed["options"].web_port == 3000
    assert observed["options"].mcp_port == 8000


def test_invalid_same_port_is_a_safe_cli_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = LocalPaths.create(tmp_path)
    monkeypatch.setattr(local_app, "_paths", lambda _args: paths)
    monkeypatch.setattr(
        local_app,
        "ensure_setup",
        lambda _paths, offline, dev: {"ready": True},
    )
    monkeypatch.setattr(local_app, "validate_repository", lambda _paths: None)
    monkeypatch.setattr(local_app, "prepare_directories", lambda _paths: None)
    monkeypatch.setattr(local_app, "ensure_runtime_available", lambda _paths: None)

    result = local_app.main(["start", "--web-port", "8123", "--mcp-port", "8123"])

    assert result == 10
    assert "must be different" in capsys.readouterr().err


def test_leading_option_uses_start_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LocalPaths.create(tmp_path)
    observed: dict[str, object] = {}
    monkeypatch.setattr(local_app, "_paths", lambda _args: paths)
    monkeypatch.setattr(local_app, "validate_repository", lambda _paths: None)
    monkeypatch.setattr(local_app, "prepare_directories", lambda _paths: None)
    monkeypatch.setattr(local_app, "ensure_runtime_available", lambda _paths: None)

    def setup(_paths, offline, dev):
        observed.update({"offline": offline, "dev": dev})
        return {"ready": True}

    monkeypatch.setattr(local_app, "ensure_setup", setup)
    monkeypatch.setattr(
        local_app,
        "LocalSupervisor",
        lambda _paths, _options: argparse.Namespace(run=lambda: 0),
    )

    assert local_app.main(["--offline", "--dev", "--no-browser"]) == 0
    assert observed == {"offline": True, "dev": True}


def test_already_running_is_rejected_before_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = LocalPaths.create(tmp_path)
    setup_called = False
    monkeypatch.setattr(local_app, "_paths", lambda _args: paths)
    monkeypatch.setattr(local_app, "validate_repository", lambda _paths: None)
    monkeypatch.setattr(local_app, "prepare_directories", lambda _paths: None)
    monkeypatch.setattr(
        local_app,
        "ensure_runtime_available",
        lambda _paths: (_ for _ in ()).throw(local_app.AlreadyRunning("already running")),
    )

    def setup(_paths, offline, dev):
        nonlocal setup_called
        setup_called = True
        return {"ready": True}

    monkeypatch.setattr(local_app, "ensure_setup", setup)

    assert local_app.main(["start", "--no-browser"]) == 11
    assert not setup_called
    assert "already running" in capsys.readouterr().err
