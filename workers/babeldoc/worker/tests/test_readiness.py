from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from papertrans.pdf_translation_worker import validate_backend_health
from papertrans_babeldoc_worker import cli
from papertrans_babeldoc_worker.constants import ADAPTER_VERSION
from papertrans_babeldoc_worker.constants import BABELDOC_VERSION
from papertrans_babeldoc_worker.constants import ENGINE_VERSION
from papertrans_babeldoc_worker.constants import FORK_PATCH_SHA256
from papertrans_babeldoc_worker.constants import PYMUPDF_VERSION
from papertrans_babeldoc_worker.constants import PYTHON_VERSION
from papertrans_babeldoc_worker.constants import UPSTREAM_REVISION
from papertrans_babeldoc_worker.constants import UPSTREAM_LOCK_SHA256
from papertrans_babeldoc_worker.readiness import check_readiness

BABELDOC_ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: object, mode: int = 0o600) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(mode)


def _fixture_files(tmp_path: Path) -> dict[str, Path]:
    build = BABELDOC_ROOT / "build-manifest.json"
    sbom = tmp_path / "sbom.cdx.json"
    _write_json(
        sbom,
        {
            "bomFormat": "CycloneDX",
            "components": [
                {"name": "pdf2zh-next", "version": ENGINE_VERSION},
                {"name": "BabelDOC", "version": BABELDOC_VERSION},
                {"name": "PyMuPDF", "version": PYMUPDF_VERSION},
                {"name": "papertrans-babeldoc-worker", "version": ADAPTER_VERSION},
            ],
        },
    )
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    asset = asset_root / "model.onnx"
    asset.write_bytes(b"model")
    asset_manifest = tmp_path / "assets.manifest.json"
    _write_json(
        asset_manifest,
        {
            "schemaVersion": 1,
            "files": [
                {
                    "path": "model.onnx",
                    "sha256": hashlib.sha256(b"model").hexdigest(),
                    "bytes": 5,
                }
            ],
        },
    )
    runtime_cache_root = tmp_path / "runtime-cache"
    runtime_cache_root.mkdir()
    provider = tmp_path / "provider.json"
    _write_json(
        provider,
        {
            "schemaVersion": 1,
            "profileId": "evaluation-ja-v1",
            "providerId": "openai-compatible-local",
            "modelId": "test/model-v1",
            "baseUrl": "http://translation-gateway:8080/v1",
            "apiKey": "not-a-real-key",
            "qps": 1,
        },
        mode=0o600,
    )
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_file = source_root / "pyproject.toml"
    source_file.write_text("version = '2.9.0+papertrans.1'\n", encoding="utf-8")
    source_manifest = tmp_path / "source.manifest.json"
    _write_json(
        source_manifest,
        {
            "schemaVersion": 1,
            "upstreamRevision": UPSTREAM_REVISION,
            "files": [
                {
                    "path": "pyproject.toml",
                    "sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
                    "bytes": source_file.stat().st_size,
                }
            ],
        },
    )
    runtime_lock = BABELDOC_ROOT / "requirements.lock"
    build_lock = BABELDOC_ROOT / "build-requirements.lock"
    fork_patch = BABELDOC_ROOT / "patches" / "0001-papertrans-safe-dependencies.patch"
    upstream_lock = BABELDOC_ROOT / "UPSTREAM.lock"
    provenance = tmp_path / "provenance.json"
    _write_json(
        provenance,
        {
            "schemaVersion": 1,
            "upstreamRevision": UPSTREAM_REVISION,
            "digests": {
                "buildManifest": hashlib.sha256(build.read_bytes()).hexdigest(),
                "runtimeRequirementsLock": hashlib.sha256(runtime_lock.read_bytes()).hexdigest(),
                "buildRequirementsLock": hashlib.sha256(build_lock.read_bytes()).hexdigest(),
                "upstreamLock": hashlib.sha256(upstream_lock.read_bytes()).hexdigest(),
                "forkPatch": hashlib.sha256(fork_patch.read_bytes()).hexdigest(),
                "sbom": hashlib.sha256(sbom.read_bytes()).hexdigest(),
                "assetManifest": hashlib.sha256(asset_manifest.read_bytes()).hexdigest(),
                "sourceManifest": hashlib.sha256(source_manifest.read_bytes()).hexdigest(),
            },
        },
    )
    return {
        "build": build,
        "sbom": sbom,
        "asset_root": asset_root,
        "asset_manifest": asset_manifest,
        "runtime_cache_root": runtime_cache_root,
        "provider": provider,
        "source_root": source_root,
        "source_manifest": source_manifest,
        "runtime_lock": runtime_lock,
        "build_lock": build_lock,
        "fork_patch": fork_patch,
        "upstream_lock": upstream_lock,
        "provenance": provenance,
    }


def _versions(name: str) -> str:
    return {
        "pdf2zh-next": ENGINE_VERSION,
        "BabelDOC": BABELDOC_VERSION,
        "PyMuPDF": PYMUPDF_VERSION,
        "papertrans-babeldoc-worker": ADAPTER_VERSION,
    }[name]


def _fake_api(settings, file):
    del settings, file


def _check(
    files: dict[str, Path],
    *,
    for_run: bool = False,
    source_revision: str = UPSTREAM_LOCK_SHA256,
    build_digest: str = "sha256:" + "a" * 64,
    image_digest: str = "sha256:" + "a" * 64,
    sbom_sha256: str | None = None,
    lock_sha256: str | None = None,
    api_import=None,
):
    import_patch = (
        patch("importlib.import_module", side_effect=api_import)
        if api_import is not None
        else patch(
            "importlib.import_module",
            return_value=SimpleNamespace(do_translate_async_stream=_fake_api),
        )
    )
    with (
        patch("importlib.metadata.version", side_effect=_versions),
        patch("platform.python_version", return_value=PYTHON_VERSION),
        import_patch,
    ):
        return check_readiness(
            build_manifest_path=files["build"],
            sbom_path=files["sbom"],
            asset_manifest_path=files["asset_manifest"],
            asset_root=files["asset_root"],
            runtime_cache_root=files["runtime_cache_root"],
            provider_path=files["provider"],
            provenance_path=files["provenance"],
            source_root=files["source_root"],
            source_manifest_path=files["source_manifest"],
            runtime_requirements_path=files["runtime_lock"],
            build_requirements_path=files["build_lock"],
            fork_patch_path=files["fork_patch"],
            upstream_lock_path=files["upstream_lock"],
            source_revision=source_revision,
            build_digest=build_digest,
            image_digest=image_digest,
            sbom_sha256=(
                sbom_sha256
                if sbom_sha256 is not None
                else hashlib.sha256(files["sbom"].read_bytes()).hexdigest()
            ),
            lock_sha256=(
                lock_sha256
                if lock_sha256 is not None
                else hashlib.sha256(files["runtime_lock"].read_bytes()).hexdigest()
            ),
            require_linux_sandbox=False,
            for_run=for_run,
        )


def test_approved_runtime_is_ready(tmp_path: Path) -> None:
    files = _fixture_files(tmp_path)
    report = _check(files)
    assert report.ready
    assert all(check["passed"] for check in report.checks)
    runtime_asset = files["runtime_cache_root"] / "model.onnx"
    assert runtime_asset.is_symlink()
    assert runtime_asset.resolve() == (files["asset_root"] / "model.onnx").resolve()


def test_preseeded_runtime_cache_is_refused(tmp_path: Path) -> None:
    files = _fixture_files(tmp_path)
    (files["runtime_cache_root"] / "untrusted").write_text("data", encoding="utf-8")
    report = _check(files)

    assert not report.ready
    assert not next(
        check for check in report.checks if check["name"] == "runtime_assets_materialized"
    )["passed"]


def test_health_suppresses_native_import_stdio_and_emits_one_json_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    files = _fixture_files(tmp_path)

    def noisy_import(name: str):
        assert name == "pdf2zh_next.high_level"
        os.write(1, b"import-stdout-noise\n")
        os.write(2, b"import-stderr-noise\n")
        return SimpleNamespace(do_translate_async_stream=_fake_api)

    monkeypatch.setattr(cli, "check_readiness", lambda: _check(files, api_import=noisy_import))
    assert cli._health() == 0
    stdout, stderr = capfd.readouterr()

    assert stderr == ""
    lines = stdout.splitlines()
    assert len(lines) == 1
    document = json.loads(lines[0])
    validate_backend_health(
        document,
        approved_fork_revisions={FORK_PATCH_SHA256},
        required_outputs={"translated_mono_pdf", "translated_dual_pdf"},
    )


@pytest.mark.parametrize(
    ("overrides", "failed_check"),
    [
        ({"source_revision": "f" * 64}, "source_revision_digest"),
        ({"build_digest": "sha256:" + "b" * 64}, "build_matches_image"),
        ({"sbom_sha256": "f" * 64}, "sbom_digest"),
        ({"lock_sha256": "f" * 64}, "lock_digest"),
    ],
)
def test_host_provenance_digest_mismatch_is_refused(
    tmp_path: Path, overrides: dict[str, str], failed_check: str
) -> None:
    report = _check(_fixture_files(tmp_path), **overrides)
    assert not report.ready
    assert not next(check for check in report.checks if check["name"] == failed_check)["passed"]


def test_vulnerable_babeldoc_is_refused_without_api_import(tmp_path: Path) -> None:
    files = _fixture_files(tmp_path)

    def versions(name: str) -> str:
        return "0.6.2" if name == "BabelDOC" else _versions(name)

    with (
        patch("importlib.metadata.version", side_effect=versions),
        patch("platform.python_version", return_value=PYTHON_VERSION),
        patch("importlib.import_module") as importer,
    ):
        report = check_readiness(
            build_manifest_path=files["build"],
            sbom_path=files["sbom"],
            asset_manifest_path=files["asset_manifest"],
            asset_root=files["asset_root"],
            runtime_cache_root=files["runtime_cache_root"],
            provider_path=files["provider"],
            provenance_path=files["provenance"],
            source_root=files["source_root"],
            source_manifest_path=files["source_manifest"],
            runtime_requirements_path=files["runtime_lock"],
            build_requirements_path=files["build_lock"],
            fork_patch_path=files["fork_patch"],
            upstream_lock_path=files["upstream_lock"],
            source_revision=UPSTREAM_LOCK_SHA256,
            build_digest="sha256:" + "a" * 64,
            image_digest="sha256:" + "a" * 64,
            sbom_sha256=hashlib.sha256(files["sbom"].read_bytes()).hexdigest(),
            lock_sha256=hashlib.sha256(files["runtime_lock"].read_bytes()).hexdigest(),
            require_linux_sandbox=False,
        )
    assert not report.ready
    assert not next(check for check in report.checks if check["name"] == "babeldoc_patched_version")["passed"]
    # Version metadata is inspected before importing executable engine code.
    importer.assert_not_called()


def test_missing_sbom_and_mutable_image_ref_are_refused(tmp_path: Path) -> None:
    files = _fixture_files(tmp_path)
    missing_sbom_digest = hashlib.sha256(files["sbom"].read_bytes()).hexdigest()
    files["sbom"].unlink()
    with (
        patch("importlib.metadata.version", side_effect=_versions),
        patch("platform.python_version", return_value=PYTHON_VERSION),
        patch(
            "importlib.import_module",
            return_value=SimpleNamespace(do_translate_async_stream=_fake_api),
        ),
    ):
        report = check_readiness(
            build_manifest_path=files["build"],
            sbom_path=files["sbom"],
            asset_manifest_path=files["asset_manifest"],
            asset_root=files["asset_root"],
            runtime_cache_root=files["runtime_cache_root"],
            provider_path=files["provider"],
            provenance_path=files["provenance"],
            source_root=files["source_root"],
            source_manifest_path=files["source_manifest"],
            runtime_requirements_path=files["runtime_lock"],
            build_requirements_path=files["build_lock"],
            fork_patch_path=files["fork_patch"],
            upstream_lock_path=files["upstream_lock"],
            source_revision=UPSTREAM_LOCK_SHA256,
            build_digest="papertrans-babeldoc:latest",
            image_digest="papertrans-babeldoc:latest",
            sbom_sha256=missing_sbom_digest,
            lock_sha256=hashlib.sha256(files["runtime_lock"].read_bytes()).hexdigest(),
            require_linux_sandbox=False,
        )
    assert not report.ready
    failed = {check["name"] for check in report.checks if not check["passed"]}
    assert {"sbom", "immutable_image_digest"} <= failed


def test_asset_tampering_is_refused(tmp_path: Path) -> None:
    files = _fixture_files(tmp_path)
    (files["asset_root"] / "model.onnx").write_bytes(b"tampered")
    report = _check(files)
    assert not report.ready
    assert not next(check for check in report.checks if check["name"] == "baked_assets")["passed"]


def test_world_readable_provider_secret_is_refused(tmp_path: Path) -> None:
    files = _fixture_files(tmp_path)
    files["provider"].chmod(0o644)
    report = _check(files, for_run=True)
    assert not report.ready
    assert not next(check for check in report.checks if check["name"] == "provider_secret")["passed"]


@pytest.mark.parametrize(
    ("target", "failed_check"),
    [
        ("source", "corresponding_source"),
        ("build_manifest", "build_manifest"),
        ("runtime_lock", "build_provenance"),
        ("fork_patch", "build_provenance"),
        ("sbom", "build_provenance"),
    ],
)
def test_digest_gates_fail_closed(tmp_path: Path, target: str, failed_check: str) -> None:
    files = _fixture_files(tmp_path)
    if target == "source":
        (files["source_root"] / "pyproject.toml").write_text("tampered\n", encoding="utf-8")
    else:
        original_key = {
            "build_manifest": "build",
            "runtime_lock": "runtime_lock",
            "fork_patch": "fork_patch",
            "sbom": "sbom",
        }[target]
        original = files[original_key]
        tampered = tmp_path / f"tampered-{original.name}"
        tampered.write_bytes(original.read_bytes() + b" ")
        files[original_key] = tampered
    report = _check(files)
    assert not report.ready
    assert not next(check for check in report.checks if check["name"] == failed_check)["passed"]
