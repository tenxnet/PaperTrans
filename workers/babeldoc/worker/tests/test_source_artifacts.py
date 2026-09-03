from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import tomllib
from pathlib import Path

import pytest

BABELDOC_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BABELDOC_ROOT / "scripts" / "fetch_source_artifacts.py"
SPEC = importlib.util.spec_from_file_location("fetch_source_artifacts", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
source_artifacts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_artifacts)
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "verify_tree_manifest", BABELDOC_ROOT / "scripts" / "verify_tree_manifest.py"
)
assert VERIFY_SPEC is not None and VERIFY_SPEC.loader is not None
verify_tree_manifest = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(verify_tree_manifest)
MAPPING_SPEC = importlib.util.spec_from_file_location(
    "verify_installed_source_mapping",
    BABELDOC_ROOT / "scripts" / "verify_installed_source_mapping.py",
)
assert MAPPING_SPEC is not None and MAPPING_SPEC.loader is not None
installed_source_mapping = importlib.util.module_from_spec(MAPPING_SPEC)
MAPPING_SPEC.loader.exec_module(installed_source_mapping)


def _archive(path: Path, members: dict[str, bytes | tuple[str, str]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, value in members.items():
            if isinstance(value, tuple):
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = value[1]
                archive.addfile(info)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))


def _artifact(path: Path, license_bytes: bytes = b"license") -> dict[str, object]:
    return {
        "id": "fixture",
        "bytes": path.stat().st_size,
        "sha256": source_artifacts.hash_file(path),
        "top_level": "fixture-1.0",
        "license_path": "LICENSE",
        "license_sha256": hashlib.sha256(license_bytes).hexdigest(),
    }


def test_repository_source_lock_is_valid_and_matches_runtime_lock() -> None:
    artifacts = source_artifacts.load_lock(BABELDOC_ROOT / "source-artifacts.lock")
    source_artifacts.validate_requirements_lock(
        artifacts, BABELDOC_ROOT / "requirements.lock"
    )
    source_artifacts.validate_upstream_lock(
        BABELDOC_ROOT / "source-artifacts.lock", BABELDOC_ROOT / "UPSTREAM.lock"
    )
    assert [(item["id"], item["version"]) for item in artifacts] == [
        ("babeldoc", "0.6.4"),
        ("pymupdf", "1.26.7"),
        ("mupdf", "1.26.12"),
    ]
    by_id = {item["id"]: item for item in artifacts}
    assert by_id["babeldoc"]["runtime_artifact_sha256"] == [
        installed_source_mapping.BABELDOC_WHEEL_SHA256
    ]
    assert set(by_id["pymupdf"]["runtime_artifact_sha256"]) == set(
        item["sha256"]
        for item in installed_source_mapping.PYMUPDF_WHEEL_BY_MACHINE.values()
    )
    assert (
        by_id["mupdf"]["url"]
        == installed_source_mapping.EXPECTED_BUILD_METADATA["mupdf_location"]
    )


def test_requirements_validation_is_scoped_to_package_and_version(
    tmp_path: Path,
) -> None:
    source_hash = "a" * 64
    wheel_hash = "b" * 64
    artifact = {
        "id": "fixture",
        "name": "fixture-package",
        "version": "1.0",
        "kind": "python-sdist",
        "sha256": source_hash,
        "runtime_artifact_sha256": [wheel_hash],
    }
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "other-package==1.0 \\\n"
        f"    --hash=sha256:{source_hash} \\\n"
        f"    --hash=sha256:{wheel_hash}\n"
        "fixture-package==2.0 \\\n"
        f"    --hash=sha256:{source_hash} \\\n"
        f"    --hash=sha256:{wheel_hash}\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="does not match requirement version"):
        source_artifacts.validate_requirements_lock([artifact], lock)


def test_requirements_validation_ignores_hashes_in_comments(tmp_path: Path) -> None:
    source_hash = "a" * 64
    wheel_hash = "b" * 64
    artifact = {
        "id": "fixture",
        "name": "fixture-package",
        "version": "1.0",
        "kind": "python-sdist",
        "sha256": source_hash,
        "runtime_artifact_sha256": [wheel_hash],
    }
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "fixture-package==1.0 \\\n"
        f"    # --hash=sha256:{source_hash} \\\n"
        f"    # --hash=sha256:{wheel_hash}\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="not pinned in requirements.lock"):
        source_artifacts.validate_requirements_lock([artifact], lock)


def _install_report(machine: str) -> dict[str, object]:
    selected = installed_source_mapping.PYMUPDF_WHEEL_BY_MACHINE[machine]
    return {
        "version": "1",
        "install": [
            {
                "download_info": {
                    "url": installed_source_mapping.BABELDOC_WHEEL["url"],
                    "archive_info": {
                        "hashes": {
                            "sha256": installed_source_mapping.BABELDOC_WHEEL["sha256"]
                        }
                    },
                },
                "metadata": {"name": "BabelDOC", "version": "0.6.4"},
            },
            {
                "download_info": {
                    "url": selected["url"],
                    "archive_info": {"hashes": {"sha256": selected["sha256"]}},
                },
                "metadata": {"name": "PyMuPDF", "version": "1.26.7"},
            },
        ],
    }


def test_install_report_records_the_actual_audited_wheels(tmp_path: Path) -> None:
    report_path = tmp_path / "pip-report.json"
    report_path.write_text(json.dumps(_install_report("x86_64")), encoding="utf-8")

    observed = installed_source_mapping._load_install_report(report_path, "x86_64")

    assert (
        observed["babeldoc"]["sha256"] == installed_source_mapping.BABELDOC_WHEEL_SHA256
    )
    assert observed["pymupdf"] == {
        **installed_source_mapping.PYMUPDF_WHEEL_BY_MACHINE["x86_64"],
        "version": "1.26.7",
    }


@pytest.mark.parametrize("change", ["sdist", "wrong_hash", "wrong_architecture"])
def test_install_report_rejects_unmapped_runtime_artifacts(
    tmp_path: Path, change: str
) -> None:
    report = _install_report("x86_64")
    pymupdf = report["install"][1]
    if change == "sdist":
        pymupdf["download_info"]["url"] = (
            "https://files.pythonhosted.org/packages/48/d6/09b28f027b510838559f7748807192149c419b30cb90e6d5f0cf916dc9dc/"
            "pymupdf-1.26.7.tar.gz"
        )
        pymupdf["download_info"]["archive_info"]["hashes"]["sha256"] = (
            "71add8bdc8eb1aaa207c69a13400693f06ad9b927bea976f5d5ab9df0bb489c3"
        )
    elif change == "wrong_hash":
        pymupdf["download_info"]["archive_info"]["hashes"]["sha256"] = "0" * 64
    else:
        selected = installed_source_mapping.PYMUPDF_WHEEL_BY_MACHINE["aarch64"]
        pymupdf["download_info"]["url"] = selected["url"]
        pymupdf["download_info"]["archive_info"]["hashes"]["sha256"] = selected[
            "sha256"
        ]
    report_path = tmp_path / "pip-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(SystemExit, match="did not install the audited pymupdf wheel"):
        installed_source_mapping._load_install_report(report_path, "x86_64")


def test_source_lock_rejects_invalid_builds_for_relationship(tmp_path: Path) -> None:
    text = (BABELDOC_ROOT / "source-artifacts.lock").read_text(encoding="utf-8")
    changed = text.replace(
        'builds_for = "PyMuPDF==1.26.7"', 'builds_for = "PyMuPDF==9.9"'
    )
    lock = tmp_path / "source-artifacts.lock"
    lock.write_text(changed, encoding="utf-8")

    with pytest.raises(SystemExit, match="invalid builds_for relationship"):
        source_artifacts.load_lock(lock)


def test_source_output_rejects_unexpected_or_symlinked_entries(tmp_path: Path) -> None:
    artifacts = source_artifacts.load_lock(BABELDOC_ROOT / "source-artifacts.lock")
    output = tmp_path / "source"
    output.mkdir()
    (output / "unexpected").write_text("not source", encoding="utf-8")
    with pytest.raises(SystemExit, match="unexpected source-artifact output entry"):
        source_artifacts.prepare_output_directory(output, artifacts)

    (output / "unexpected").unlink()
    (output / artifacts[0]["filename"]).symlink_to(tmp_path / "outside")
    with pytest.raises(SystemExit, match="unexpected source-artifact output entry"):
        source_artifacts.prepare_output_directory(output, artifacts)


def test_source_archive_validation_checks_license_and_internal_links(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "fixture.tar.gz"
    _archive(
        archive,
        {
            "fixture-1.0/LICENSE": b"license",
            "fixture-1.0/src/module.c": b"source",
            "fixture-1.0/src/alias.c": ("symlink", "module.c"),
        },
    )
    source_artifacts.validate_archive(archive, _artifact(archive))


def test_source_archive_validation_rejects_link_escape(tmp_path: Path) -> None:
    archive = tmp_path / "fixture.tar.gz"
    _archive(
        archive,
        {
            "fixture-1.0/LICENSE": b"license",
            "fixture-1.0/src/escape": ("symlink", "../../../outside"),
        },
    )
    with pytest.raises(SystemExit, match="unsafe source archive link"):
        source_artifacts.validate_archive(archive, _artifact(archive))


def test_source_archive_validation_rejects_license_change(tmp_path: Path) -> None:
    archive = tmp_path / "fixture.tar.gz"
    _archive(archive, {"fixture-1.0/LICENSE": b"changed"})
    with pytest.raises(SystemExit, match="source license mismatch"):
        source_artifacts.validate_archive(archive, _artifact(archive))


def test_dockerfile_embeds_complete_corresponding_source_recipe() -> None:
    dockerfile = (BABELDOC_ROOT / "Dockerfile").read_text(encoding="utf-8")
    for required in (
        "/opt/papertrans/corresponding-source/pdf2zh-next",
        "/opt/papertrans/corresponding-source/upstream-archives",
        "/opt/papertrans/corresponding-source/patches",
        "/opt/papertrans/corresponding-source/scripts",
        "/opt/papertrans/corresponding-source/worker",
        "/opt/papertrans/corresponding-source/.dockerignore",
        "/opt/papertrans/corresponding-source/runtime-source-map.json",
        "/opt/papertrans/corresponding-source.manifest.json",
        "--complete-source-manifest /opt/papertrans/corresponding-source.manifest.json",
        "verify_installed_source_mapping.py",
    ):
        assert required in dockerfile


def test_dockerfile_cannot_override_recorded_base_or_claim_incomplete_license() -> None:
    dockerfile = (BABELDOC_ROOT / "Dockerfile").read_text(encoding="utf-8")
    build_manifest = json.loads(
        (BABELDOC_ROOT / "build-manifest.json").read_text(encoding="utf-8")
    )
    upstream_lock = tomllib.loads(
        (BABELDOC_ROOT / "UPSTREAM.lock").read_text(encoding="utf-8")
    )
    from_images = [
        line.split()[1] for line in dockerfile.splitlines() if line.startswith("FROM ")
    ]
    assert len(from_images) == 3
    assert set(from_images) == {build_manifest["baseImage"]}
    assert build_manifest["baseImage"] == upstream_lock["container"]["base_image"]
    assert "ARG PYTHON_IMAGE" not in dockerfile
    assert "ARG SOURCE_DATE_EPOCH" not in dockerfile
    assert "org.opencontainers.image.licenses" not in dockerfile
    assert "--report /build/runtime-install-report.json" in dockerfile
    assert "--only-binary=BabelDOC,PyMuPDF" in dockerfile
    assert dockerfile.count("--no-compile") == 3
    assert "--install-report /build/runtime-install-report.json" in dockerfile
    assert dockerfile.splitlines()[0] == (
        "# syntax=docker/dockerfile:1.7@sha256:"
        "a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e"
    )


def test_complete_source_tree_manifest_verifier_fails_on_change(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_file = source_root / "source.py"
    source_file.write_text("original\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    revision = "f" * 40
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "upstreamRevision": revision,
                "files": [
                    {
                        "path": "source.py",
                        "bytes": 9,
                        "sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    verify_tree_manifest.verify_tree(source_root, manifest, revision)
    source_file.write_text("modified\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="source-tree file differs"):
        verify_tree_manifest.verify_tree(source_root, manifest, revision)
