from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _build_wheel(uv: str, directory: Path) -> Path:
    wheel_directory = directory / "wheel"
    build = _run(
        [
            uv,
            "build",
            "--project",
            str(REPOSITORY_ROOT),
            "--wheel",
            "--out-dir",
            str(wheel_directory),
        ],
        cwd=directory,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(wheel_directory.glob("papertrans-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _build_release_artifacts(uv: str, directory: Path) -> tuple[Path, Path]:
    artifact_directory = directory / "artifacts"
    build = _run(
        [
            uv,
            "build",
            "--project",
            str(REPOSITORY_ROOT),
            "--out-dir",
            str(artifact_directory),
        ],
        cwd=directory,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(artifact_directory.glob("papertrans-*.whl"))
    sdists = list(artifact_directory.glob("papertrans-*.tar.gz"))
    assert len(wheels) == len(sdists) == 1
    return wheels[0], sdists[0]


def _create_environment(uv: str, directory: Path) -> Path:
    environment_directory = directory / "environment"
    create_environment = _run(
        [uv, "venv", "--python", sys.executable, str(environment_directory)],
        cwd=directory,
    )
    assert create_environment.returncode == 0, (
        create_environment.stdout + create_environment.stderr
    )
    return environment_directory


def _install_wheel_without_dependencies(
    uv: str, environment_directory: Path, wheel: Path, *, cwd: Path
) -> None:
    install = _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(environment_directory / "bin" / "python"),
            "--no-deps",
            str(wheel),
        ],
        cwd=cwd,
    )
    assert install.returncode == 0, install.stdout + install.stderr


def test_base_wheel_mcp_entrypoint_explains_missing_extra(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to verify the Python distribution"

    wheel = _build_wheel(uv, tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "Provides-Extra: docling" not in metadata
    assert "extra == 'docling'" not in metadata
    requirements = [
        line.lower() for line in metadata.splitlines() if line.startswith("Requires-Dist:")
    ]
    assert not any("docling" in line or "transformers" in line for line in requirements)
    environment_directory = _create_environment(uv, tmp_path)
    _install_wheel_without_dependencies(
        uv, environment_directory, wheel, cwd=tmp_path
    )

    result = _run(
        [str(environment_directory / "bin" / "papertrans-mcp"), "--help"],
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "pip install 'papertrans[mcp]'" in result.stderr
    assert "Traceback" not in result.stderr


def test_wheel_mcp_entrypoint_help_works_with_mcp_extra(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to verify the Python distribution"

    wheel = _build_wheel(uv, tmp_path)
    environment_directory = _create_environment(uv, tmp_path)
    requirements = tmp_path / "mcp-extra-requirements.txt"
    export = _run(
        [
            uv,
            "export",
            "--project",
            str(REPOSITORY_ROOT),
            "--quiet",
            "--frozen",
            "--extra",
            "mcp",
            "--no-dev",
            "--no-emit-project",
            "--output-file",
            str(requirements),
        ],
        cwd=tmp_path,
    )
    assert export.returncode == 0, export.stdout + export.stderr
    install_extra = _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(environment_directory / "bin" / "python"),
            "--require-hashes",
            "--requirement",
            str(requirements),
        ],
        cwd=tmp_path,
    )
    assert install_extra.returncode == 0, install_extra.stdout + install_extra.stderr
    _install_wheel_without_dependencies(
        uv, environment_directory, wheel, cwd=tmp_path
    )

    result = _run(
        [str(environment_directory / "bin" / "papertrans-mcp"), "--help"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage: " in result.stdout
    assert "Run the PaperTrans translation MCP server" in result.stdout
    assert "pip install 'papertrans[mcp]'" not in result.stderr


def test_built_artifacts_include_project_license_and_exclude_workers(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to verify the Python distribution"
    wheel, sdist = _build_release_artifacts(uv, tmp_path)
    expected_license = (REPOSITORY_ROOT / "LICENSE").read_bytes()

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        license_names = [
            name for name in names if name.endswith(".dist-info/licenses/LICENSE")
        ]
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        assert len(license_names) == 1
        assert archive.read(license_names[0]) == expected_license
        assert "License-Expression: Apache-2.0" in archive.read(
            metadata_name
        ).decode("utf-8")

    with tarfile.open(sdist, mode="r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        names = [member.name for member in members]
        assert not any("/workers/" in name for name in names)
        license_members = [member for member in members if member.name.endswith("/LICENSE")]
        assert len(license_members) == 1
        extracted = archive.extractfile(license_members[0])
        assert extracted is not None
        assert extracted.read() == expected_license
