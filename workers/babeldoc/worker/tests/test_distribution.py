from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile


WORKER_ROOT = Path(__file__).resolve().parents[1]


def test_built_worker_wheel_contains_apache_license(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "uv is required to verify the worker distribution"

    result = subprocess.run(
        [
            uv,
            "build",
            "--project",
            str(WORKER_ROOT),
            "--wheel",
            "--out-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    wheels = list(tmp_path.glob("papertrans_babeldoc_worker-*.whl"))
    assert len(wheels) == 1
    with ZipFile(wheels[0]) as wheel:
        license_members = [
            name
            for name in wheel.namelist()
            if name.endswith(".dist-info/licenses/LICENSE")
        ]
        assert len(license_members) == 1
        assert wheel.read(license_members[0]) == (WORKER_ROOT / "LICENSE").read_bytes()

        metadata_members = [
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        ]
        assert len(metadata_members) == 1
        metadata = wheel.read(metadata_members[0]).decode("utf-8")
        assert "License-Expression: Apache-2.0\n" in metadata
        assert "License-File: LICENSE\n" in metadata
