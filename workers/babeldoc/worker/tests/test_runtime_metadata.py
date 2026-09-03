from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_runtime_metadata.py"
SPEC = importlib.util.spec_from_file_location("papertrans_generate_runtime_metadata", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RUNTIME_METADATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME_METADATA)


class _Distribution:
    version = "1.0"
    metadata = {
        "Name": "papertrans-test-component",
        "License": "legacy value that must not override the SPDX expression",
        "License-Expression": "Apache-2.0",
    }


class _DistributionWithoutLicense:
    version = "2.0"
    metadata = {
        "Name": "papertrans-unlicensed-test-component",
        "License": "UNKNOWN",
    }


class _DistributionWithLicenseClassifier:
    version = "3.0"
    metadata = {
        "Name": "papertrans-classifier-test-component",
        "License": "UNKNOWN",
        "Classifier": "License :: OSI Approved :: MIT License",
    }


PEEWEE_LICENSE = """Copyright (c) 2010 Charles Leifer

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


class _PeeweeDistribution:
    version = "4.4.0"
    metadata = {
        "Name": "peewee",
        "License-File": "LICENSE",
    }

    @staticmethod
    def read_text(filename: str) -> str | None:
        return PEEWEE_LICENSE if filename == "licenses/LICENSE" else None


class _UnexpectedPeeweeDistribution(_PeeweeDistribution):
    version = "4.4.1"


def _asset_inventory_sha256(inventory: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(
            inventory,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def test_sbom_prefers_pep639_license_expression(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(RUNTIME_METADATA, "EXPECTED", {})
    monkeypatch.setattr(
        RUNTIME_METADATA.importlib.metadata,
        "distributions",
        lambda: [_Distribution()],
    )
    output = tmp_path / "sbom.cdx.json"

    RUNTIME_METADATA.generate_sbom(output)

    sbom = json.loads(output.read_text(encoding="utf-8"))
    assert sbom["components"][0]["licenses"] == [{"expression": "Apache-2.0"}]


def test_sbom_preserves_license_classifier_without_guessing_expression(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(RUNTIME_METADATA, "EXPECTED", {})
    monkeypatch.setattr(
        RUNTIME_METADATA.importlib.metadata,
        "distributions",
        lambda: [_DistributionWithLicenseClassifier()],
    )
    output = tmp_path / "sbom.cdx.json"

    RUNTIME_METADATA.generate_sbom(output)

    sbom = json.loads(output.read_text(encoding="utf-8"))
    assert sbom["components"][0]["licenses"] == [
        {
            "license": {
                "name": "License :: OSI Approved :: MIT License",
            }
        }
    ]


def test_sbom_uses_verified_exact_version_license_override(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(RUNTIME_METADATA, "EXPECTED", {})
    monkeypatch.setattr(
        RUNTIME_METADATA.importlib.metadata,
        "distributions",
        lambda: [_PeeweeDistribution()],
    )
    output = tmp_path / "sbom.cdx.json"

    RUNTIME_METADATA.generate_sbom(output)

    component = json.loads(output.read_text(encoding="utf-8"))["components"][0]
    assert component["licenses"] == [{"expression": "MIT"}]
    assert component["properties"] == [
        {
            "name": "papertrans:license-override-source",
            "value": "https://github.com/coleifer/peewee/blob/942d9c510ed7accce6fc65afee6023d25a69d5fa/LICENSE",
        },
        {
            "name": "papertrans:license-override-wheel-sha256",
            "value": "c4d6bc13d9a9b22fe691f695f5a5d716645b4bd5387fef27b9cb53ef14fae1e1",
        },
        {
            "name": "papertrans:license-override-license-file-sha256",
            "value": "3740096125b08735a247b8dd08cd82e0ba984d3bebd9221d378576637e5240da",
        },
    ]
    requirements_lock = SCRIPT.parents[1] / "requirements.lock"
    assert (
        "--hash=sha256:c4d6bc13d9a9b22fe691f695f5a5d716645b4bd5387fef27b9cb53ef14fae1e1"
        in requirements_lock.read_text(encoding="utf-8")
    )


def test_sbom_does_not_apply_license_override_to_other_version(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(RUNTIME_METADATA, "EXPECTED", {})
    monkeypatch.setattr(
        RUNTIME_METADATA.importlib.metadata,
        "distributions",
        lambda: [_UnexpectedPeeweeDistribution()],
    )

    with pytest.raises(SystemExit, match="missing dependency license metadata"):
        RUNTIME_METADATA.generate_sbom(tmp_path / "sbom.cdx.json")


def test_sbom_rejects_license_override_when_file_digest_changes(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(RUNTIME_METADATA, "EXPECTED", {})
    monkeypatch.setattr(
        RUNTIME_METADATA.importlib.metadata,
        "distributions",
        lambda: [_PeeweeDistribution()],
    )
    monkeypatch.setattr(
        _PeeweeDistribution,
        "read_text",
        staticmethod(lambda _filename: PEEWEE_LICENSE + "modified\n"),
    )

    with pytest.raises(SystemExit, match="audited license override mismatch"):
        RUNTIME_METADATA.generate_sbom(tmp_path / "sbom.cdx.json")


def test_sbom_fails_closed_when_dependency_license_metadata_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(RUNTIME_METADATA, "EXPECTED", {})
    monkeypatch.setattr(
        RUNTIME_METADATA.importlib.metadata,
        "distributions",
        lambda: [_DistributionWithoutLicense()],
    )

    output = tmp_path / "sbom.cdx.json"
    with pytest.raises(SystemExit, match="missing dependency license metadata"):
        RUNTIME_METADATA.generate_sbom(output)
    assert not output.exists()


def test_asset_manifest_binds_expected_paths_and_sha3(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    asset = root / "models" / "model.onnx"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"model")
    expected = {"models/model.onnx": hashlib.sha3_256(b"model").hexdigest()}
    output = tmp_path / "assets.manifest.json"

    RUNTIME_METADATA.generate_asset_manifest(
        root,
        output,
        expected,
        expected_bytes=5,
    )

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 2
    assert manifest["inventorySha256"] == _asset_inventory_sha256(expected)
    assert manifest["files"] == [
        {
            "path": "models/model.onnx",
            "sha256": hashlib.sha256(b"model").hexdigest(),
            "sha3_256": expected["models/model.onnx"],
            "bytes": 5,
        }
    ]


@pytest.mark.parametrize("change", ["content", "missing", "unexpected"])
def test_asset_manifest_rejects_inventory_drift(tmp_path: Path, change: str) -> None:
    root = tmp_path / "assets"
    asset = root / "fonts" / "font.ttf"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"font")
    expected = {"fonts/font.ttf": hashlib.sha3_256(b"font").hexdigest()}
    if change == "content":
        asset.write_bytes(b"changed")
    elif change == "missing":
        asset.unlink()
    else:
        (root / "unexpected").write_bytes(b"unexpected")

    with pytest.raises(SystemExit, match="BabelDOC asset"):
        RUNTIME_METADATA.generate_asset_manifest(
            root,
            tmp_path / "assets.manifest.json",
            expected,
            expected_bytes=4,
        )


def test_provenance_covers_complete_corresponding_source_manifest(
    tmp_path: Path,
) -> None:
    paths = {}
    for name in (
        "build_manifest",
        "requirements_lock",
        "build_requirements_lock",
        "upstream_lock",
        "patch",
        "sbom",
        "asset_manifest",
        "source_manifest",
        "complete_source_manifest",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode("ascii"))
        paths[name] = path
    provenance = tmp_path / "provenance.json"

    RUNTIME_METADATA.generate_provenance(
        SimpleNamespace(provenance=provenance, **paths)
    )

    digests = json.loads(provenance.read_text(encoding="utf-8"))["digests"]
    assert digests["completeSourceManifest"] == hashlib.sha256(
        paths["complete_source_manifest"].read_bytes()
    ).hexdigest()
    assert set(digests) == {
        "assetManifest",
        "buildManifest",
        "buildRequirementsLock",
        "completeSourceManifest",
        "forkPatch",
        "runtimeRequirementsLock",
        "sbom",
        "sourceManifest",
        "upstreamLock",
    }
