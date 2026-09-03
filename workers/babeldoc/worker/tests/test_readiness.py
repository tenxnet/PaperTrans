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
from papertrans_babeldoc_worker import readiness
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
    asset_inventory = {"model.onnx": hashlib.sha3_256(b"model").hexdigest()}
    asset_inventory_sha256 = hashlib.sha256(
        json.dumps(
            asset_inventory,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    asset_manifest = tmp_path / "assets.manifest.json"
    _write_json(
        asset_manifest,
        {
            "schemaVersion": 2,
            "inventorySha256": asset_inventory_sha256,
            "files": [
                {
                    "path": "model.onnx",
                    "sha256": hashlib.sha256(b"model").hexdigest(),
                    "sha3_256": asset_inventory["model.onnx"],
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
    complete_source_root = tmp_path / "complete-source"
    complete_source_root.mkdir()
    complete_source_file = complete_source_root / "upstream-archive.tar.gz"
    complete_source_file.write_bytes(b"complete source")
    complete_source_manifest = tmp_path / "complete-source.manifest.json"
    _write_json(
        complete_source_manifest,
        {
            "schemaVersion": 1,
            "upstreamRevision": UPSTREAM_REVISION,
            "files": [
                {
                    "path": "upstream-archive.tar.gz",
                    "sha256": hashlib.sha256(
                        complete_source_file.read_bytes()
                    ).hexdigest(),
                    "bytes": complete_source_file.stat().st_size,
                }
            ],
        },
    )
    runtime_lock = BABELDOC_ROOT / "requirements.lock"
    source_artifacts_lock = BABELDOC_ROOT / "source-artifacts.lock"
    source_artifacts_root = tmp_path / "source-artifacts"
    source_artifacts_manifest = source_artifacts_root / "source-artifacts.manifest.json"
    runtime_source_mapping = tmp_path / "runtime-source-map.json"
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
                "runtimeRequirementsLock": hashlib.sha256(
                    runtime_lock.read_bytes()
                ).hexdigest(),
                "buildRequirementsLock": hashlib.sha256(
                    build_lock.read_bytes()
                ).hexdigest(),
                "upstreamLock": hashlib.sha256(upstream_lock.read_bytes()).hexdigest(),
                "forkPatch": hashlib.sha256(fork_patch.read_bytes()).hexdigest(),
                "sbom": hashlib.sha256(sbom.read_bytes()).hexdigest(),
                "assetManifest": hashlib.sha256(
                    asset_manifest.read_bytes()
                ).hexdigest(),
                "sourceManifest": hashlib.sha256(
                    source_manifest.read_bytes()
                ).hexdigest(),
                "completeSourceManifest": hashlib.sha256(
                    complete_source_manifest.read_bytes()
                ).hexdigest(),
            },
        },
    )
    return {
        "build": build,
        "sbom": sbom,
        "asset_root": asset_root,
        "asset_manifest": asset_manifest,
        "asset_inventory_sha256": asset_inventory_sha256,
        "runtime_cache_root": runtime_cache_root,
        "provider": provider,
        "source_root": source_root,
        "source_manifest": source_manifest,
        "complete_source_root": complete_source_root,
        "complete_source_manifest": complete_source_manifest,
        "runtime_lock": runtime_lock,
        "source_artifacts_lock": source_artifacts_lock,
        "source_artifacts_root": source_artifacts_root,
        "source_artifacts_manifest": source_artifacts_manifest,
        "runtime_source_mapping": runtime_source_mapping,
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


def _runtime_source_mapping_value() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "babeldoc": {
            "version": "0.6.4",
            "sourceArchiveSha256": "dbd2a69ccaf6678c34089f8c422a38a0fa170f5fa88ee1313b4235103421a875",
            "runtimeArtifactSha256": "e7dcdd5b8213f657af1df68e329f92d0534c9b94c53fcd82e9e04c52060cb7d0",
            "runtimeArtifactUrl": "https://files.pythonhosted.org/packages/3e/b1/7036b4a5ec6fda008e161950ac619a6e989730a05f3a90fcf1e437f07dec/babeldoc-0.6.4-py3-none-any.whl",
            "payloadFiles": 350,
            "payloadInventorySha256": "1c297d90628a3ec1f95e261a72216fb56c92a6d867a38d7a95c7fe7be1fd9ef3",
            "recordFiles": 355,
            "recordVerified": True,
            "wheelLicenseSha256": "afca41723b45e26069f68d485bf906a202f892f90c801d3052f8e6296bb41454",
        },
        "pymupdf": {
            "version": "1.26.7",
            "sourceArchiveSha256": "71add8bdc8eb1aaa207c69a13400693f06ad9b927bea976f5d5ab9df0bb489c3",
            "runtimeArtifactSha256": "69dfc78f206a96e5b3ac22741263ebab945fdf51f0dbe7c5757c3511b23d9d72",
            "runtimeArtifactUrl": "https://files.pythonhosted.org/packages/2a/6b/3de1714d734ff949be1e90a22375d0598d3540b22ae73eb85c2d7d1f36a9/pymupdf-1.26.7-cp310-abi3-manylinux_2_28_x86_64.whl",
            "architecture": "x86_64",
            "directSourceFiles": 10,
            "directSourceInventorySha256": "c0d659ccc04978f27afcc528ec0f2b0449d1a7f9e76c49d52dfbba5343d45ace",
            "recordFiles": 112,
            "recordVerified": True,
            "generatedFiles": [
                "pymupdf/_build.py",
                "pymupdf/extra.py",
                "pymupdf/mupdf.py",
                "pymupdf/_extra.so",
                "pymupdf/_mupdf.so",
            ],
            "nativeLibraries": [
                "pymupdf/libmupdf.so.26.12",
                "pymupdf/libmupdfcpp.so.26.12",
            ],
            "buildMetadataSha256": "cbc07581332a1b30f6ae7cb3c20b4d2aa191393a50d6beefdebe72d66593a319",
            "pymupdfGitSha": "8264a4b3798d06ec44af2e0e9d2a13abbc94e97d",
            "swigVersion": "4.4.0",
            "wheelLicenseDeclarationSha256": "40e60697600535eabfb5ae05f72829d88cfe8d02dd4792f5a754f6f51dabe55b",
            "mupdf": {
                "version": "1.26.12",
                "sourceArchiveSha256": "6baf910928f404167ba49be6340195dec340795724722b331f5a2143f5aa0d01",
                "sourceUrl": "https://mupdf.com/downloads/archive/mupdf-1.26.12-source.tar.gz",
            },
        },
    }


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
    source_artifacts_ok: bool = True,
    runtime_source_mapping_ok: bool = True,
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
        patch(
            "papertrans_babeldoc_worker.readiness.BABELDOC_ASSET_INVENTORY_SHA256",
            files["asset_inventory_sha256"],
        ),
        patch(
            "papertrans_babeldoc_worker.readiness._source_artifacts_ok",
            return_value=source_artifacts_ok,
        ),
        patch(
            "papertrans_babeldoc_worker.readiness._runtime_source_mapping_ok",
            return_value=runtime_source_mapping_ok,
        ),
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
            complete_source_root=files["complete_source_root"],
            complete_source_manifest_path=files["complete_source_manifest"],
            source_artifacts_lock_path=files["source_artifacts_lock"],
            source_artifacts_root=files["source_artifacts_root"],
            source_artifacts_manifest_path=files["source_artifacts_manifest"],
            runtime_source_mapping_path=files["runtime_source_mapping"],
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

    # Docker health checks and the one-job run use the same cache tmpfs.
    repeated = _check(files)
    assert repeated.ready
    assert runtime_asset.resolve() == (files["asset_root"] / "model.onnx").resolve()


def test_health_import_cache_side_effect_is_isolated_from_runtime_cache(
    tmp_path: Path,
) -> None:
    files = _fixture_files(tmp_path)
    import_homes: list[Path] = []

    def import_with_babeldoc_cache_side_effect(_name: str):
        home = Path(os.environ["HOME"])
        import_homes.append(home)
        cache = home / ".cache" / "babeldoc"
        cache.mkdir(parents=True)
        (cache / "cache.v1.db").write_bytes(b"sqlite")
        return SimpleNamespace(do_translate_async_stream=_fake_api)

    assert _check(files, api_import=import_with_babeldoc_cache_side_effect).ready
    assert import_homes
    assert all(home.name.startswith("papertrans-health-") for home in import_homes)
    assert not (files["runtime_cache_root"] / "cache.v1.db").exists()
    assert _check(files, api_import=import_with_babeldoc_cache_side_effect).ready


def test_health_remains_ready_while_one_job_owns_the_mutable_cache(
    tmp_path: Path,
) -> None:
    files = _fixture_files(tmp_path)
    assert _check(files).ready

    def run_import_with_babeldoc_cache_side_effect(_name: str):
        for suffix in ("", "-wal", "-shm"):
            (files["runtime_cache_root"] / f"cache.v1.db{suffix}").write_bytes(
                b"sqlite"
            )
        return SimpleNamespace(do_translate_async_stream=_fake_api)

    assert _check(
        files, for_run=True, api_import=run_import_with_babeldoc_cache_side_effect
    ).ready
    assert _check(files).ready

    # A second job may not inherit the first job's translation cache.
    assert not _check(files, for_run=True).ready


@pytest.mark.parametrize(
    "change", ["missing", "wrong_target", "additional", "regular", "extra_directory"]
)
def test_materialized_runtime_asset_links_fail_closed_on_drift(
    tmp_path: Path, change: str
) -> None:
    files = _fixture_files(tmp_path)
    assert _check(files).ready
    runtime_asset = files["runtime_cache_root"] / "model.onnx"
    if change == "missing":
        runtime_asset.unlink()
        (files["runtime_cache_root"] / "residual-directory").mkdir()
    elif change == "wrong_target":
        runtime_asset.unlink()
        wrong = tmp_path / "wrong-model.onnx"
        wrong.write_bytes(b"model")
        runtime_asset.symlink_to(wrong)
    elif change == "additional":
        (files["runtime_cache_root"] / "unexpected").write_bytes(b"unexpected")
    elif change == "extra_directory":
        (files["runtime_cache_root"] / "unexpected-directory").mkdir()
    else:
        runtime_asset.unlink()
        runtime_asset.write_bytes(b"model")

    report = _check(files)
    assert not report.ready
    assert not next(
        check
        for check in report.checks
        if check["name"] == "runtime_assets_materialized"
    )["passed"]


def test_preseeded_runtime_cache_is_refused(tmp_path: Path) -> None:
    files = _fixture_files(tmp_path)
    (files["runtime_cache_root"] / "untrusted").write_text("data", encoding="utf-8")
    report = _check(files)

    assert not report.ready
    assert not next(
        check
        for check in report.checks
        if check["name"] == "runtime_assets_materialized"
    )["passed"]


def test_source_artifact_gate_is_required_for_readiness(tmp_path: Path) -> None:
    report = _check(_fixture_files(tmp_path), source_artifacts_ok=False)
    assert not report.ready
    assert not next(
        check for check in report.checks if check["name"] == "source_artifacts"
    )["passed"]


def test_runtime_source_mapping_gate_is_required_for_readiness(tmp_path: Path) -> None:
    report = _check(_fixture_files(tmp_path), runtime_source_mapping_ok=False)
    assert not report.ready
    assert not next(
        check for check in report.checks if check["name"] == "runtime_source_mapping"
    )["passed"]


def test_runtime_source_mapping_record_is_exact_and_fails_on_drift(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-source-map.json"
    value = _runtime_source_mapping_value()
    _write_json(path, value)
    with patch("platform.machine", return_value="x86_64"):
        assert readiness._runtime_source_mapping_ok(path)

    with patch("platform.machine", return_value="arm64"):
        assert not readiness._runtime_source_mapping_ok(path)

    value["pymupdf"]["runtimeArtifactSha256"] = "0" * 64
    _write_json(path, value)
    with patch("platform.machine", return_value="x86_64"):
        assert not readiness._runtime_source_mapping_ok(path)


def _write_source_artifact_fixture(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "archives"
    root.mkdir()
    archive = root / "fixture-1.0.tar.gz"
    archive.write_bytes(b"preferred source")
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    license_sha256 = hashlib.sha256(b"license").hexdigest()
    lock = tmp_path / "source-artifacts.lock"
    lock.write_text(
        "schema_version = 1\n\n"
        "[[artifacts]]\n"
        'id = "fixture"\n'
        'name = "Fixture"\n'
        'version = "1.0"\n'
        'kind = "python-sdist"\n'
        'filename = "fixture-1.0.tar.gz"\n'
        'url = "https://files.pythonhosted.org/fixture-1.0.tar.gz"\n'
        f'sha256 = "{archive_sha256}"\n'
        f"bytes = {archive.stat().st_size}\n"
        'top_level = "fixture-1.0"\n'
        'license_declaration = "MIT (fixture)"\n'
        'license_path = "LICENSE"\n'
        f'license_sha256 = "{license_sha256}"\n'
        f'runtime_artifact_sha256 = ["{"b" * 64}"]\n',
        encoding="utf-8",
    )
    lock_sha256 = hashlib.sha256(lock.read_bytes()).hexdigest()
    manifest = root / "source-artifacts.manifest.json"
    _write_json(
        manifest,
        {
            "schemaVersion": 1,
            "lockSha256": lock_sha256,
            "files": [
                {
                    "id": "fixture",
                    "name": "Fixture",
                    "version": "1.0",
                    "path": archive.name,
                    "sha256": archive_sha256,
                    "bytes": archive.stat().st_size,
                    "licenseDeclaration": "MIT (fixture)",
                    "licensePath": "fixture-1.0/LICENSE",
                    "licenseSha256": license_sha256,
                }
            ],
        },
    )
    return {"root": root, "archive": archive, "lock": lock, "manifest": manifest}


def test_source_artifacts_are_checked_against_locked_bytes(tmp_path: Path) -> None:
    files = _write_source_artifact_fixture(tmp_path)
    lock_sha256 = hashlib.sha256(files["lock"].read_bytes()).hexdigest()
    with patch.object(readiness, "SOURCE_ARTIFACTS_LOCK_SHA256", lock_sha256):
        assert readiness._source_artifacts_ok(
            files["lock"], files["root"], files["manifest"]
        )
        files["archive"].write_bytes(b"different source")
        assert not readiness._source_artifacts_ok(
            files["lock"], files["root"], files["manifest"]
        )


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

    monkeypatch.setattr(
        cli, "check_readiness", lambda: _check(files, api_import=noisy_import)
    )
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
    assert not next(check for check in report.checks if check["name"] == failed_check)[
        "passed"
    ]


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
            complete_source_root=files["complete_source_root"],
            complete_source_manifest_path=files["complete_source_manifest"],
            source_artifacts_lock_path=files["source_artifacts_lock"],
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
    assert not next(
        check for check in report.checks if check["name"] == "babeldoc_patched_version"
    )["passed"]
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
            complete_source_root=files["complete_source_root"],
            complete_source_manifest_path=files["complete_source_manifest"],
            source_artifacts_lock_path=files["source_artifacts_lock"],
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
    assert not next(
        check for check in report.checks if check["name"] == "baked_assets"
    )["passed"]


def test_world_readable_provider_secret_is_refused(tmp_path: Path) -> None:
    files = _fixture_files(tmp_path)
    files["provider"].chmod(0o644)
    report = _check(files, for_run=True)
    assert not report.ready
    assert not next(
        check for check in report.checks if check["name"] == "provider_secret"
    )["passed"]


@pytest.mark.parametrize(
    ("target", "failed_check"),
    [
        ("source", "corresponding_source"),
        ("complete_source", "complete_corresponding_source"),
        ("build_manifest", "build_manifest"),
        ("runtime_lock", "build_provenance"),
        ("source_artifacts_lock", "source_artifacts_lock"),
        ("fork_patch", "build_provenance"),
        ("sbom", "build_provenance"),
    ],
)
def test_digest_gates_fail_closed(
    tmp_path: Path, target: str, failed_check: str
) -> None:
    files = _fixture_files(tmp_path)
    if target == "source":
        (files["source_root"] / "pyproject.toml").write_text(
            "tampered\n", encoding="utf-8"
        )
    elif target == "complete_source":
        (files["complete_source_root"] / "upstream-archive.tar.gz").write_bytes(
            b"tampered"
        )
    else:
        original_key = {
            "build_manifest": "build",
            "runtime_lock": "runtime_lock",
            "source_artifacts_lock": "source_artifacts_lock",
            "fork_patch": "fork_patch",
            "sbom": "sbom",
        }[target]
        original = files[original_key]
        tampered = tmp_path / f"tampered-{original.name}"
        tampered.write_bytes(original.read_bytes() + b" ")
        files[original_key] = tampered
    report = _check(files)
    assert not report.ready
    assert not next(check for check in report.checks if check["name"] == failed_check)[
        "passed"
    ]
