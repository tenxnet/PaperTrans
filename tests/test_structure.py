from pathlib import Path

import pymupdf as fitz

from papertrans.structure import (
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


def test_one_black_pixel_is_not_classified_as_blank() -> None:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
    pixmap.clear_with(255)
    assert is_near_certain_blank_pixmap(pixmap) is True

    pixmap.set_pixel(2, 2, (0, 0, 0))
    assert is_near_certain_blank_pixmap(pixmap) is False
