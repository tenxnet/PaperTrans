from __future__ import annotations

import json
from pathlib import Path

import pytest

import papertrans.docling_worker as worker


def test_worker_main_writes_json_atomically_without_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "paper.pdf"
    output = tmp_path / "docling-document.json"
    document = {"schema_name": "DoclingDocument", "pages": {}}
    monkeypatch.setattr(worker, "convert_pdf_with_docling", lambda value: document)

    assert worker.main([str(source), str(output)]) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert json.loads(output.read_text()) == document
    assert list(tmp_path.glob(".docling-document.json.*.tmp")) == []


def test_worker_returns_dedicated_code_for_partial_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "paper.pdf"
    output = tmp_path / "docling-document.json"

    def partial(_source: Path):
        raise worker.DoclingPartialConversionError(
            "Docling conversion status partial_success: Page 1 failed to parse."
        )

    monkeypatch.setattr(worker, "convert_pdf_with_docling", partial)

    assert worker.main([str(source), str(output)]) == worker._DOCLING_PARTIAL_EXIT_CODE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Docling conversion status partial_success: Page 1 failed to parse.\n"
    )
    assert not output.exists()


def test_atomic_writer_preserves_previous_json_when_serialization_fails(
    tmp_path: Path,
) -> None:
    output = tmp_path / "docling-document.json"
    output.write_text('{"previous": true}\n', encoding="utf-8")
    circular: dict[str, object] = {}
    circular["self"] = circular

    with pytest.raises(ValueError, match="Circular reference"):
        worker.write_json_atomic(circular, output)

    assert json.loads(output.read_text()) == {"previous": True}
    assert list(tmp_path.glob(".docling-document.json.*.tmp")) == []


def test_atomic_writer_rejects_oversized_json_without_replacing_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "docling-document.json"
    output.write_text('{"previous": true}\n', encoding="utf-8")

    with pytest.raises(worker.DoclingResourceLimitError, match="exceeds 32 bytes"):
        worker.write_json_atomic({"text": "x" * 64}, output, max_bytes=32)

    assert json.loads(output.read_text()) == {"previous": True}
    assert list(tmp_path.glob(".docling-document.json.*.tmp")) == []


def test_worker_returns_dedicated_code_for_resource_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "docling-document.json"

    def limited(_source: Path):
        raise worker.DoclingResourceLimitError("PDF page count exceeds 300")

    monkeypatch.setattr(worker, "convert_pdf_with_docling", limited)

    assert worker.main([str(tmp_path / "paper.pdf"), str(output)]) == (
        worker._DOCLING_RESOURCE_LIMIT_EXIT_CODE
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "PDF page count exceeds 300\n"
    assert not output.exists()
