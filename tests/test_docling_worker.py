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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "docling-document.json"
    output.write_text('{"previous": true}\n', encoding="utf-8")

    def partial_write(_value, handle, **_kwargs):
        handle.write("{")
        raise RuntimeError("serialization failed")

    monkeypatch.setattr(worker.json, "dump", partial_write)
    with pytest.raises(RuntimeError, match="serialization failed"):
        worker.write_json_atomic({"new": True}, output)

    assert json.loads(output.read_text()) == {"previous": True}
    assert list(tmp_path.glob(".docling-document.json.*.tmp")) == []
