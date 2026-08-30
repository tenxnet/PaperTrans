from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from papertrans import pdf_import_status
from papertrans.local_setup import LocalPaths


def _write_import_lock(paths: LocalPaths, value: dict[str, object]) -> Path:
    paths.output_root.mkdir(parents=True, exist_ok=True)
    lock_path = paths.output_root / ".papertrans-pdf-import.lock"
    lock_path.mkdir(mode=0o700)
    (lock_path / f"{value['owner']}.json").write_text(
        json.dumps(value), encoding="utf-8"
    )
    return lock_path


def _write_legacy_import_lock(paths: LocalPaths, value: dict[str, object]) -> Path:
    paths.output_root.mkdir(parents=True, exist_ok=True)
    lock_path = paths.output_root / ".papertrans-pdf-import.lock"
    lock_path.write_text(json.dumps(value), encoding="utf-8")
    return lock_path


def test_pdf_import_status_is_idle_without_a_lock(tmp_path: Path) -> None:
    status = pdf_import_status.inspect_pdf_import(LocalPaths.create(tmp_path).output_root)

    assert status["active"] is False
    assert status["state"] == "idle"


def test_pdf_import_status_reports_a_running_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LocalPaths.create(tmp_path)
    _write_import_lock(paths, {"owner": "test", "createdAt": 1, "pid": 4321})
    observed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, signal: observed.append((pid, signal)))

    status = pdf_import_status.inspect_pdf_import(paths.output_root)

    assert status["active"] is True
    assert status["state"] == "running"
    assert observed == [((-4321 if os.name != "nt" else 4321), 0)]


def test_pdf_import_status_reports_an_exited_process_group_as_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LocalPaths.create(tmp_path)
    _write_import_lock(paths, {"owner": "test", "createdAt": 1, "pid": 4321})

    def missing_process(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", missing_process)

    status = pdf_import_status.inspect_pdf_import(paths.output_root)

    assert status["active"] is False
    assert status["state"] == "stale"


def test_pdf_import_status_reports_a_recent_directory_lock_as_starting(
    tmp_path: Path,
) -> None:
    paths = LocalPaths.create(tmp_path)
    _write_import_lock(
        paths,
        {"owner": "test", "createdAt": int(time.time() * 1000), "pid": None},
    )

    status = pdf_import_status.inspect_pdf_import(paths.output_root)

    assert status["active"] is True
    assert status["state"] == "starting"


def test_pdf_import_status_reports_an_old_setup_lock_as_stale(
    tmp_path: Path,
) -> None:
    paths = LocalPaths.create(tmp_path)
    _write_import_lock(paths, {"owner": "test", "createdAt": 1, "pid": None})

    status = pdf_import_status.inspect_pdf_import(paths.output_root)

    assert status["active"] is False
    assert status["state"] == "stale"


def test_pdf_import_status_supports_the_legacy_single_file_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LocalPaths.create(tmp_path)
    _write_legacy_import_lock(
        paths, {"owner": "legacy", "createdAt": 1, "pid": 4321}
    )
    monkeypatch.setattr(os, "kill", lambda _pid, _signal: None)

    status = pdf_import_status.inspect_pdf_import(paths.output_root)

    assert status["active"] is True
    assert status["state"] == "running"


def test_pdf_import_status_fails_closed_for_a_recent_invalid_lock(
    tmp_path: Path,
) -> None:
    paths = LocalPaths.create(tmp_path)
    paths.output_root.mkdir(parents=True, exist_ok=True)
    lock_path = paths.output_root / ".papertrans-pdf-import.lock"
    lock_path.mkdir()
    (lock_path / "invalid.json").write_text("not-json", encoding="utf-8")

    status = pdf_import_status.inspect_pdf_import(paths.output_root)

    assert status["active"] is True
    assert status["state"] == "unknown"
