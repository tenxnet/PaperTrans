from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "papertrans"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _launcher_environment(
    tmp_path: Path,
    *,
    include_uv: bool = True,
    include_node: bool = True,
    node_major: str = "22",
    platform: str = "Darwin",
) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    record = tmp_path / "uv-record.json"

    _write_executable(
        fake_bin / "uname",
        "import os\nprint(os.environ['FAKE_UNAME'])\n",
    )
    if include_node:
        _write_executable(
            fake_bin / "node",
            "import os\nprint(os.environ['FAKE_NODE_MAJOR'])\n",
        )
    if include_uv:
        _write_executable(
            fake_bin / "uv",
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['UV_RECORD']).write_text(\n"
            "    json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}),\n"
            "    encoding='utf-8',\n"
            ")\n",
        )

    environment = os.environ.copy()
    environment.update(
        {
            "FAKE_NODE_MAJOR": node_major,
            "FAKE_UNAME": platform,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "UV_RECORD": str(record),
        }
    )
    return environment, record


def _run_launcher(
    tmp_path: Path,
    *arguments: str,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(LAUNCHER), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_launcher_is_executable() -> None:
    assert LAUNCHER.stat().st_mode & stat.S_IXUSR


def test_launcher_delegates_from_repo_root_with_pdf_extras_and_literal_arguments(
    tmp_path: Path,
) -> None:
    environment, record = _launcher_environment(tmp_path)
    injection_marker = tmp_path / "injected"
    literal_argument = f"$(touch {injection_marker})"

    result = _run_launcher(
        tmp_path,
        "start",
        "--no-browser",
        "path with spaces",
        literal_argument,
        environment=environment,
    )

    assert result.returncode == 0, result.stderr
    invocation = json.loads(record.read_text(encoding="utf-8"))
    assert invocation == {
        "argv": [
            "run",
            "--frozen",
            "--extra",
            "mcp",
            "--extra",
            "docling",
            "python",
            "-m",
            "papertrans.local_app",
            "start",
            "--no-browser",
            "path with spaces",
            literal_argument,
        ],
        "cwd": str(REPO_ROOT),
    }
    assert not injection_marker.exists()


def test_launcher_passes_offline_to_uv_and_local_app(tmp_path: Path) -> None:
    environment, record = _launcher_environment(tmp_path, platform="Linux")

    result = _run_launcher(
        tmp_path,
        "start",
        "--offline",
        "--no-browser",
        environment=environment,
    )

    assert result.returncode == 0, result.stderr
    invocation = json.loads(record.read_text(encoding="utf-8"))
    assert invocation["argv"] == [
        "run",
        "--offline",
        "--frozen",
        "--extra",
        "mcp",
        "--extra",
        "docling",
        "python",
        "-m",
        "papertrans.local_app",
        "start",
        "--offline",
        "--no-browser",
    ]


def test_launcher_reports_missing_uv(tmp_path: Path) -> None:
    environment, record = _launcher_environment(tmp_path, include_uv=False)

    result = _run_launcher(tmp_path, "start", environment=environment)

    assert result.returncode == 10
    assert "uv is required" in result.stderr
    assert not record.exists()


def test_launcher_reports_missing_node(tmp_path: Path) -> None:
    environment, record = _launcher_environment(tmp_path, include_node=False)

    result = _run_launcher(tmp_path, "start", environment=environment)

    assert result.returncode == 10
    assert "Node.js 22 or newer is required" in result.stderr
    assert not record.exists()


def test_launcher_rejects_old_node(tmp_path: Path) -> None:
    environment, record = _launcher_environment(tmp_path, node_major="21")

    result = _run_launcher(tmp_path, "start", environment=environment)

    assert result.returncode == 10
    assert "detected: 21" in result.stderr
    assert not record.exists()


def test_launcher_rejects_unsupported_platform(tmp_path: Path) -> None:
    environment, record = _launcher_environment(tmp_path, platform="Windows_NT")

    result = _run_launcher(tmp_path, "start", environment=environment)

    assert result.returncode == 10
    assert "supports macOS and Linux only" in result.stderr
    assert "Windows_NT" in result.stderr
    assert not record.exists()


def test_launcher_rejects_symlinked_release_lock(tmp_path: Path) -> None:
    environment, record = _launcher_environment(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    launcher = checkout / "papertrans"
    shutil.copy2(LAUNCHER, launcher)
    for name in ("pyproject.toml", "uv.lock", "package.json", "pnpm-lock.yaml"):
        (checkout / name).write_text("test\n", encoding="utf-8")
    replacement = tmp_path / "model-lock.json"
    replacement.write_text("{}\n", encoding="utf-8")
    lock = checkout / "docling-models.lock.json"
    lock.symlink_to(replacement)

    result = subprocess.run(
        [str(launcher), "doctor"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 10
    assert "missing or unsafe" in result.stderr
    assert not record.exists()
