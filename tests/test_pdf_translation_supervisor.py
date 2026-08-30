from pathlib import Path

import pymupdf as fitz
import pytest

from papertrans.pdf_translation_supervisor import (
    ContainerWorkerProfile,
    PdfTranslationSupervisorError,
    container_command,
    make_candidate_request,
)


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


def _pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 72), "PaperTrans worker test")
    document.save(path)
    document.close()


def _profile(tmp_path: Path) -> ContainerWorkerProfile:
    font = tmp_path / "font.otf"
    font.write_bytes(b"font")
    import hashlib

    font_hash = hashlib.sha256(font.read_bytes()).hexdigest()
    return ContainerWorkerProfile(
        backend_id="papertrans-harumi",
        image=f"papertrans-harumi@sha256:{HEX_A}",
        source_revision=HEX_B,
        sbom_sha256=HEX_C,
        lock_sha256=HEX_A,
        font_path=font,
        font_sha256=font_hash,
    )


def test_candidate_request_is_source_bound_and_strict(tmp_path: Path):
    source = tmp_path / "source.pdf"
    _pdf(source)
    request = make_candidate_request(source, run_id="pdf-harumi-01")
    assert request["source"]["bytes"] == source.stat().st_size
    assert request["translation"]["targetLanguage"] == "ja"
    assert request["outputs"] == ["translated_mono_pdf"]


def test_container_command_has_hard_isolation_and_fixed_mounts(tmp_path: Path):
    request = tmp_path / "request.json"
    source = tmp_path / "source.pdf"
    request.write_text("{}", encoding="utf-8")
    source.write_bytes(b"%PDF-fixture")
    output = tmp_path / "staging"
    argv = container_command(
        "/usr/local/bin/docker",
        _profile(tmp_path),
        command="run",
        container_name="papertrans-run-01",
        request_path=request,
        source_path=source,
        output_path=output,
    )
    joined = " ".join(argv)
    assert "--network none" in joined
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "dst=/input/request.json,readonly" in joined
    assert "dst=/input/source.pdf,readonly" in joined
    assert "dst=/output" in joined
    assert "--pull=never" in argv


def test_mutable_images_and_networked_profiles_are_rejected(tmp_path: Path):
    profile = _profile(tmp_path)
    mutable = ContainerWorkerProfile(
        **{**profile.__dict__, "image": "papertrans-harumi:latest"}
    )
    with pytest.raises(PdfTranslationSupervisorError, match="immutable"):
        mutable.validate()
    networked = ContainerWorkerProfile(
        **{**profile.__dict__, "network": "bridge"}
    )
    with pytest.raises(PdfTranslationSupervisorError, match="network=none"):
        networked.validate()
