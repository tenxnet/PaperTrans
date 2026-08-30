from pathlib import Path

import pymupdf as fitz
import pytest

import papertrans.structure as structure_module
from papertrans.structure import (
    PdfRenderBudget,
    PdfRenderLimitError,
    is_near_certain_blank_pixmap,
    render_visual_objects,
)


def _structure() -> dict:
    return {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    {
                        "blockId": "embedded-label",
                        "hidden": True,
                        "suppressedVisualText": True,
                    }
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-1",
                        "kind": "figure",
                        "bboxNormalized": [0.0, 0.0, 1.0, 1.0],
                        "confidence": 0.95,
                    }
                ],
            }
        ],
        "warnings": [],
    }


def _write_pdf(path: Path, *, draw_sparse_line: bool = False) -> None:
    document = fitz.open()
    page = document.new_page(width=100, height=100)
    if draw_sparse_line:
        page.draw_line((10, 50), (90, 50), color=(0, 0, 0), width=0.1)
    document.save(path)
    document.close()


def test_render_visual_objects_filters_blank_crop_and_keeps_descendants_suppressed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "blank.pdf"
    _write_pdf(source)
    structure = _structure()

    rendered = render_visual_objects(source, structure, tmp_path / "assets")

    assert rendered == []
    assert structure["pages"][0]["visualObjects"] == []
    assert structure["pages"][0]["blockAssignments"][0]["hidden"] is True
    assert structure["pages"][0]["blockAssignments"][0]["suppressedVisualText"] is True
    assert structure["renderDiagnostics"]["filteredBlankVisuals"] == [
        {
            "objectId": "figure-1",
            "pageNumber": 1,
            "kind": "figure",
            "bboxNormalized": [0.0, 0.0, 1.0, 1.0],
            "reason": "Every rendered RGB sample was near-white (>= 250).",
        }
    ]
    assert "embedded descendants remain suppressed" in structure["warnings"][0]
    assert list((tmp_path / "assets").glob("*.png")) == []


def test_render_visual_objects_retains_a_sparse_black_rule(tmp_path: Path) -> None:
    source = tmp_path / "sparse-rule.pdf"
    _write_pdf(source, draw_sparse_line=True)
    structure = _structure()

    rendered = render_visual_objects(source, structure, tmp_path / "assets")

    assert len(rendered) == 1
    assert structure["pages"][0]["visualObjects"][0]["objectId"] == "figure-1"
    assert (tmp_path / rendered[0]["asset"]).is_file()
    assert "renderDiagnostics" not in structure


def test_render_visual_objects_rejects_pixel_budget_before_allocation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "large-crop.pdf"
    _write_pdf(source, draw_sparse_line=True)
    budget = PdfRenderBudget(
        max_renders=1,
        max_single_pixels=10_000,
        max_total_pixels=10_000,
        max_output_bytes=1024,
    )

    with pytest.raises(PdfRenderLimitError, match="per-render pixel limit"):
        render_visual_objects(
            source,
            _structure(),
            tmp_path / "assets",
            budget=budget,
        )

    assert budget.renders == 0
    assert list((tmp_path / "assets").glob("*.png")) == []


def test_render_visual_objects_removes_artifact_when_output_budget_is_exceeded(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bounded-output.pdf"
    _write_pdf(source, draw_sparse_line=True)
    budget = PdfRenderBudget(
        max_renders=1,
        max_single_pixels=100_000,
        max_total_pixels=100_000,
        max_output_bytes=1,
    )

    with pytest.raises(PdfRenderLimitError, match="artifact bytes"):
        render_visual_objects(
            source,
            _structure(),
            tmp_path / "assets",
            budget=budget,
        )

    assert budget.renders == 1
    assert budget.output_bytes == 0
    assert list((tmp_path / "assets").glob("*.png")) == []


def test_render_visual_objects_cleans_temporary_file_when_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "publish-failure.pdf"
    _write_pdf(source, draw_sparse_line=True)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(structure_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        render_visual_objects(source, _structure(), tmp_path / "assets")

    assert list((tmp_path / "assets").iterdir()) == []


def test_one_black_pixel_is_not_classified_as_blank() -> None:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
    pixmap.clear_with(255)
    assert is_near_certain_blank_pixmap(pixmap) is True

    pixmap.set_pixel(2, 2, (0, 0, 0))
    assert is_near_certain_blank_pixmap(pixmap) is False
