from __future__ import annotations

from pathlib import Path
from typing import Callable

import pymupdf as fitz

from papertrans.pdf_caption_refinement import refine_pdf_caption_texts


PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0


def _write_pdf(path: Path, draw: Callable[[fitz.Page], None]) -> None:
    document = fitz.open()
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    draw(page)
    document.save(path)
    document.close()


def _source_lines(path: Path) -> list[dict]:
    document = fitz.open(path)
    try:
        return [
            line
            for block in document[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
        ]
    finally:
        document.close()


def _bbox_union(*boxes: tuple[float, float, float, float]) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _inputs(blocks: list[dict], visual: dict) -> tuple[dict, dict]:
    evidence = {
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "widthPdf": PAGE_WIDTH,
                "heightPdf": PAGE_HEIGHT,
                "blocks": blocks,
            }
        ],
    }
    structure = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [],
                "visualObjects": [visual],
            }
        ]
    }
    return evidence, structure


def test_recovers_pdf_line_order_from_vertically_interleaved_caption_blocks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "interleaved-caption.pdf"

    def draw(page: fitz.Page) -> None:
        label = "Table 12:"
        page.insert_text(
            (108, 100),
            label,
            fontname="Times-Bold",
            fontsize=10,
        )
        prose_x = 108 + fitz.get_text_length(
            label, fontname="Times-Bold", fontsize=10
        ) + 8
        # Separate font/text operations reproduce the same-baseline fragments
        # emitted by TeX for bold labels followed by regular caption prose.
        page.insert_text(
            (prose_x, 100),
            "Comparison with prior work on VOC-",
            fontname="Times-Roman",
            fontsize=10,
        )
        page.insert_text(
            (108, 111),
            "2012. Our models were trained using ConvNets",
            fontname="Times-Roman",
            fontsize=10,
        )
        page.insert_text(
            (108, 122),
            "pre-trained on the extended dataset.",
            fontname="Times-Roman",
            fontsize=10,
        )
        # The first table row is close enough to tempt a loose line collector,
        # but its indentation and two-column baseline make it non-caption text.
        page.insert_text((177, 133), "Method", fontname="Times-Roman", fontsize=9)
        page.insert_text((351, 133), "Mean AP", fontname="Times-Roman", fontsize=9)

    _write_pdf(source, draw)
    lines = _source_lines(source)
    first_caption_line = _bbox_union(
        tuple(lines[0]["bbox"]), tuple(lines[1]["bbox"])
    )
    first_two = _bbox_union(
        tuple(first_caption_line), tuple(lines[2]["bbox"])
    )
    last_two = _bbox_union(
        tuple(lines[2]["bbox"]), tuple(lines[3]["bbox"])
    )
    evidence, structure = _inputs(
        [
            {
                "blockId": "caption-a",
                # Deliberately follows the bad fragment order seen from Docling.
                "text": "Table 12: Comparison with prior work on VOC- Our models were trained using ConvNets",
                "bboxPdf": first_two,
            },
            {
                "blockId": "caption-b",
                "text": "2012. pre-trained on the extended dataset.",
                "bboxPdf": last_two,
            },
        ],
        {
            "objectId": "table-any-parser-id",
            "kind": "table",
            "label": "Table 12",
            "captionBlockIds": ["caption-a", "caption-b"],
            "bboxNormalized": [0.17, 0.16, 0.72, 0.31],
        },
    )

    assert refine_pdf_caption_texts(source, evidence, structure) == {
        "table-any-parser-id": (
            "Table 12: Comparison with prior work on VOC-2012. Our models were "
            "trained using ConvNets pre-trained on the extended dataset."
        )
    }


def test_preserves_confidently_elevated_numeric_span_as_superscript(tmp_path: Path) -> None:
    source = tmp_path / "superscript-caption.pdf"
    prefix = "Figure 23. Samples from a model at 512"

    def draw(page: fitz.Page) -> None:
        x0 = 90.0
        baseline = 100.0
        size = 9.0
        page.insert_text(
            (x0, baseline), prefix, fontname="Times-Roman", fontsize=size
        )
        superscript_x = x0 + fitz.get_text_length(
            prefix, fontname="Times-Roman", fontsize=size
        )
        page.insert_text(
            (superscript_x, baseline - 2.7),
            "2",
            fontname="Times-Roman",
            fontsize=6,
        )
        suffix_x = superscript_x + fitz.get_text_length(
            "2", fontname="Times-Roman", fontsize=6
        )
        page.insert_text(
            (suffix_x, baseline),
            " images.",
            fontname="Times-Roman",
            fontsize=size,
        )

    _write_pdf(source, draw)
    line = _source_lines(source)[0]
    spans = line["spans"]
    evidence, structure = _inputs(
        [
            {
                "blockId": "fragment-label",
                "text": "Figure 23.",
                "bboxPdf": list(spans[0]["bbox"]),
            },
            {
                "blockId": "fragment-super",
                "text": "2",
                "bboxPdf": list(spans[1]["bbox"]),
            },
            {
                "blockId": "fragment-body",
                "text": "Samples from a model at 512 images.",
                "bboxPdf": _bbox_union(
                    tuple(spans[0]["bbox"]), tuple(spans[2]["bbox"])
                ),
            },
        ],
        {
            "objectId": "figure-model-output",
            "kind": "figure",
            "label": "Figure 23",
            "captionBlockIds": [
                "fragment-label",
                "fragment-super",
                "fragment-body",
            ],
            "bboxNormalized": [0.1, 0.1, 0.9, 0.7],
        },
    )

    assert refine_pdf_caption_texts(source, evidence, structure) == {
        "figure-model-output": "Figure 23. Samples from a model at 512² images."
    }


def test_fails_open_for_missing_scanned_and_ambiguous_text_layers(tmp_path: Path) -> None:
    blank = tmp_path / "blank-scan.pdf"
    _write_pdf(blank, lambda _page: None)
    blocks = [
        {
            "blockId": "caption",
            "text": "Figure 1. Expected caption.",
            "bboxPdf": [90, 90, 300, 108],
        }
    ]
    visual = {
        "objectId": "figure-1",
        "kind": "figure",
        "label": "Figure 1",
        "captionBlockIds": ["caption"],
        "bboxNormalized": [0.1, 0.2, 0.9, 0.6],
    }
    evidence, structure = _inputs(blocks, visual)
    assert refine_pdf_caption_texts(tmp_path / "missing.pdf", evidence, structure) == {}
    assert refine_pdf_caption_texts(blank, evidence, structure) == {}

    ambiguous = tmp_path / "ambiguous.pdf"

    def draw_duplicates(page: fitz.Page) -> None:
        page.insert_text(
            (90, 100), "Figure 1. First caption.", fontname="Times-Roman", fontsize=9
        )
        page.insert_text(
            (90, 105), "Figure 1. Second caption.", fontname="Times-Roman", fontsize=9
        )

    _write_pdf(ambiguous, draw_duplicates)
    duplicate_lines = _source_lines(ambiguous)
    blocks[0]["bboxPdf"] = _bbox_union(
        tuple(duplicate_lines[0]["bbox"]), tuple(duplicate_lines[1]["bbox"])
    )
    evidence, structure = _inputs(blocks, visual)
    assert refine_pdf_caption_texts(ambiguous, evidence, structure) == {}
