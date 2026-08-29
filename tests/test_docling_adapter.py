from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

import papertrans.docling_adapter as adapter
from papertrans.docling_adapter import (
    DoclingAdapterError,
    DoclingUnavailableError,
    docling_document_to_dict,
    docling_document_to_ir,
    extract_docling_semantics,
)
from papertrans.semantic import build_semantic_document, iter_translatable_units
from papertrans.structure import validate_structure_batch


def sample_docling_document() -> dict:
    paragraph = "A claim cites [1] and Figure 1. It continues on page two."
    split = paragraph.index("It continues")
    bottom_left = "BOTTOMLEFT"
    texts = [
        {
            "self_ref": "#/texts/0",
            "label": "title",
            "orig": "Native Docling Paper",
            "text": "Native Docling Paper",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 60, "t": 760, "r": 540, "b": 720, "coord_origin": bottom_left},
                    "charspan": [0, 20],
                }
            ],
        },
        {
            "self_ref": "#/texts/1",
            "label": "paragraph",
            "orig": paragraph,
            "text": paragraph,
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 60, "t": 690, "r": 540, "b": 650, "coord_origin": bottom_left},
                    "charspan": [0, split],
                },
                {
                    "page_no": 2,
                    "bbox": {"l": 60, "t": 760, "r": 540, "b": 720, "coord_origin": bottom_left},
                    "charspan": [split, len(paragraph)],
                },
            ],
        },
        {
            "self_ref": "#/texts/2",
            "label": "section_header",
            "level": 1,
            "orig": "1 Introduction",
            "text": "1 Introduction",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 60, "t": 710, "r": 300, "b": 692, "coord_origin": bottom_left},
                    "charspan": [0, 14],
                }
            ],
        },
        {
            "self_ref": "#/texts/3",
            "label": "caption",
            "orig": "Figure 1: Native visual evidence.",
            "text": "Figure 1: Native visual evidence.",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 90, "t": 230, "r": 510, "b": 205, "coord_origin": bottom_left},
                    "charspan": [0, 33],
                }
            ],
        },
        {
            "self_ref": "#/texts/4",
            "label": "formula",
            "orig": "E = mc²",
            "text": "E = mc²",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 180, "t": 185, "r": 420, "b": 145, "coord_origin": bottom_left},
                    "charspan": [0, 7],
                }
            ],
        },
        {
            "self_ref": "#/texts/5",
            "label": "section_header",
            "level": 2,
            "orig": "1.1 Details",
            "text": "1.1 Details",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 60, "t": 680, "r": 300, "b": 660, "coord_origin": bottom_left},
                    "charspan": [0, 11],
                }
            ],
        },
        {
            "self_ref": "#/texts/6",
            "label": "list_item",
            "orig": "• First exact item",
            "text": "• First exact item",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 70, "t": 640, "r": 350, "b": 615, "coord_origin": bottom_left},
                    "charspan": [0, 18],
                }
            ],
        },
        {
            "self_ref": "#/texts/7",
            "label": "section_header",
            "level": 1,
            "orig": "References",
            "text": "References",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 60, "t": 560, "r": 260, "b": 535, "coord_origin": bottom_left},
                    "charspan": [0, 10],
                }
            ],
        },
        {
            "self_ref": "#/texts/8",
            "label": "reference",
            "orig": "[1] Exact source reference.",
            "text": "[1] Exact source reference.",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 60, "t": 520, "r": 500, "b": 490, "coord_origin": bottom_left},
                    "charspan": [0, 27],
                }
            ],
        },
        {
            "self_ref": "#/texts/9",
            "label": "page_header",
            "content_layer": "furniture",
            "orig": "Running head",
            "text": "Running head",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 60, "t": 795, "r": 220, "b": 780, "coord_origin": bottom_left},
                    "charspan": [0, 12],
                }
            ],
        },
        {
            "self_ref": "#/texts/10",
            "label": "page_footer",
            "content_layer": "furniture",
            "orig": "2",
            "text": "2",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 295, "t": 20, "r": 305, "b": 8, "coord_origin": bottom_left},
                    "charspan": [0, 1],
                }
            ],
        },
    ]
    return {
        "schema_name": "DoclingDocument",
        "version": "1.0.0",
        "name": "native-docling-paper",
        "origin": {"filename": "native-docling-paper.pdf"},
        "pages": {
            "1": {"page_no": 1, "size": {"width": 600, "height": 800}},
            "2": {"page_no": 2, "size": {"width": 600, "height": 800}},
        },
        "body": {
            "self_ref": "#/body",
            "children": [{"$ref": "#/texts/0"}, {"$ref": "#/groups/0"}],
        },
        "furniture": {
            "self_ref": "#/furniture",
            "children": [{"$ref": "#/texts/9"}, {"$ref": "#/texts/10"}],
        },
        "groups": [
            {
                "self_ref": "#/groups/0",
                "label": "section",
                # Deliberately differs from texts[] order: the graph is authoritative.
                "children": [
                    {"$ref": "#/texts/2"},
                    {"$ref": "#/texts/1"},
                    {"$ref": "#/pictures/0"},
                    {"$ref": "#/texts/3"},
                    {"$ref": "#/texts/4"},
                    {"$ref": "#/texts/5"},
                    {"$ref": "#/texts/6"},
                    {"$ref": "#/texts/7"},
                    {"$ref": "#/texts/8"},
                ],
            }
        ],
        "texts": texts,
        "pictures": [
            {
                "self_ref": "#/pictures/0",
                "label": "picture",
                "captions": [{"$ref": "#/texts/3"}],
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {"l": 60, "t": 500, "r": 540, "b": 250, "coord_origin": bottom_left},
                        "charspan": [0, 0],
                    }
                ],
            }
        ],
        "tables": [],
        "key_value_items": [],
    }


def _assignments(structure: dict) -> dict[str, dict]:
    return {
        assignment["blockId"]: assignment
        for page in structure["pages"]
        for assignment in page["blockAssignments"]
    }


def _rendered_visuals(evidence: dict, structure: dict) -> list[dict]:
    page_sizes = {
        page["pageNumber"]: (page["widthPdf"], page["heightPdf"])
        for page in evidence["pages"]
    }
    values = []
    for page in structure["pages"]:
        width, height = page_sizes[page["pageNumber"]]
        for visual in page["visualObjects"]:
            x0, y0, x1, y1 = visual["bboxNormalized"]
            values.append(
                {
                    **visual,
                    "pageNumber": page["pageNumber"],
                    "asset": f"assets/{visual['objectId']}.png",
                    "bboxPdf": [x0 * width, y0 * height, x1 * width, y1 * height],
                }
            )
    return values


def _bottom_left_provenance(
    page_number: int,
    bbox_normalized: tuple[float, float, float, float],
    *,
    width: float = 600,
    height: float = 800,
    text_length: int = 0,
) -> dict:
    x0, y0, x1, y1 = bbox_normalized
    return {
        "page_no": page_number,
        "bbox": {
            "l": x0 * width,
            "t": (1 - y0) * height,
            "r": x1 * width,
            "b": (1 - y1) * height,
            "coord_origin": "BOTTOMLEFT",
        },
        "charspan": [0, text_length],
    }


def _synthetic_recovery_block(
    block_id: str,
    text: str,
    bbox: tuple[float, float, float, float],
    *,
    owner_ref: str | None = None,
    suppressed: bool = False,
) -> dict:
    return {
        "blockId": block_id,
        "sourceBlockId": block_id,
        "text": text,
        "bboxPdf": [bbox[0] * 600, bbox[1] * 800, bbox[2] * 600, bbox[3] * 800],
        "bboxNormalized": list(bbox),
        "embeddedVisualOwnerRefs": [owner_ref] if owner_ref else [],
        "suppressedVisualText": suppressed,
        "visualCaptionCandidate": False,
        "associatedVisualCaption": False,
    }


def _synthetic_recovery_assignment(
    block: dict,
    *,
    role: str = "noise",
    section_id: str | None = None,
    hidden: bool = True,
    reading_order: int,
) -> dict:
    return {
        "blockId": block["blockId"],
        "role": role,
        "readingOrder": reading_order,
        "sectionId": section_id,
        "paragraphId": None if role != "paragraph" else f"para-{block['blockId']}",
        "continuesFrom": None,
        "hidden": hidden,
        "citations": [],
        "objectReferences": [],
        "referenceLabel": None,
        "suppressedVisualText": block["suppressedVisualText"],
        "visualCaptionCandidate": False,
        "associatedVisualCaption": False,
        "confidence": 0.99,
        "warnings": [],
    }


def test_maps_native_docling_graph_to_existing_ir_without_markdown() -> None:
    evidence, structure = docling_document_to_ir(
        sample_docling_document(), source_file="requested.pdf"
    )

    assert evidence["sourceFile"] == "requested.pdf"
    assert evidence["extractionEngine"] == "docling"
    validate_structure_batch(evidence["pages"], structure)
    assert [block["text"] for block in evidence["pages"][0]["blocks"][:3]] == [
        "Native Docling Paper",
        "1 Introduction",
        "A claim cites [1] and Figure 1.",
    ]

    assignments = _assignments(structure)
    first = assignments["dl-texts-1"]
    continued = assignments["dl-texts-1-s2"]
    assert first["paragraphId"] == continued["paragraphId"]
    assert continued["continuesFrom"] == "dl-texts-1"
    assert first["citations"] == ["[1]"]
    assert first["objectReferences"] == ["Figure 1"]
    assert assignments["dl-texts-8"]["referenceLabel"] == "1"
    assert assignments["dl-texts-9"]["role"] == "header"
    assert assignments["dl-texts-9"]["hidden"] is True
    assert assignments["dl-texts-10"]["hidden"] is True

    section_by_id = {section["sectionId"]: section for section in structure["sections"]}
    assert section_by_id["sec-texts-5"]["parentSectionId"] == "sec-texts-2"
    assert section_by_id["sec-texts-2"]["number"] == "1"
    assert section_by_id["sec-texts-5"]["number"] == "1.1"

    visuals = structure["pages"][0]["visualObjects"]
    picture = next(value for value in visuals if value["kind"] == "figure")
    assert picture["bboxNormalized"] == [0.1, 0.375, 0.9, 0.6875]
    assert picture["captionBlockIds"] == ["dl-texts-3"]
    assert picture["label"] == "Figure 1"
    assert picture["insertAfterBlockId"] == "dl-texts-1"
    assert any(value["kind"] == "equation" for value in visuals)


def test_hides_picture_overlay_text_but_keeps_caption_and_body() -> None:
    document = sample_docling_document()
    document["texts"][3]["parent"] = {"$ref": "#/pictures/0"}
    for index, (text, bbox) in enumerate(
        (
            ("80", {"l": 180, "t": 420, "r": 195, "b": 405}),
            ("deception", {"l": 300, "t": 380, "r": 360, "b": 365}),
        ),
        start=11,
    ):
        document["texts"].append(
            {
                "self_ref": f"#/texts/{index}",
                "parent": {"$ref": "#/pictures/0"},
                "label": "text",
                "orig": text,
                "text": text,
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {**bbox, "coord_origin": "BOTTOMLEFT"},
                        "charspan": [0, len(text)],
                    }
                ],
            }
        )
    document["pictures"][0]["children"] = [
        {"$ref": "#/texts/3"},
        {"$ref": "#/texts/11"},
        {"$ref": "#/texts/12"},
    ]
    document["groups"][0]["children"] = [
        value
        for value in document["groups"][0]["children"]
        if value["$ref"] != "#/texts/3"
    ]

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)
    blocks = {
        block["blockId"]: block
        for page in evidence["pages"]
        for block in page["blocks"]
    }

    for block_id in ("dl-texts-11", "dl-texts-12"):
        assert assignments[block_id]["role"] == "noise"
        assert assignments[block_id]["hidden"] is True
        assert assignments[block_id]["paragraphId"] is None
        assert assignments[block_id]["suppressedVisualText"] is True
        assert blocks[block_id]["suppressedVisualText"] is True
        assert blocks[block_id]["embeddedVisualOwnerRefs"] == ["#/pictures/0"]

    assert assignments["dl-texts-3"]["role"] == "caption"
    assert assignments["dl-texts-3"]["hidden"] is False
    figure = next(value for value in structure["pages"][0]["visualObjects"] if value["kind"] == "figure")
    assert figure["captionBlockIds"] == ["dl-texts-3"]
    assert figure["insertAfterBlockId"] == "dl-texts-1"
    assert structure["doclingDiagnostics"] == {
        "embeddedVisualTextItems": 2,
        "suppressedEmbeddedVisualTextBlocks": 2,
        "associatedOrphanCaptions": [],
    }

    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    originals = [unit["original"] for unit in iter_translatable_units(semantic)]
    assert "80" not in originals
    assert "deception" not in originals
    assert any("A claim cites" in original for original in originals)
    rendered_figure = next(value for value in semantic["visualObjects"] if value["kind"] == "figure")
    assert rendered_figure["caption"] == "Figure 1: Native visual evidence."


def test_hides_body_reachable_short_text_inside_visual_bbox_only() -> None:
    document = sample_docling_document()
    for index, (text, bbox) in enumerate(
        (
            ("harmful", {"l": 300, "t": 420, "r": 360, "b": 400}),
            ("Outside figure body text.", {"l": 60, "t": 610, "r": 260, "b": 585}),
        ),
        start=11,
    ):
        document["texts"].append(
            {
                "self_ref": f"#/texts/{index}",
                "parent": {"$ref": "#/body"},
                "label": "text",
                "orig": text,
                "text": text,
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {**bbox, "coord_origin": "BOTTOMLEFT"},
                        "charspan": [0, len(text)],
                    }
                ],
            }
        )
    picture_index = next(
        index
        for index, value in enumerate(document["groups"][0]["children"])
        if value["$ref"] == "#/pictures/0"
    )
    document["groups"][0]["children"][picture_index:picture_index] = [
        {"$ref": "#/texts/12"},
        {"$ref": "#/texts/11"},
    ]

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)
    blocks = {
        block["blockId"]: block
        for page in evidence["pages"]
        for block in page["blocks"]
    }

    assert assignments["dl-texts-11"]["role"] == "noise"
    assert assignments["dl-texts-11"]["hidden"] is True
    assert assignments["dl-texts-11"]["suppressedVisualText"] is True
    assert blocks["dl-texts-11"]["embeddedVisualOwnerRefs"] == ["#/pictures/0"]
    assert assignments["dl-texts-12"]["role"] == "paragraph"
    assert assignments["dl-texts-12"]["hidden"] is False
    assert assignments["dl-texts-12"]["suppressedVisualText"] is False
    assert blocks["dl-texts-12"]["embeddedVisualOwnerRefs"] == []

    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    originals = [unit["original"] for unit in iter_translatable_units(semantic)]
    assert "harmful" not in originals
    assert "Outside figure body text." in originals
    assert structure["doclingDiagnostics"] == {
        "embeddedVisualTextItems": 1,
        "suppressedEmbeddedVisualTextBlocks": 1,
        "associatedOrphanCaptions": [],
    }


def test_table_caption_and_footnote_survive_while_cell_text_is_hidden() -> None:
    document = sample_docling_document()
    table_texts = (
        ("Table 1: Exact results.", "caption", 11),
        ("* Equal contribution.", "footnote", 12),
        ("Accuracy", "text", 13),
    )
    for text, label, index in table_texts:
        top = 500 if index == 11 else 450 - (index - 11) * 25
        bottom = 485 if index == 11 else 430 - (index - 11) * 25
        document["texts"].append(
            {
                "self_ref": f"#/texts/{index}",
                "parent": {
                    "$ref": "#/body" if index == 11 else "#/tables/0"
                },
                "label": label,
                "orig": text,
                "text": text,
                "prov": [
                    {
                        "page_no": 2,
                        "bbox": {
                            "l": 100,
                            "t": top,
                            "r": 500,
                            "b": bottom,
                            "coord_origin": "BOTTOMLEFT",
                        },
                        "charspan": [0, len(text)],
                    }
                ],
            }
        )
    document["tables"] = [
        {
            "self_ref": "#/tables/0",
            "label": "table",
            "children": [
                {"$ref": "#/texts/12"},
                {"$ref": "#/texts/13"},
            ],
            "captions": [],
            "footnotes": [{"$ref": "#/texts/12"}],
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {
                        "l": 80,
                        "t": 480,
                        "r": 520,
                        "b": 360,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [0, 0],
                }
            ],
        }
    ]
    document["groups"][0]["children"][-2:-2] = [
        {"$ref": "#/texts/11"},
        {"$ref": "#/tables/0"},
    ]

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)
    assert assignments["dl-texts-11"]["role"] == "caption"
    assert assignments["dl-texts-11"]["hidden"] is False
    assert assignments["dl-texts-12"]["role"] == "footnote"
    assert assignments["dl-texts-12"]["hidden"] is False
    assert assignments["dl-texts-13"]["role"] == "noise"
    assert assignments["dl-texts-13"]["hidden"] is True

    table = next(
        value
        for page in structure["pages"]
        for value in page["visualObjects"]
        if value["kind"] == "table"
    )
    assert table["label"] == "Table 1"
    assert table["captionBlockIds"] == ["dl-texts-11"]
    assert structure["doclingDiagnostics"]["associatedOrphanCaptions"] == [
        {"captionRef": "#/texts/11", "visualRef": "#/tables/0"}
    ]
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    originals = [unit["original"] for unit in iter_translatable_units(semantic)]
    assert "* Equal contribution." in originals
    assert "Accuracy" not in originals


def test_caption_assignment_repairs_wrong_owner_by_kind_and_geometry() -> None:
    document = sample_docling_document()
    document["pictures"][0]["captions"] = []
    table_caption = "Table 1: Exact results."
    document["texts"].append(
        {
            "self_ref": "#/texts/11",
            "parent": {"$ref": "#/body"},
            "label": "text",
            "orig": table_caption,
            "text": table_caption,
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {
                        "l": 90,
                        "t": 90,
                        "r": 510,
                        "b": 70,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [0, len(table_caption)],
                }
            ],
        }
    )
    document["tables"] = [
        {
            "self_ref": "#/tables/0",
            "label": "table",
            "children": [],
            # Docling incorrectly points the table at the figure caption.
            "captions": [{"$ref": "#/texts/3"}],
            "footnotes": [],
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {
                        "l": 80,
                        "t": 180,
                        "r": 520,
                        "b": 100,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [0, 0],
                }
            ],
        }
    ]
    children = document["groups"][0]["children"]
    caption_index = children.index({"$ref": "#/texts/3"})
    children[caption_index + 1 : caption_index + 1] = [
        {"$ref": "#/tables/0"},
        {"$ref": "#/texts/11"},
    ]

    _evidence, structure = docling_document_to_ir(document)
    visuals = {
        visual["kind"]: visual
        for page in structure["pages"]
        for visual in page["visualObjects"]
        if visual["kind"] in {"figure", "table"}
    }

    assert visuals["figure"]["label"] == "Figure 1"
    assert visuals["figure"]["captionBlockIds"] == ["dl-texts-3"]
    assert visuals["table"]["label"] == "Table 1"
    assert visuals["table"]["captionBlockIds"] == ["dl-texts-11"]
    caption_ids = [
        block_id
        for page in structure["pages"]
        for visual in page["visualObjects"]
        for block_id in visual["captionBlockIds"]
    ]
    assert len(caption_ids) == len(set(caption_ids))


def test_caption_alignment_is_global_monotone_and_geometry_can_override_owner() -> None:
    table_visuals = [
        {
            "ref": f"#/tables/{index}",
            "pageNumber": 6,
            "kind": "table",
            "order": 10 + index * 2,
            "bboxNormalized": bbox,
        }
        for index, bbox in enumerate(
            (
                [0.14, 0.089, 0.41, 0.264],
                [0.095, 0.320, 0.454, 0.492],
                [0.098, 0.536, 0.451, 0.648],
            )
        )
    ]
    table_captions = [
        {
            "ref": f"#/texts/{index}",
            "blockId": f"caption-{index}",
            "text": f"Table {index + 3}. Results.",
            "pageNumber": 6,
            "order": 11 + index * 2,
            "segmentIndex": 1,
            "bboxNormalized": bbox,
            "visualCaptionCandidate": False,
        }
        for index, bbox in enumerate(
            (
                [0.082, 0.271, 0.468, 0.309],
                [0.082, 0.500, 0.468, 0.524],
                [0.082, 0.655, 0.468, 0.679],
            )
        )
    ]
    table_owners = {
        "#/texts/1": {"#/tables/1"},
        "#/texts/2": {"#/tables/2"},
    }

    table_pairs = adapter._align_visual_captions(
        table_captions, table_visuals, table_owners
    )

    assert [
        (caption["ref"], visual["ref"]) for caption, visual in table_pairs
    ] == [
        ("#/texts/0", "#/tables/0"),
        ("#/texts/1", "#/tables/1"),
        ("#/texts/2", "#/tables/2"),
    ]

    figure_visuals = [
        {
            "ref": "#/pictures/5",
            "pageNumber": 8,
            "kind": "figure",
            "order": 30,
            "bboxNormalized": [0.16, 0.09, 0.81, 0.218],
        },
        {
            "ref": "#/pictures/6",
            "pageNumber": 8,
            "kind": "figure",
            "order": 40,
            "bboxNormalized": [0.10, 0.267, 0.45, 0.415],
        },
    ]
    figure_captions = [
        {
            "ref": "#/texts/353",
            "blockId": "figure-6-caption",
            "text": "Figure 6. Training curves.",
            "pageNumber": 8,
            "order": 41,
            "segmentIndex": 1,
            "bboxNormalized": [0.10, 0.225, 0.88, 0.250],
            "visualCaptionCandidate": False,
        },
        {
            "ref": "#/texts/386",
            "blockId": "figure-7-caption",
            "text": "Figure 7. Layer responses.",
            "pageNumber": 8,
            "order": 50,
            "segmentIndex": 1,
            "bboxNormalized": [0.10, 0.420, 0.45, 0.445],
            "visualCaptionCandidate": False,
        },
    ]

    figure_pairs = adapter._align_visual_captions(
        figure_captions,
        figure_visuals,
        {"#/texts/353": {"#/pictures/6"}},
    )

    assert [
        (caption["ref"], visual["ref"]) for caption, visual in figure_pairs
    ] == [
        ("#/texts/353", "#/pictures/5"),
        ("#/texts/386", "#/pictures/6"),
    ]

    distant_caption = {
        **figure_captions[0],
        "ref": "#/texts/far",
        "blockId": "far-caption",
        "bboxNormalized": [0.10, 0.80, 0.45, 0.83],
    }
    assert adapter._align_visual_captions(
        [distant_caption],
        [figure_visuals[0]],
        {"#/texts/far": {"#/pictures/5"}},
    ) == []


def test_recovers_single_internal_caption_crop_and_releases_following_prose(
    tmp_path: Path,
) -> None:
    source = tmp_path / "single-internal-caption.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(90, 80, 510, 300), fill=(0, 0, 0))
    page.insert_text((120, 360), "Figure 1. Recovered caption.", fontsize=10)
    page.insert_text((100, 450), "Recovered body prose.", fontsize=10)
    page.insert_text((100, 720), "1 Preserved footnote.", fontsize=8)
    page.insert_text((260, 760), "Footer", fontsize=9)
    pdf.save(source)
    pdf.close()

    anchor = _synthetic_recovery_block("anchor", "Earlier body.", (0.1, 0.04, 0.4, 0.06))
    caption = _synthetic_recovery_block(
        "caption",
        "Figure 1. Recovered caption.",
        (0.20, 0.435, 0.415, 0.455),
        owner_ref="#/pictures/0",
        suppressed=True,
    )
    prose = _synthetic_recovery_block(
        "prose",
        "Recovered body prose.",
        (0.166, 0.548, 0.55, 0.568),
        owner_ref="#/pictures/0",
        suppressed=True,
    )
    footnote = _synthetic_recovery_block(
        "footnote",
        "1 Preserved footnote.",
        (0.166, 0.885, 0.50, 0.902),
    )
    footer = _synthetic_recovery_block(
        "footer",
        "Footer",
        (0.43, 0.935, 0.55, 0.955),
        owner_ref="#/pictures/0",
        suppressed=True,
    )
    blocks = [anchor, footnote, caption, prose, footer]
    evidence = {
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "widthPdf": 600,
                "heightPdf": 800,
                "blocks": blocks,
            }
        ],
    }
    structure = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    _synthetic_recovery_assignment(
                        anchor,
                        role="paragraph",
                        section_id="sec-base",
                        hidden=False,
                        reading_order=1,
                    ),
                    _synthetic_recovery_assignment(
                        footnote,
                        role="footnote",
                        section_id="sec-base",
                        hidden=False,
                        reading_order=2,
                    ),
                    *[
                        _synthetic_recovery_assignment(block, reading_order=index)
                        for index, block in enumerate(blocks[2:], start=3)
                    ],
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-pictures-0",
                        "kind": "figure",
                        "label": None,
                        "bboxNormalized": [0.1, 0.08, 0.9, 0.97],
                        "captionBlockIds": [],
                        "insertAfterBlockId": "anchor",
                        "confidence": 0.99,
                        "warnings": [],
                    }
                ],
            }
        ],
        "sections": [
            {
                "sectionId": "sec-base",
                "number": "1",
                "titleBlockId": "anchor",
                "level": 1,
                "parentSectionId": None,
                "pageStart": 1,
            }
        ],
        "doclingDiagnostics": {},
        "warnings": [],
    }

    adapter._recover_overlarge_docling_pictures(source, evidence, structure)

    visual = structure["pages"][0]["visualObjects"][0]
    assignments = _assignments(structure)
    assert visual["label"] == "Figure 1"
    assert visual["captionBlockIds"] == ["caption"]
    assert visual["bboxNormalized"][3] < caption["bboxNormalized"][1]
    assert assignments["caption"]["role"] == "caption"
    assert assignments["caption"]["hidden"] is False
    assert assignments["prose"]["role"] == "paragraph"
    assert assignments["prose"]["hidden"] is False
    assert assignments["footnote"]["readingOrder"] > assignments["prose"]["readingOrder"]
    assert assignments["footer"]["hidden"] is True
    assert structure["doclingDiagnostics"]["recoveredOverlargeVisuals"][0][
        "movedFootnoteBlockIds"
    ] == ["footnote"]
    assert structure["doclingDiagnostics"]["suppressedInternalCaptionBlockIds"] == []
    assert adapter.refine_pdf_caption_texts(source, evidence, structure) == {
        "figure-pictures-0": "Figure 1. Recovered caption."
    }


def test_splits_two_internal_captions_and_recovers_heading_equation_and_page_link(
    tmp_path: Path,
) -> None:
    source = tmp_path / "two-internal-captions.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(90, 70, 510, 180), fill=(0, 0, 0))
    page.insert_text((120, 220), "Figure 6. First panel.", fontsize=10)
    page.draw_rect(fitz.Rect(110, 270, 490, 360), fill=(0, 0, 0))
    page.insert_text((120, 400), "Figure 7. Second panel.", fontsize=10)
    page.insert_text((100, 435), "Body before the recovered heading.", fontsize=10)
    page.insert_text((100, 460), "Recovered run-in heading", fontname="hebo", fontsize=10)
    page.insert_text((330, 460), "Introductory prose.", fontsize=10)
    page.insert_text((150, 530), "L", fontsize=10)
    page.insert_text((190, 530), "=", fontsize=10)
    page.insert_text((450, 530), "(16)", fontsize=10)
    page.insert_text((100, 710), "paragraph continues to", fontsize=10)
    page.insert_text((285, 775), "1", fontsize=9)
    second_page = pdf.new_page(width=600, height=800)
    second_page.insert_text((100, 60), "finish the sentence.", fontsize=10)
    pdf.save(source)
    pdf.close()

    owner = "#/pictures/5"
    page_one_specs = [
        ("anchor", "Earlier body.", (0.1, 0.03, 0.4, 0.05), None, False),
        ("cap6", "Figure 6. First panel.", (0.20, 0.26, 0.60, 0.28), owner, True),
        ("cap7", "Figure 7. Second panel.", (0.20, 0.485, 0.62, 0.505), owner, True),
        (
            "pre-heading",
            "Body before the recovered heading.",
            (0.166, 0.525, 0.62, 0.548),
            owner,
            True,
        ),
        ("run-in", "Recovered run-in heading", (0.166, 0.558, 0.50, 0.58), owner, True),
        ("intro", "Introductory prose.", (0.55, 0.558, 0.82, 0.58), owner, True),
        ("eq-left", "L", (0.25, 0.648, 0.28, 0.67), owner, True),
        ("eq-op", "=", (0.315, 0.648, 0.35, 0.67), owner, True),
        ("eq-label", "(16)", (0.75, 0.648, 0.82, 0.67), owner, True),
        ("tail", "paragraph continues to", (0.166, 0.872, 0.55, 0.895), owner, True),
        ("footer", "1", (0.475, 0.955, 0.51, 0.975), owner, True),
    ]
    page_one_blocks = [
        _synthetic_recovery_block(
            block_id,
            text,
            bbox,
            owner_ref=owner_ref,
            suppressed=suppressed,
        )
        for block_id, text, bbox, owner_ref, suppressed in page_one_specs
    ]
    continuation = _synthetic_recovery_block(
        "continuation", "finish the sentence.", (0.166, 0.06, 0.5, 0.08)
    )
    evidence = {
        "pageCount": 2,
        "pages": [
            {"pageNumber": 1, "blocks": page_one_blocks},
            {"pageNumber": 2, "blocks": [continuation]},
        ],
    }
    structure = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    _synthetic_recovery_assignment(
                        page_one_blocks[0],
                        role="paragraph",
                        section_id="sec-base",
                        hidden=False,
                        reading_order=1,
                    ),
                    *[
                        _synthetic_recovery_assignment(block, reading_order=index)
                        for index, block in enumerate(page_one_blocks[1:], start=2)
                    ],
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-pictures-5",
                        "kind": "figure",
                        "label": None,
                        "bboxNormalized": [0.1, 0.06, 0.9, 0.98],
                        "captionBlockIds": [],
                        "insertAfterBlockId": "anchor",
                        "confidence": 0.99,
                        "warnings": [],
                    }
                ],
            },
            {
                "pageNumber": 2,
                "blockAssignments": [
                    _synthetic_recovery_assignment(
                        continuation,
                        role="paragraph",
                        section_id="sec-base",
                        hidden=False,
                        reading_order=1,
                    )
                ],
                "visualObjects": [],
            },
        ],
        "sections": [
            {
                "sectionId": "sec-base",
                "number": "1",
                "titleBlockId": "anchor",
                "level": 2,
                "parentSectionId": None,
                "pageStart": 1,
            }
        ],
        "doclingDiagnostics": {},
        "warnings": [],
    }

    adapter._recover_overlarge_docling_pictures(source, evidence, structure)

    visuals = structure["pages"][0]["visualObjects"]
    assignments = _assignments(structure)
    assert [(value["kind"], value["label"]) for value in visuals] == [
        ("figure", "Figure 6"),
        ("figure", "Figure 7"),
        ("equation", "Equation 16"),
    ]
    assert assignments["run-in"]["role"] == "heading"
    assert assignments["pre-heading"]["role"] == "paragraph"
    assert assignments["pre-heading"]["sectionId"] == "sec-base"
    assert assignments["intro"]["role"] == "paragraph"
    assert assignments["intro"]["sectionId"] == "sec-run-in"
    assert assignments["eq-left"]["role"] == "equation"
    assert assignments["eq-label"]["role"] == "equation"
    assert assignments["tail"]["paragraphId"] == assignments["continuation"]["paragraphId"]
    assert assignments["continuation"]["continuesFrom"] == "tail"
    assert assignments["continuation"]["sectionId"] == "sec-run-in"
    assert assignments["footer"]["hidden"] is True
    assert any(section["sectionId"] == "sec-run-in" for section in structure["sections"])


def test_overlarge_recovery_fails_closed_without_source_text_region(
    tmp_path: Path,
) -> None:
    source = tmp_path / "image-only.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(80, 80, 520, 500), fill=(0, 0, 0))
    pdf.save(source)
    pdf.close()
    caption = _synthetic_recovery_block(
        "caption",
        "Figure 9. Missing source text.",
        (0.2, 0.55, 0.7, 0.58),
        owner_ref="#/pictures/9",
        suppressed=True,
    )
    evidence = {"pageCount": 1, "pages": [{"pageNumber": 1, "blocks": [caption]}]}
    structure = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    _synthetic_recovery_assignment(caption, reading_order=1)
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-pictures-9",
                        "kind": "figure",
                        "label": None,
                        "bboxNormalized": [0.1, 0.08, 0.9, 0.95],
                        "captionBlockIds": [],
                        "insertAfterBlockId": None,
                        "confidence": 0.99,
                        "warnings": [],
                    }
                ],
            }
        ],
        "sections": [],
        "doclingDiagnostics": {},
        "warnings": [],
    }

    adapter._recover_overlarge_docling_pictures(source, evidence, structure)

    assert structure["pages"][0]["visualObjects"][0]["captionBlockIds"] == []
    assert _assignments(structure)["caption"]["hidden"] is True
    assert structure["doclingDiagnostics"]["suppressedInternalCaptionBlockIds"] == [
        "caption"
    ]
    assert structure["doclingDiagnostics"]["overlargeVisualObjectIds"] == [
        "figure-pictures-9"
    ]


def test_overlarge_recovery_separates_body_sharing_pdf_caption_region(
    tmp_path: Path,
) -> None:
    source = tmp_path / "same-region.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(80, 70, 520, 300), fill=(0, 0, 0))
    page.insert_textbox(
        fitz.Rect(100, 330, 500, 390),
        "Figure 1. Caption.\nBody prose shares the PDF block.",
        fontsize=10,
    )
    pdf.save(source)
    pdf.close()
    owner = "#/pictures/1"
    caption = _synthetic_recovery_block(
        "caption",
        "Figure 1. Caption.",
        (0.166, 0.412, 0.31, 0.431),
        owner_ref=owner,
        suppressed=True,
    )
    body = _synthetic_recovery_block(
        "body",
        "Body prose shares the PDF block.",
        (0.166, 0.431, 0.43, 0.451),
        owner_ref=owner,
        suppressed=True,
    )
    blocks = [caption, body]
    evidence = {"pageCount": 1, "pages": [{"pageNumber": 1, "blocks": blocks}]}
    structure = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    _synthetic_recovery_assignment(block, reading_order=index)
                    for index, block in enumerate(blocks, start=1)
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-pictures-1",
                        "kind": "figure",
                        "label": None,
                        "bboxNormalized": [0.1, 0.06, 0.9, 0.90],
                        "captionBlockIds": [],
                        "insertAfterBlockId": None,
                        "confidence": 0.99,
                        "warnings": [],
                    }
                ],
            }
        ],
        "sections": [],
        "doclingDiagnostics": {},
        "warnings": [],
    }

    adapter._recover_overlarge_docling_pictures(source, evidence, structure)

    assignments = _assignments(structure)
    visual = structure["pages"][0]["visualObjects"][0]
    assert visual["captionBlockIds"] == ["caption"]
    assert assignments["caption"]["role"] == "caption"
    assert assignments["body"]["role"] == "paragraph"
    assert assignments["body"]["hidden"] is False
    assert "body" not in visual["captionBlockIds"]


def test_overlarge_recovery_fails_atomically_for_inter_caption_body(
    tmp_path: Path,
) -> None:
    source = tmp_path / "inter-caption-body.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(80, 60, 520, 160), fill=(0, 0, 0))
    page.insert_text((100, 190), "Figure 6. First.", fontsize=10)
    page.insert_text(
        (100, 235), "This prose must remain semantic body text.", fontsize=10
    )
    page.draw_rect(fitz.Rect(80, 270, 520, 360), fill=(0, 0, 0))
    page.insert_text((100, 405), "Figure 7. Second.", fontsize=10)
    pdf.save(source)
    pdf.close()
    owner = "#/pictures/5"
    specs = [
        ("cap6", "Figure 6. First.", (0.166, 0.224, 0.35, 0.243)),
        (
            "interstitial",
            "This prose must remain semantic body text.",
            (0.166, 0.280, 0.52, 0.300),
        ),
        ("cap7", "Figure 7. Second.", (0.166, 0.492, 0.37, 0.512)),
    ]
    blocks = [
        _synthetic_recovery_block(
            block_id,
            text,
            bbox,
            owner_ref=owner,
            suppressed=True,
        )
        for block_id, text, bbox in specs
    ]
    evidence = {"pageCount": 1, "pages": [{"pageNumber": 1, "blocks": blocks}]}
    structure = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    _synthetic_recovery_assignment(block, reading_order=index)
                    for index, block in enumerate(blocks, start=1)
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-pictures-5",
                        "kind": "figure",
                        "label": None,
                        "bboxNormalized": [0.1, 0.05, 0.9, 0.90],
                        "captionBlockIds": [],
                        "insertAfterBlockId": None,
                        "confidence": 0.99,
                        "warnings": [],
                    }
                ],
            }
        ],
        "sections": [],
        "doclingDiagnostics": {},
        "warnings": [],
    }

    adapter._recover_overlarge_docling_pictures(source, evidence, structure)

    assert structure["pages"][0]["visualObjects"][0]["captionBlockIds"] == []
    assert all(value["hidden"] for value in _assignments(structure).values())
    assert structure["doclingDiagnostics"]["overlargeVisualObjectIds"] == [
        "figure-pictures-5"
    ]


def test_multi_panel_merge_fails_closed_when_unlabeled_neighbor_is_ambiguous(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ambiguous-panels.pdf"
    pdf = fitz.open()
    pdf.new_page(width=600, height=800)
    pdf.save(source)
    pdf.close()
    caption = _synthetic_recovery_block(
        "caption", "Figure 2. (left) A. (right) B.", (0.2, 0.34, 0.8, 0.37)
    )
    evidence = {"pageCount": 1, "pages": [{"pageNumber": 1, "blocks": [caption]}]}
    common = {
        "kind": "figure",
        "confidence": 0.99,
        "warnings": [],
        "insertAfterBlockId": "caption",
    }
    structure = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    _synthetic_recovery_assignment(
                        caption,
                        role="caption",
                        hidden=False,
                        reading_order=1,
                    )
                ],
                "visualObjects": [
                    {
                        **common,
                        "objectId": "figure-captioned",
                        "label": "Figure 2",
                        "bboxNormalized": [0.55, 0.10, 0.75, 0.30],
                        "captionBlockIds": ["caption"],
                    },
                    {
                        **common,
                        "objectId": "figure-left-a",
                        "label": None,
                        "bboxNormalized": [0.30, 0.11, 0.45, 0.29],
                        "captionBlockIds": [],
                    },
                    {
                        **common,
                        "objectId": "figure-left-b",
                        "label": None,
                        "bboxNormalized": [0.32, 0.12, 0.47, 0.28],
                        "captionBlockIds": [],
                    },
                ],
            }
        ],
        "sections": [],
        "doclingDiagnostics": {},
        "warnings": [],
    }

    adapter._merge_split_panel_figures(source, evidence, structure)

    assert len(structure["pages"][0]["visualObjects"]) == 3
    assert structure["doclingDiagnostics"]["unmergedMultiPanelVisualObjectIds"] == [
        "figure-captioned",
        "figure-left-a",
        "figure-left-b",
    ]


def test_multi_panel_zero_neighbor_distinguishes_combined_from_missing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "zero-neighbor-panels.pdf"
    pdf = fitz.open()
    pdf.new_page(width=600, height=800)
    pdf.save(source)
    pdf.close()
    caption = _synthetic_recovery_block(
        "caption", "Figure 2. (left) A. (right) B.", (0.2, 0.34, 0.8, 0.37)
    )
    evidence = {"pageCount": 1, "pages": [{"pageNumber": 1, "blocks": [caption]}]}

    def structure_for_bbox(bbox: list[float]) -> dict:
        return {
            "pages": [
                {
                    "pageNumber": 1,
                    "blockAssignments": [
                        _synthetic_recovery_assignment(
                            caption,
                            role="caption",
                            hidden=False,
                            reading_order=1,
                        )
                    ],
                    "visualObjects": [
                        {
                            "objectId": "figure-captioned",
                            "kind": "figure",
                            "label": "Figure 2",
                            "bboxNormalized": bbox,
                            "captionBlockIds": ["caption"],
                            "insertAfterBlockId": "caption",
                            "confidence": 0.99,
                            "warnings": [],
                        }
                    ],
                }
            ],
            "sections": [],
            "doclingDiagnostics": {},
            "warnings": [],
        }

    missing = structure_for_bbox([0.55, 0.10, 0.75, 0.30])
    adapter._merge_split_panel_figures(source, evidence, missing)
    assert missing["doclingDiagnostics"]["unmergedMultiPanelVisualObjectIds"] == [
        "figure-captioned"
    ]

    combined = structure_for_bbox([0.20, 0.10, 0.80, 0.30])
    adapter._merge_split_panel_figures(source, evidence, combined)
    assert combined["doclingDiagnostics"]["unmergedMultiPanelVisualObjectIds"] == []


def test_merges_explicit_left_right_panels_and_suppresses_duplicate_headers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "split-panels.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((145, 80), "Left Panel", fontname="hebo", fontsize=9)
    page.draw_rect(fitz.Rect(170, 95, 240, 220), fill=(0, 0, 0))
    page.insert_text((355, 80), "Right Panel", fontname="hebo", fontsize=9)
    page.draw_rect(fitz.Rect(340, 90, 460, 235), fill=(0, 0, 0))
    page.insert_text(
        (108, 290),
        "Figure 2. (left) Left Panel. (right) Right Panel.",
        fontsize=9,
    )
    pdf.save(source)
    pdf.close()

    anchor = _synthetic_recovery_block(
        "anchor", "Previous body continues", (0.1, 0.03, 0.4, 0.05)
    )
    left_header = _synthetic_recovery_block(
        "left-header", "Left Panel", (0.241, 0.087, 0.39, 0.105)
    )
    caption = _synthetic_recovery_block(
        "caption",
        "Figure 2. (left) Left Panel. (right) Right Panel.",
        (0.176, 0.348, 0.75, 0.37),
        owner_ref="#/pictures/2",
    )
    right_header = _synthetic_recovery_block(
        "right-header",
        "Right Panel",
        (0.59, 0.087, 0.74, 0.105),
        owner_ref="#/pictures/2",
        suppressed=True,
    )
    body = _synthetic_recovery_block(
        "body", "with the following body.", (0.18, 0.4, 0.5, 0.42)
    )
    child_heading = _synthetic_recovery_block(
        "child-heading", "Real subsection", (0.18, 0.45, 0.4, 0.47)
    )
    blocks = [anchor, left_header, caption, right_header, body, child_heading]
    structure = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    _synthetic_recovery_assignment(
                        anchor,
                        role="paragraph",
                        section_id="sec-base",
                        hidden=False,
                        reading_order=1,
                    ),
                    _synthetic_recovery_assignment(
                        left_header,
                        role="heading",
                        section_id="sec-panel-label",
                        hidden=False,
                        reading_order=2,
                    ),
                    _synthetic_recovery_assignment(
                        caption,
                        role="caption",
                        section_id="sec-panel-label",
                        hidden=False,
                        reading_order=3,
                    ),
                    _synthetic_recovery_assignment(
                        right_header, reading_order=4
                    ),
                    _synthetic_recovery_assignment(
                        body,
                        role="paragraph",
                        section_id="sec-panel-label",
                        hidden=False,
                        reading_order=5,
                    ),
                    _synthetic_recovery_assignment(
                        child_heading,
                        role="heading",
                        section_id="sec-child",
                        hidden=False,
                        reading_order=6,
                    ),
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-pictures-1",
                        "kind": "figure",
                        "label": None,
                        "bboxNormalized": [0.28, 0.118, 0.40, 0.277],
                        "captionBlockIds": [],
                        "insertAfterBlockId": "left-header",
                        "confidence": 0.99,
                        "warnings": [],
                    },
                    {
                        "objectId": "figure-pictures-2",
                        "kind": "figure",
                        "label": "Figure 2",
                        "bboxNormalized": [0.56, 0.09, 0.77, 0.30],
                        "captionBlockIds": ["caption"],
                        "insertAfterBlockId": "left-header",
                        "confidence": 0.99,
                        "warnings": [],
                    },
                ],
            }
        ],
        "sections": [
            {
                "sectionId": "sec-base",
                "number": "1",
                "titleBlockId": "anchor",
                "level": 1,
                "parentSectionId": None,
                "pageStart": 1,
            },
            {
                "sectionId": "sec-panel-label",
                "number": None,
                "titleBlockId": "left-header",
                "level": 2,
                "parentSectionId": "sec-base",
                "pageStart": 1,
            },
            {
                "sectionId": "sec-child",
                "number": None,
                "titleBlockId": "child-heading",
                "level": 3,
                "parentSectionId": "sec-panel-label",
                "pageStart": 1,
            },
        ],
        "doclingDiagnostics": {},
        "warnings": [],
    }
    evidence = {
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "widthPdf": 600,
                "heightPdf": 800,
                "blocks": blocks,
            }
        ],
    }

    adapter._merge_split_panel_figures(source, evidence, structure)

    assignments = _assignments(structure)
    visuals = structure["pages"][0]["visualObjects"]
    assert len(visuals) == 1
    assert visuals[0]["objectId"] == "figure-pictures-2"
    assert visuals[0]["bboxNormalized"][0] <= left_header["bboxNormalized"][0]
    assert visuals[0]["bboxNormalized"][3] < caption["bboxNormalized"][1]
    assert visuals[0]["insertAfterBlockId"] == "anchor"
    assert assignments["left-header"]["hidden"] is True
    assert assignments["right-header"]["hidden"] is True
    assert assignments["body"]["sectionId"] == "sec-base"
    assert assignments["body"]["paragraphId"] == assignments["anchor"]["paragraphId"]
    assert assignments["body"]["continuesFrom"] == "anchor"
    assert "sec-panel-label" not in {
        section["sectionId"] for section in structure["sections"]
    }
    child_section = next(
        section for section in structure["sections"] if section["sectionId"] == "sec-child"
    )
    assert child_section["parentSectionId"] == "sec-base"
    assert structure["doclingDiagnostics"]["danglingParentSectionIds"] == []
    assert structure["doclingDiagnostics"]["unmergedMultiPanelVisualObjectIds"] == []


def test_reassembles_same_line_caption_fragments() -> None:
    document = sample_docling_document()
    document["pictures"][0]["captions"] = []
    document["texts"][3]["orig"] = "Figure 1."
    document["texts"][3]["text"] = "Figure 1."
    document["texts"][3]["prov"][0]["charspan"] = [0, 9]
    document["texts"][3]["prov"][0]["bbox"].update({"l": 90, "r": 160})
    continuation = "Split caption text."
    document["texts"].append(
        {
            "self_ref": "#/texts/11",
            "parent": {"$ref": "#/body"},
            "label": "text",
            "orig": continuation,
            "text": continuation,
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {
                        "l": 165,
                        "t": 230,
                        "r": 330,
                        "b": 205,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [0, len(continuation)],
                }
            ],
        }
    )
    children = document["groups"][0]["children"]
    caption_index = children.index({"$ref": "#/texts/3"})
    children.insert(caption_index + 1, {"$ref": "#/texts/11"})

    evidence, structure = docling_document_to_ir(document)
    figure = next(
        visual
        for page in structure["pages"]
        for visual in page["visualObjects"]
        if visual["kind"] == "figure"
    )
    assert figure["captionBlockIds"] == ["dl-texts-3", "dl-texts-11"]
    assignments = _assignments(structure)
    assert assignments["dl-texts-11"]["role"] == "caption"
    assert assignments["dl-texts-11"]["associatedVisualCaption"] is True
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    rendered_figure = next(
        visual for visual in semantic["visualObjects"] if visual["kind"] == "figure"
    )
    assert rendered_figure["caption"] == "Figure 1. Split caption text."


def test_attaches_only_nearby_explicit_caption_continuations() -> None:
    document = sample_docling_document()
    document["pictures"][0]["captions"] = [
        {"$ref": "#/texts/11"},
        {"$ref": "#/texts/12"},
        {"$ref": "#/texts/13"},
    ]
    continuation = "Second exact caption line."
    distant_internal_title = "Unrelated title at the opposite visual boundary"
    panel_label = "(b) Inception feature space nearest neighbors"
    for index, text, bbox in (
        (11, continuation, (0.15, 0.735, 0.55, 0.755)),
        (12, distant_internal_title, (0.25, 0.39, 0.75, 0.41)),
        (13, panel_label, (0.25, 0.755, 0.65, 0.775)),
    ):
        document["texts"].append(
            {
                "self_ref": f"#/texts/{index}",
                "parent": {"$ref": "#/pictures/0"},
                "label": "caption",
                "orig": text,
                "text": text,
                "prov": [
                    _bottom_left_provenance(
                        1, bbox, text_length=len(text)
                    )
                ],
            }
        )

    evidence, structure = docling_document_to_ir(document)
    figure = next(
        visual
        for page in structure["pages"]
        for visual in page["visualObjects"]
        if visual["kind"] == "figure"
    )

    assert figure["captionBlockIds"] == ["dl-texts-3", "dl-texts-11"]
    assignments = _assignments(structure)
    assert assignments["dl-texts-11"]["associatedVisualCaption"] is True
    assert assignments["dl-texts-12"]["associatedVisualCaption"] is False
    assert assignments["dl-texts-13"]["associatedVisualCaption"] is False
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    rendered_figure = next(
        visual for visual in semantic["visualObjects"] if visual["kind"] == "figure"
    )
    assert rendered_figure["caption"].endswith(continuation)
    assert distant_internal_title not in rendered_figure["caption"]
    assert panel_label not in rendered_figure["caption"]


def test_orders_caption_by_geometry_recovers_superscript_and_rejects_tall_body() -> None:
    document = sample_docling_document()
    picture = document["pictures"][0]
    picture["captions"] = []
    picture["prov"] = [
        _bottom_left_provenance(1, (0.10, 0.20, 0.80, 0.70))
    ]

    label = document["texts"][3]
    label["orig"] = label["text"] = "Figure 23."
    label["prov"] = [
        _bottom_left_provenance(
            1, (0.15, 0.695, 0.21, 0.705), text_length=len("Figure 23.")
        )
    ]

    fragments = (
        (11, "2", (0.70, 0.691, 0.71, 0.698), "#/pictures/0"),
        (
            12,
            "Convolutional samples finetuned on",
            (0.22, 0.695, 0.65, 0.705),
            "#/body",
        ),
        (13, "512", (0.65, 0.695, 0.70, 0.705), "#/body"),
        (14, "images.", (0.715, 0.695, 0.78, 0.705), "#/body"),
        (
            15,
            "This is neighboring body prose. " * 30,
            (0.01, 0.67, 0.14, 0.73),
            "#/body",
        ),
    )
    for index, text, bbox, parent in fragments:
        document["texts"].append(
            {
                "self_ref": f"#/texts/{index}",
                "parent": {"$ref": parent},
                "label": "text",
                "orig": text,
                "text": text,
                "prov": [
                    _bottom_left_provenance(
                        1, bbox, text_length=len(text)
                    )
                ],
            }
        )
    picture["children"] = [{"$ref": "#/texts/11"}]
    children = document["groups"][0]["children"]
    caption_index = children.index({"$ref": "#/texts/3"})
    children[caption_index + 1 : caption_index + 1] = [
        {"$ref": "#/texts/12"},
        {"$ref": "#/texts/13"},
        {"$ref": "#/texts/14"},
        {"$ref": "#/texts/15"},
    ]

    evidence, structure = docling_document_to_ir(document)
    figure = next(
        visual
        for page in structure["pages"]
        for visual in page["visualObjects"]
        if visual["kind"] == "figure"
    )

    assert figure["captionBlockIds"] == [
        "dl-texts-3",
        "dl-texts-12",
        "dl-texts-13",
        "dl-texts-11",
        "dl-texts-14",
    ]
    assert figure["bboxNormalized"][3] == pytest.approx(0.691)
    assignments = _assignments(structure)
    assert assignments["dl-texts-15"]["associatedVisualCaption"] is False
    assert assignments["dl-texts-15"]["role"] == "paragraph"
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    rendered_figure = next(
        visual for visual in semantic["visualObjects"] if visual["kind"] == "figure"
    )
    assert rendered_figure["caption"] == (
        "Figure 23. Convolutional samples finetuned on 512² images."
    )


def test_suppresses_only_visual_segments_and_bbox_boundary_overlap() -> None:
    document = sample_docling_document()
    body_text = "Visible body sentence."
    visual_text = "harmful"
    combined = f"{body_text}\n{visual_text}"
    document["texts"].append(
        {
            "self_ref": "#/texts/11",
            "parent": {"$ref": "#/body"},
            "label": "text",
            "orig": combined,
            "text": combined,
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {
                        "l": 60,
                        "t": 640,
                        "r": 260,
                        "b": 615,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [0, len(body_text)],
                },
                {
                    "page_no": 2,
                    "bbox": {
                        "l": 250,
                        "t": 420,
                        "r": 310,
                        "b": 400,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [len(body_text) + 1, len(combined)],
                },
            ],
        }
    )
    edge_text = "edge label"
    document["texts"].append(
        {
            "self_ref": "#/texts/12",
            "parent": {"$ref": "#/body"},
            "label": "text",
            "orig": edge_text,
            "text": edge_text,
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {
                        "l": 250,
                        "t": 253,
                        "r": 330,
                        "b": 246,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [0, len(edge_text)],
                }
            ],
        }
    )
    document["pictures"].append(
        {
            "self_ref": "#/pictures/1",
            "label": "picture",
            "children": [],
            "captions": [],
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {
                        "l": 60,
                        "t": 500,
                        "r": 540,
                        "b": 250,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [0, 0],
                }
            ],
        }
    )
    children = document["groups"][0]["children"]
    children[2:2] = [
        {"$ref": "#/texts/11"},
        {"$ref": "#/pictures/1"},
        {"$ref": "#/texts/12"},
    ]

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)
    assert assignments["dl-texts-11"]["role"] == "paragraph"
    assert assignments["dl-texts-11"]["hidden"] is False
    assert assignments["dl-texts-11-s2"]["role"] == "noise"
    assert assignments["dl-texts-11-s2"]["hidden"] is True
    assert assignments["dl-texts-12"]["role"] == "noise"
    assert assignments["dl-texts-12"]["hidden"] is True
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    originals = [unit["original"] for unit in iter_translatable_units(semantic)]
    assert body_text in originals
    assert visual_text not in originals
    assert edge_text not in originals


def test_multi_page_character_spans_preserve_exact_text_and_reject_gaps() -> None:
    document = sample_docling_document()
    paragraph = document["texts"][1]
    paragraph["orig"] = paragraph["text"] = "  Alpha Beta  "
    paragraph["prov"][0]["charspan"] = [2, 7]
    paragraph["prov"][1]["charspan"] = [8, 12]

    evidence, structure = docling_document_to_ir(document)
    blocks = {
        block["blockId"]: block
        for page in evidence["pages"]
        for block in page["blocks"]
    }
    assert blocks["dl-texts-1"]["text"] == "Alpha"
    assert blocks["dl-texts-1-s2"]["text"] == "Beta"
    assert _assignments(structure)["dl-texts-1-s2"]["continuesFrom"] == "dl-texts-1"

    paragraph["prov"][1]["charspan"] = [9, 12]
    evidence, structure = docling_document_to_ir(document)
    blocks = {
        block["blockId"]: block
        for page in evidence["pages"]
        for block in page["blocks"]
    }
    assert blocks["dl-texts-1"]["text"] == "Alpha Beta"
    assert "dl-texts-1-s2" not in blocks
    assert any(
        "complete, ordered character spans" in warning
        for warning in _assignments(structure)["dl-texts-1"]["warnings"]
    )


def test_promotes_top_unnumbered_first_heading_to_missing_title() -> None:
    document = sample_docling_document()
    title = document["texts"][0]
    title["label"] = "section_header"
    title["orig"] = title["text"] = "A Simple Paper"
    title["prov"][0]["charspan"] = [0, len("A Simple Paper")]

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)

    assert assignments["dl-texts-0"]["role"] == "title"
    assert [section["number"] for section in structure["sections"][:2]] == ["1", "1.1"]
    assert "sec-texts-0" not in {section["sectionId"] for section in structure["sections"]}
    assert adapter._heading_details("A Simple Paper", {}) == (None, 1)
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    assert semantic["title"]["original"] == "A Simple Paper"


def test_promotes_centered_title_before_distant_abstract_and_preserves_front_matter() -> None:
    document = sample_docling_document()
    title = document["texts"][0]
    title["label"] = "section_header"
    title["level"] = 3
    title["orig"] = title["text"] = "A Long Paper Title"
    title["prov"][0]["charspan"] = [0, len("A Long Paper Title")]

    front_specs = [
        ("author", "Ada Example"),
        ("affiliation", "Example University"),
        *[("text", f"Unclassified front matter {index}") for index in range(7)],
    ]
    front_refs = []
    for offset, (label, text) in enumerate(front_specs):
        index = len(document["texts"])
        ref = f"#/texts/{index}"
        top = 700 - offset * 14
        document["texts"].append(
            {
                "self_ref": ref,
                "label": label,
                "orig": text,
                "text": text,
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {
                            "l": 150,
                            "t": top,
                            "r": 450,
                            "b": top - 10,
                            "coord_origin": "BOTTOMLEFT",
                        },
                        "charspan": [0, len(text)],
                    }
                ],
            }
        )
        front_refs.append({"$ref": ref})

    abstract_index = len(document["texts"])
    abstract_ref = f"#/texts/{abstract_index}"
    document["texts"].append(
        {
            "self_ref": abstract_ref,
            "label": "section_header",
            "level": 1,
            "orig": "Abstract",
            "text": "Abstract",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {
                        "l": 270,
                        "t": 550,
                        "r": 330,
                        "b": 535,
                        "coord_origin": "BOTTOMLEFT",
                    },
                    "charspan": [0, len("Abstract")],
                }
            ],
        }
    )
    document["body"]["children"] = [
        {"$ref": "#/texts/0"},
        *front_refs,
        {"$ref": abstract_ref},
        {"$ref": "#/groups/0"},
    ]
    document["texts"][2]["prov"][0]["page_no"] = 2

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)

    assert assignments["dl-texts-0"]["role"] == "title"
    assert assignments["dl-texts-0"]["warnings"] == [
        "Docling labeled the page-one title as a section header; it was conservatively promoted."
    ]
    assert assignments["dl-texts-11"]["role"] == "author"
    assert assignments["dl-texts-12"]["role"] == "affiliation"
    assert assignments["dl-texts-13"]["role"] == "paragraph"
    assert "sec-texts-0" not in {section["sectionId"] for section in structure["sections"]}
    assert structure["sections"][0]["titleBlockId"] == f"dl-texts-{abstract_index}"

    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    assert semantic["title"]["original"] == "A Long Paper Title"
    assert semantic["title"]["sourceBlockIds"] == ["dl-texts-0"]
    assert semantic["frontMatter"]["authors"][0]["original"] == "Ada Example"
    assert semantic["frontMatter"]["affiliations"][0]["original"] == "Example University"
    preamble = next(section for section in semantic["sections"] if section["id"] == "sec-preamble")
    assert any(
        item["type"] == "unit"
        and item["value"]["original"] == "Unclassified front matter 0"
        for item in preamble["content"]
    )


def test_does_not_promote_left_aligned_unnumbered_first_section() -> None:
    document = sample_docling_document()
    candidate = document["texts"][0]
    candidate["label"] = "section_header"
    candidate["orig"] = candidate["text"] = "Executive Summary"
    candidate["prov"][0]["charspan"] = [0, len("Executive Summary")]
    candidate["prov"][0]["bbox"]["l"] = 60
    candidate["prov"][0]["bbox"]["r"] = 260

    _evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)

    assert assignments["dl-texts-0"]["role"] == "heading"
    assert structure["sections"][0]["titleBlockId"] == "dl-texts-0"


def test_does_not_promote_centered_unnumbered_section_without_front_boundary() -> None:
    document = sample_docling_document()
    candidate = document["texts"][0]
    candidate["label"] = "section_header"
    candidate["orig"] = candidate["text"] = "Executive Summary"
    candidate["prov"][0]["charspan"] = [0, len("Executive Summary")]
    first_numbered = document["texts"][2]
    first_numbered["orig"] = first_numbered["text"] = "1 Methods"
    first_numbered["prov"][0]["charspan"] = [0, len("1 Methods")]

    _evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)

    assert assignments["dl-texts-0"]["role"] == "heading"
    assert structure["sections"][0]["titleBlockId"] == "dl-texts-0"


def test_heading_number_excludes_delimiter_and_sets_real_depth() -> None:
    assert adapter._heading_details("1. Introduction", {"level": 1}) == ("1", 1)
    assert adapter._heading_details("3.1. Details", {"level": 1}) == ("3.1", 2)
    assert adapter._heading_details("Appendix A. Supplement", {"level": 1}) == ("A", 1)
    assert adapter._heading_details("Appendix A.2. Proof", {"level": 1}) == ("A.2", 2)


def test_existing_semantic_builder_accepts_adapter_outputs() -> None:
    evidence, structure = docling_document_to_ir(sample_docling_document())
    document = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )

    assert document["title"]["original"] == "Native Docling Paper"
    introduction = next(section for section in document["sections"] if section["number"] == "1")
    paragraph = next(
        item["value"]
        for item in introduction["content"]
        if item["type"] == "unit" and item["value"]["kind"] == "paragraph"
    )
    assert paragraph["sourceBlockIds"] == ["dl-texts-1", "dl-texts-1-s2"]
    assert paragraph["pages"] == [1, 2]
    assert paragraph["citations"] == ["[1]"]
    figure = next(value for value in document["visualObjects"] if value["kind"] == "figure")
    assert figure["caption"] == "Figure 1: Native visual evidence."
    assert "Running head" not in json.dumps(document)


def test_semantic_builder_prefers_exact_pdf_caption_override() -> None:
    evidence, structure = docling_document_to_ir(sample_docling_document())
    figure = next(
        visual
        for page in structure["pages"]
        for visual in page["visualObjects"]
        if visual["kind"] == "figure"
    )
    figure["captionTextOverride"] = "Figure 1: Exact source-PDF caption."

    document = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )

    rendered_figure = next(
        visual for visual in document["visualObjects"] if visual["kind"] == "figure"
    )
    assert rendered_figure["caption"] == "Figure 1: Exact source-PDF caption."


def test_accepts_mapping_json_and_docling_document_protocol() -> None:
    document = sample_docling_document()
    exported = docling_document_to_dict(
        SimpleNamespace(export_to_dict=lambda: document)
    )
    parsed = docling_document_to_dict(json.dumps(document))

    assert exported == document
    assert parsed == document
    exported["name"] = "changed"
    assert document["name"] == "native-docling-paper"
    with pytest.raises(DoclingAdapterError, match="Expected"):
        docling_document_to_dict(object())


def test_docling_import_is_lazy_and_reports_optional_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(name: str):
        assert name == "docling.document_converter"
        raise ModuleNotFoundError("No module named 'docling'", name="docling")

    monkeypatch.setattr(adapter.importlib, "import_module", unavailable)
    with pytest.raises(DoclingUnavailableError, match="uv sync --extra docling"):
        adapter.convert_pdf_with_docling(Path("paper.pdf"))


def test_converter_explicitly_disables_ocr_for_digital_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeAcceleratorDevice:
        CPU = "cpu"

    class FakeAcceleratorOptions:
        def __init__(self, *, device: str):
            observed["accelerator_device"] = device
            self.device = device

    class FakeOnnxRuntimeObjectDetectionEngineOptions:
        def __init__(self, *, providers: list[str]):
            observed["onnx_providers"] = providers
            self.providers = providers

    class FakeLayoutObjectDetectionOptions:
        def __init__(
            self, *, engine_options: FakeOnnxRuntimeObjectDetectionEngineOptions
        ):
            observed["layout_engine_options"] = engine_options
            self.engine_options = engine_options

    class FakeHeadingHierarchyOptions:
        def __init__(self, *, enabled: bool):
            observed["heading_hierarchy_enabled"] = enabled
            self.enabled = enabled

    class FakeThreadedDoclingParseBackendOptions:
        def __init__(self, *, parser_threads: int):
            observed["parser_threads"] = parser_threads
            self.parser_threads = parser_threads

    class FakePdfPipelineOptions:
        def __init__(
            self,
            *,
            do_ocr: bool,
            document_timeout: float,
            artifacts_path: Path,
            accelerator_options: FakeAcceleratorOptions,
            layout_options: FakeLayoutObjectDetectionOptions,
            heading_hierarchy_options: FakeHeadingHierarchyOptions,
            generate_parsed_pages: bool,
        ):
            observed["pipeline_kwargs"] = {
                "do_ocr": do_ocr,
                "document_timeout": document_timeout,
                "artifacts_path": artifacts_path,
                "accelerator_options": accelerator_options,
                "layout_options": layout_options,
                "heading_hierarchy_options": heading_hierarchy_options,
                "generate_parsed_pages": generate_parsed_pages,
            }
            self.do_ocr = do_ocr

    class FakePdfFormatOption:
        def __init__(
            self,
            *,
            pipeline_options: FakePdfPipelineOptions,
            backend_options: FakeThreadedDoclingParseBackendOptions,
        ):
            observed["pipeline_options"] = pipeline_options
            observed["backend_options"] = backend_options

    class FakeInputFormat:
        PDF = "pdf"

    class FakeConversionStatus:
        SUCCESS = "success"

    class FakeDocumentConverter:
        def __init__(self, *, format_options: dict):
            observed["format_options"] = format_options

        def convert(self, source: Path, *, raises_on_error: bool):
            observed["source"] = source
            observed["raises_on_error"] = raises_on_error
            return SimpleNamespace(
                status=FakeConversionStatus.SUCCESS,
                errors=[],
                document=SimpleNamespace(
                    export_to_dict=lambda: sample_docling_document()
                )
            )

    modules = {
        "docling.document_converter": SimpleNamespace(
            DocumentConverter=FakeDocumentConverter,
            PdfFormatOption=FakePdfFormatOption,
        ),
        "docling.datamodel.base_models": SimpleNamespace(
            InputFormat=FakeInputFormat,
            ConversionStatus=FakeConversionStatus,
        ),
        "docling.datamodel.accelerator_options": SimpleNamespace(
            AcceleratorDevice=FakeAcceleratorDevice,
            AcceleratorOptions=FakeAcceleratorOptions,
        ),
        "docling.datamodel.object_detection_engine_options": SimpleNamespace(
            OnnxRuntimeObjectDetectionEngineOptions=(
                FakeOnnxRuntimeObjectDetectionEngineOptions
            )
        ),
        "docling.datamodel.pipeline_options": SimpleNamespace(
            PdfPipelineOptions=FakePdfPipelineOptions,
            LayoutObjectDetectionOptions=FakeLayoutObjectDetectionOptions,
            HeadingHierarchyOptions=FakeHeadingHierarchyOptions,
        ),
        "docling.datamodel.backend_options": SimpleNamespace(
            ThreadedDoclingParseBackendOptions=(
                FakeThreadedDoclingParseBackendOptions
            )
        ),
        "onnxruntime": SimpleNamespace(),
    }
    monkeypatch.setattr(adapter.importlib, "import_module", modules.__getitem__)
    monkeypatch.setenv("PAPERTRANS_DOCLING_ARTIFACTS_PATH", "/models/docling")
    monkeypatch.setenv("PAPERTRANS_DOCLING_DOCUMENT_TIMEOUT", "123.5")
    monkeypatch.setenv("PAPERTRANS_DOCLING_PARSER_THREADS", "3")

    result = adapter.convert_pdf_with_docling(Path("paper.pdf"))

    assert result["schema_name"] == "DoclingDocument"
    pipeline_kwargs = observed["pipeline_kwargs"]
    assert pipeline_kwargs["do_ocr"] is False
    assert pipeline_kwargs["document_timeout"] == 123.5
    assert pipeline_kwargs["artifacts_path"] == Path("/models/docling")
    assert pipeline_kwargs["accelerator_options"].device == FakeAcceleratorDevice.CPU
    assert (
        pipeline_kwargs["layout_options"].engine_options.providers
        == ["CPUExecutionProvider"]
    )
    assert pipeline_kwargs["heading_hierarchy_options"].enabled is True
    assert pipeline_kwargs["generate_parsed_pages"] is True
    assert observed["accelerator_device"] == FakeAcceleratorDevice.CPU
    assert observed["onnx_providers"] == ["CPUExecutionProvider"]
    assert observed["heading_hierarchy_enabled"] is True
    assert observed["parser_threads"] == 3
    assert observed["backend_options"].parser_threads == 3
    assert observed["source"] == Path("paper.pdf")
    assert observed["raises_on_error"] is False
    format_options = observed["format_options"]
    assert isinstance(format_options, dict)
    assert list(format_options) == [FakeInputFormat.PDF]
    assert format_options[FakeInputFormat.PDF].__class__ is FakePdfFormatOption
    assert observed["pipeline_options"].do_ocr is False


def test_converter_fails_closed_without_onnx_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def modules(name: str):
        if name == "onnxruntime":
            raise ModuleNotFoundError("No module named 'onnxruntime'", name="onnxruntime")
        return SimpleNamespace()

    monkeypatch.setattr(adapter.importlib, "import_module", modules)
    with pytest.raises(DoclingUnavailableError, match=r"uv sync --extra docling"):
        adapter.convert_pdf_with_docling(Path("paper.pdf"))


def test_converter_rejects_partial_success_with_sanitized_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Option:
        def __init__(self, **_kwargs):
            pass

    class InputFormat:
        PDF = "pdf"

    class ConversionStatus:
        SUCCESS = "success"
        PARTIAL_SUCCESS = "partial_success"

    class Converter:
        def __init__(self, **_kwargs):
            pass

        def convert(self, _source: Path, *, raises_on_error: bool):
            assert raises_on_error is False
            return SimpleNamespace(
                status=ConversionStatus.PARTIAL_SUCCESS,
                errors=[SimpleNamespace(error_message="page 1\nfailed\x00to parse")],
                document=SimpleNamespace(export_to_dict=lambda: sample_docling_document()),
            )

    modules = {
        "docling.document_converter": SimpleNamespace(
            DocumentConverter=Converter,
            PdfFormatOption=Option,
        ),
        "docling.datamodel.base_models": SimpleNamespace(
            InputFormat=InputFormat,
            ConversionStatus=ConversionStatus,
        ),
        "docling.datamodel.accelerator_options": SimpleNamespace(
            AcceleratorDevice=SimpleNamespace(CPU="cpu"),
            AcceleratorOptions=Option,
        ),
        "docling.datamodel.object_detection_engine_options": SimpleNamespace(
            OnnxRuntimeObjectDetectionEngineOptions=Option
        ),
        "docling.datamodel.pipeline_options": SimpleNamespace(
            PdfPipelineOptions=Option,
            LayoutObjectDetectionOptions=Option,
            HeadingHierarchyOptions=Option,
        ),
        "onnxruntime": SimpleNamespace(),
    }
    monkeypatch.setattr(adapter.importlib, "import_module", modules.__getitem__)

    with pytest.raises(
        DoclingAdapterError,
        match="conversion status partial_success: page 1 failed to parse",
    ) as captured:
        adapter.convert_pdf_with_docling(Path("paper.pdf"))
    assert "\n" not in str(captured.value)
    assert "\x00" not in str(captured.value)


def test_runtime_conversion_rejects_zero_size_but_allows_one_blank_page() -> None:
    document = sample_docling_document()
    document["pages"]["1"]["size"] = {"width": 0, "height": 0}
    document["texts"] = [
        item
        for item in document["texts"]
        if not any(prov.get("page_no") == 1 for prov in item.get("prov", []))
    ]
    document["pictures"] = []

    issues = adapter._runtime_document_issues(document)

    assert issues == ["page 1 (zero or missing page size)"]

    document["pages"]["1"]["size"] = {"width": 612, "height": 792}
    assert adapter._runtime_document_issues(document) == []


def test_runtime_conversion_rejects_document_without_textual_body() -> None:
    document = sample_docling_document()
    document["texts"] = []

    assert adapter._runtime_document_issues(document) == [
        "document (no textual body content)"
    ]


def test_preserves_unclassified_front_matter_as_translatable_preamble() -> None:
    document = sample_docling_document()
    front = {
        "self_ref": "#/texts/11",
        "label": "text",
        "orig": "Ada Lovelace · Example University",
        "text": "Ada Lovelace · Example University",
        "prov": [
            {
                "page_no": 1,
                "bbox": {"l": 80, "t": 715, "r": 520, "b": 700, "coord_origin": "BOTTOMLEFT"},
                "charspan": [0, 33],
            }
        ],
    }
    document["texts"].append(front)
    document["body"]["children"].insert(1, {"$ref": "#/texts/11"})

    evidence, structure = docling_document_to_ir(document)
    assignment = _assignments(structure)["dl-texts-11"]
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )

    assert assignment["role"] == "paragraph"
    preamble = semantic["sections"][0]
    assert preamble["title"]["original"] == "Preamble"
    assert preamble["content"][0]["value"]["original"] == front["orig"]


def test_joins_strong_cross_page_continuation_across_text_items() -> None:
    document = sample_docling_document()
    paragraph = document["texts"][1]
    paragraph["orig"] = paragraph["text"] = "A paragraph continues"
    paragraph["prov"] = [paragraph["prov"][0]]
    paragraph["prov"][0]["charspan"] = [0, len(paragraph["text"])]
    continuation = {
        "self_ref": "#/texts/11",
        "label": "paragraph",
        "orig": "across a separate Docling text item.",
        "text": "across a separate Docling text item.",
        "prov": [
            {
                "page_no": 2,
                "bbox": {"l": 60, "t": 760, "r": 540, "b": 720, "coord_origin": "BOTTOMLEFT"},
                "charspan": [0, 36],
            }
        ],
    }
    document["texts"].append(continuation)
    children = document["groups"][0]["children"]
    children.insert(children.index({"$ref": "#/texts/5"}), {"$ref": "#/texts/11"})

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)

    assert assignments["dl-texts-11"]["paragraphId"] == assignments["dl-texts-1"]["paragraphId"]
    assert assignments["dl-texts-11"]["continuesFrom"] == "dl-texts-1"
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    introduction = next(section for section in semantic["sections"] if section["number"] == "1")
    merged = next(
        item["value"]
        for item in introduction["content"]
        if item["type"] == "unit" and item["value"]["id"] == assignments["dl-texts-1"]["paragraphId"]
    )
    assert merged["sourceBlockIds"] == ["dl-texts-1", "dl-texts-11"]


def test_parent_runs_worker_without_stdout_protocol_and_reads_atomic_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    work_dir = tmp_path / "work"
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        kwargs["stderr"].write(b"worker diagnostic\n")
        worker_output = Path(command[-1])
        observed["worker_output"] = worker_output
        worker_output.write_text(
            json.dumps(sample_docling_document()), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter._run_docling_worker(source, work_dir, timeout_seconds=12.5)

    command = observed["command"]
    assert command[:3] == [adapter.sys.executable, "-m", "papertrans.docling_worker"]
    assert command[3] == str(source)
    worker_output = Path(command[4])
    assert worker_output.parent == work_dir
    assert worker_output.name.startswith(".docling-document-")
    assert worker_output.suffix == ".json"
    assert observed["stdout"] is adapter.subprocess.DEVNULL
    assert observed["cwd"] == work_dir.resolve()
    assert observed["check"] is False
    assert observed["timeout"] == 12.5
    assert (work_dir / "docling-worker.log").read_text() == "worker diagnostic\n"
    assert not worker_output.exists()
    assert json.loads((work_dir / "docling-document.json").read_text()) == result
    assert result["schema_name"] == "DoclingDocument"


def test_parent_retries_native_parser_crash_once_with_one_parser_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    work_dir = tmp_path / "work"
    calls: list[dict[str, object]] = []
    retry_directories: list[Path] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert "env" not in kwargs
            kwargs["stderr"].write(b"native parser crashed\n")
            return SimpleNamespace(returncode=-adapter.signal.SIGSEGV)
        assert kwargs["env"]["PAPERTRANS_DOCLING_PARSER_THREADS"] == "1"
        retry_directory = Path(kwargs["cwd"])
        retry_directories.append(retry_directory)
        assert retry_directory.parent == work_dir.resolve()
        assert retry_directory.name.startswith(".docling-retry-")
        kwargs["stderr"].write(b"single-thread retry succeeded\n")
        Path(command[-1]).write_text(
            json.dumps(sample_docling_document()), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.delenv("PAPERTRANS_DOCLING_PARSER_THREADS", raising=False)
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter._run_docling_worker(source, work_dir, timeout_seconds=12.5)

    assert len(calls) == 2
    assert len(retry_directories) == 1
    assert not retry_directories[0].exists()
    assert result["schema_name"] == "DoclingDocument"
    log = (work_dir / "docling-worker.log").read_text()
    assert "native parser crashed" in log
    assert "retrying once with parser_threads=1" in log
    assert "single-thread retry succeeded" in log


def test_parent_retries_partial_conversion_once_with_one_parser_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    work_dir = tmp_path / "work"
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            kwargs["stderr"].write(b"partial conversion\n")
            return SimpleNamespace(returncode=adapter._DOCLING_PARTIAL_EXIT_CODE)
        assert kwargs["env"]["PAPERTRANS_DOCLING_PARSER_THREADS"] == "1"
        Path(command[-1]).write_text(
            json.dumps(sample_docling_document()), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.delenv("PAPERTRANS_DOCLING_PARSER_THREADS", raising=False)
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter._run_docling_worker(source, work_dir, timeout_seconds=12.5)

    assert len(calls) == 2
    assert result["schema_name"] == "DoclingDocument"
    log = (work_dir / "docling-worker.log").read_text()
    assert "partial conversion" in log
    assert "retrying once with parser_threads=1" in log


def test_parent_does_not_retry_persistent_partial_conversion_more_than_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def partial_run(_command: list[str], **kwargs):
        nonlocal calls
        calls += 1
        kwargs["stderr"].write(b"partial conversion\n")
        return SimpleNamespace(returncode=adapter._DOCLING_PARTIAL_EXIT_CODE)

    monkeypatch.delenv("PAPERTRANS_DOCLING_PARSER_THREADS", raising=False)
    monkeypatch.setattr(adapter.subprocess, "run", partial_run)
    with pytest.raises(adapter.DoclingWorkerError, match="status 75"):
        adapter._run_docling_worker(tmp_path / "paper.pdf", tmp_path / "work")

    assert calls == 2


def test_parent_rejects_nonfinite_worker_timeout_without_starting_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("worker must not start")

    monkeypatch.setattr(adapter.subprocess, "run", unexpected_run)
    with pytest.raises(DoclingAdapterError, match="positive number"):
        adapter._run_docling_worker(
            tmp_path / "paper.pdf", tmp_path / "work", timeout_seconds=float("nan")
        )


def test_parent_caps_worker_stderr_and_raises_for_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"
    tail = b"FINAL-DIAGNOSTIC"

    def failed_run(_command: list[str], **kwargs):
        kwargs["stderr"].write(
            b"x" * (adapter._DOCLING_WORKER_LOG_LIMIT_BYTES + 200) + tail
        )
        return SimpleNamespace(returncode=139)

    monkeypatch.setattr(adapter.subprocess, "run", failed_run)
    with pytest.raises(adapter.DoclingWorkerError, match="status 139"):
        adapter._run_docling_worker(tmp_path / "paper.pdf", work_dir)

    log = (work_dir / "docling-worker.log").read_bytes()
    assert len(log) <= adapter._DOCLING_WORKER_LOG_LIMIT_BYTES
    assert log.startswith(b"[PaperTrans truncated ")
    assert log.endswith(tail)
    notice, retained = log.split(b"\n", 1)
    omitted = int(notice.removeprefix(b"[PaperTrans truncated ").split(b" ", 1)[0])
    assert omitted == adapter._DOCLING_WORKER_LOG_LIMIT_BYTES + 200 + len(tail) - len(retained)


def test_parent_turns_worker_timeout_into_specific_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"

    def timed_out(command: list[str], **kwargs):
        kwargs["stderr"].write(b"last worker line\n")
        raise adapter.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(adapter.subprocess, "run", timed_out)
    with pytest.raises(adapter.DoclingWorkerTimeoutError, match="3 seconds"):
        adapter._run_docling_worker(
            tmp_path / "paper.pdf", work_dir, timeout_seconds=3
        )
    log = (work_dir / "docling-worker.log").read_text()
    assert "last worker line" in log
    assert "timed out after 3 seconds" in log


def test_parent_removes_published_and_temporary_worker_output_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"
    observed: dict[str, Path] = {}

    def timed_out(command: list[str], **kwargs):
        worker_output = Path(command[-1])
        observed["output"] = worker_output
        worker_output.write_text('{"partial": true}', encoding="utf-8")
        worker_temp = worker_output.parent / f".{worker_output.name}.fixture.tmp"
        observed["temp"] = worker_temp
        worker_temp.write_text("partial", encoding="utf-8")
        raise adapter.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(adapter.subprocess, "run", timed_out)
    with pytest.raises(adapter.DoclingWorkerTimeoutError):
        adapter._run_docling_worker(tmp_path / "paper.pdf", work_dir, timeout_seconds=3)

    assert not observed["output"].exists()
    assert not observed["temp"].exists()


def test_parent_cleans_worker_output_and_reraises_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"
    observed: dict[str, Path] = {}

    def interrupted(command: list[str], **_kwargs):
        worker_output = Path(command[-1])
        observed["output"] = worker_output
        worker_output.write_text('{"partial": true}', encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(adapter.subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        adapter._run_docling_worker(tmp_path / "paper.pdf", work_dir)

    assert not observed["output"].exists()


def test_parent_cleans_worker_output_when_log_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"
    observed: dict[str, Path] = {}

    def completed(command: list[str], **_kwargs):
        worker_output = Path(command[-1])
        observed["output"] = worker_output
        worker_output.write_text(
            json.dumps(sample_docling_document()), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    original_write_bytes = Path.write_bytes

    def fail_log_write(path: Path, data: bytes) -> int:
        if path.name == "docling-worker.log":
            raise OSError("disk full")
        return original_write_bytes(path, data)

    monkeypatch.setattr(adapter.subprocess, "run", completed)
    monkeypatch.setattr(Path, "write_bytes", fail_log_write)
    with pytest.raises(OSError, match="disk full"):
        adapter._run_docling_worker(tmp_path / "paper.pdf", work_dir)

    assert not observed["output"].exists()


def test_extract_api_persists_three_semantic_pipeline_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    work_dir = tmp_path / "work"
    evidence_path = work_dir / "layout-evidence.json"
    structure_path = work_dir / "structure.json"
    visuals_path = work_dir / "visual-objects.json"
    expected_document = sample_docling_document()
    observed: dict[str, object] = {}

    def fake_worker(value: Path, worker_dir: Path, *, timeout_seconds: float):
        observed["worker_source"] = value
        observed["worker_dir"] = worker_dir
        observed["worker_timeout"] = timeout_seconds
        return expected_document

    monkeypatch.setattr(adapter, "_run_docling_worker", fake_worker)

    def fake_render(value: Path, structure: dict, assets_dir: Path) -> list[dict]:
        observed["source"] = value
        observed["assets"] = assets_dir
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else None
        # Rendering happens before persistence, so construct only the fields the
        # downstream semantic builder requires.
        page_sizes = {1: (600, 800), 2: (600, 800)}
        rendered = []
        for page in structure["pages"]:
            width, height = page_sizes[page["pageNumber"]]
            for visual in page["visualObjects"]:
                x0, y0, x1, y1 = visual["bboxNormalized"]
                rendered.append(
                    {
                        **visual,
                        "pageNumber": page["pageNumber"],
                        "asset": f"assets/{visual['objectId']}.png",
                        "bboxPdf": [x0 * width, y0 * height, x1 * width, y1 * height],
                    }
                )
        assert evidence is None
        return rendered

    monkeypatch.setattr(adapter, "render_visual_objects", fake_render)
    evidence, structure, visuals = extract_docling_semantics(
        source, work_dir, evidence_path, structure_path, visuals_path
    )

    assert observed == {
        "worker_source": source.resolve(),
        "worker_dir": work_dir,
        "worker_timeout": None,
        "source": source.resolve(),
        "assets": work_dir / "assets",
    }
    assert json.loads(evidence_path.read_text()) == evidence
    assert json.loads(structure_path.read_text()) == structure
    assert json.loads(visuals_path.read_text()) == visuals
    build_semantic_document(evidence, structure, visuals)


def test_suppresses_only_repeated_blank_headings_that_anchor_figures(
    tmp_path: Path,
) -> None:
    source = tmp_path / "blank-heading-metadata.pdf"
    pdf = fitz.open()
    for page_number in range(1, 5):
        page = pdf.new_page(width=600, height=800)
        if page_number in {2, 3, 4}:
            page.draw_rect(fitz.Rect(60, 200, 540, 650), fill=(0, 0, 0))
        if page_number == 1:
            page.insert_text((60, 80), "Real preceding body")
        if page_number == 4:
            page.insert_text((180, 92), "bicubic", fontsize=8)
    pdf.save(source)
    pdf.close()

    evidence = {
        "pages": [
            {
                "pageNumber": 1,
                "blocks": [
                    {
                        "blockId": "body-1",
                        "text": "Real preceding body",
                        "bboxNormalized": [0.1, 0.07, 0.4, 0.12],
                    }
                ],
            },
            *[
                {
                    "pageNumber": page_number,
                    "blocks": [
                        {
                            "blockId": f"heading-{page_number}",
                            "text": "Invisible Layer Metadata"
                            if page_number in {2, 3}
                            else "bicubic",
                            "bboxNormalized": [0.18, 0.1, 0.45, 0.15]
                            if page_number in {2, 3}
                            else [0.29, 0.1, 0.36, 0.125],
                        }
                    ],
                }
                for page_number in (2, 3, 4)
            ],
        ]
    }
    structure = {
        "sections": [
            {
                "sectionId": "sec-main",
                "titleBlockId": "body-1",
                "parentSectionId": None,
            },
            *[
                {
                    "sectionId": f"sec-heading-{page_number}",
                    "titleBlockId": f"heading-{page_number}",
                    "parentSectionId": "sec-main",
                }
                for page_number in (2, 3, 4)
            ],
        ],
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    {
                        "blockId": "body-1",
                        "role": "paragraph",
                        "readingOrder": 1,
                        "sectionId": "sec-main",
                        "paragraphId": "para-body-1",
                        "continuesFrom": None,
                        "hidden": False,
                        "warnings": [],
                    }
                ],
                "visualObjects": [],
            },
            *[
                {
                    "pageNumber": page_number,
                    "blockAssignments": [
                        {
                            "blockId": f"heading-{page_number}",
                            "role": "heading",
                            "readingOrder": 1,
                            "sectionId": f"sec-heading-{page_number}",
                            "paragraphId": None,
                            "continuesFrom": None,
                            "hidden": False,
                            "warnings": [],
                        }
                    ],
                    "visualObjects": [
                        {
                            "objectId": f"figure-{page_number}",
                            "kind": "figure",
                            "bboxNormalized": [0.1, 0.25, 0.9, 0.82],
                            "captionBlockIds": [],
                            "insertAfterBlockId": f"heading-{page_number}",
                        }
                    ],
                }
                for page_number in (2, 3, 4)
            ],
        ],
        "doclingDiagnostics": {},
        "warnings": [],
    }
    evidence["pages"][1]["blocks"].append(
        {
            "blockId": "caption-2",
            "text": "Figure 2: Metadata example.",
            "bboxNormalized": [0.1, 0.84, 0.9, 0.88],
        }
    )
    structure["pages"][1]["blockAssignments"].append(
        {
            "blockId": "caption-2",
            "role": "caption",
            "readingOrder": 2,
            "sectionId": "sec-heading-2",
            "paragraphId": None,
            "continuesFrom": None,
            "hidden": False,
            "warnings": [],
        }
    )
    structure["pages"][1]["visualObjects"][0]["captionBlockIds"] = ["caption-2"]

    adapter._suppress_blank_docling_headings(source, evidence, structure)

    assignments = _assignments(structure)
    assert assignments["heading-2"]["role"] == "noise"
    assert assignments["heading-2"]["hidden"] is True
    assert assignments["heading-3"]["role"] == "noise"
    assert assignments["heading-3"]["hidden"] is True
    assert assignments["heading-4"]["role"] == "heading"
    assert assignments["heading-4"]["hidden"] is False
    assert assignments["caption-2"]["sectionId"] == "sec-main"
    assert structure["doclingDiagnostics"]["suppressedBlankHeadingBlockIds"] == [
        "heading-2",
        "heading-3",
    ]
    assert structure["doclingDiagnostics"]["blankVisibleHeadingBlockIds"] == []
    assert {section["sectionId"] for section in structure["sections"]} == {
        "sec-main",
        "sec-heading-4",
    }
    for page_number in (2, 3):
        assert structure["pages"][page_number - 1]["visualObjects"][0][
            "insertAfterBlockId"
        ] == "body-1"


def test_absorbs_aligned_panel_headings_and_reanchors_following_figures(
    tmp_path: Path,
) -> None:
    source = tmp_path / "panel-headings.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((60, 80), "H. Additional Results")
    page.insert_text((60, 110), "Final real paragraph")
    page = pdf.new_page(width=600, height=800)
    page.insert_text((180, 92), "bicubic", fontsize=8)
    page.insert_text((365, 92), "LDM-BSR", fontsize=8)
    page.draw_rect(fitz.Rect(90, 95, 510, 680), fill=(0.2, 0.2, 0.2))
    page = pdf.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(90, 95, 510, 680), fill=(0.3, 0.3, 0.3))
    pdf.save(source)
    pdf.close()

    evidence = {
        "pages": [
            {
                "pageNumber": 1,
                "blocks": [
                    {
                        "blockId": "body-1",
                        "text": "Final real paragraph",
                        "bboxNormalized": [0.1, 0.1, 0.45, 0.15],
                    }
                ],
            },
            {
                "pageNumber": 2,
                "blocks": [
                    {
                        "blockId": "panel-left",
                        "text": "bicubic",
                        "bboxNormalized": [0.29, 0.105, 0.36, 0.116],
                    },
                    {
                        "blockId": "panel-right",
                        "text": "LDM-BSR",
                        "bboxNormalized": [0.6, 0.105, 0.69, 0.116],
                    },
                    {
                        "blockId": "caption-2",
                        "text": "Figure 2: Comparison.",
                        "bboxNormalized": [0.1, 0.86, 0.9, 0.89],
                    },
                ],
            },
            {
                "pageNumber": 3,
                "blocks": [
                    {
                        "blockId": "caption-3",
                        "text": "Figure 3: More comparisons.",
                        "bboxNormalized": [0.1, 0.86, 0.9, 0.89],
                    }
                ],
            },
        ]
    }
    structure = {
        "sections": [
            {
                "sectionId": "sec-main",
                "titleBlockId": "body-1",
                "parentSectionId": None,
            },
            {
                "sectionId": "sec-left",
                "titleBlockId": "panel-left",
                "parentSectionId": "sec-main",
            },
            {
                "sectionId": "sec-right",
                "titleBlockId": "panel-right",
                "parentSectionId": "sec-left",
            },
        ],
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    {
                        "blockId": "body-1",
                        "role": "paragraph",
                        "readingOrder": 1,
                        "sectionId": "sec-main",
                        "paragraphId": "para-body-1",
                        "continuesFrom": None,
                        "hidden": False,
                        "warnings": [],
                    }
                ],
                "visualObjects": [],
            },
            {
                "pageNumber": 2,
                "blockAssignments": [
                    {
                        "blockId": "panel-left",
                        "role": "heading",
                        "readingOrder": 1,
                        "sectionId": "sec-left",
                        "paragraphId": None,
                        "continuesFrom": None,
                        "hidden": False,
                        "warnings": [],
                    },
                    {
                        "blockId": "panel-right",
                        "role": "heading",
                        "readingOrder": 2,
                        "sectionId": "sec-right",
                        "paragraphId": None,
                        "continuesFrom": None,
                        "hidden": False,
                        "warnings": [],
                    },
                    {
                        "blockId": "caption-2",
                        "role": "caption",
                        "readingOrder": 3,
                        "sectionId": "sec-right",
                        "paragraphId": None,
                        "continuesFrom": None,
                        "hidden": False,
                        "warnings": [],
                    },
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-2",
                        "kind": "figure",
                        "bboxNormalized": [0.15, 0.112, 0.85, 0.85],
                        "captionBlockIds": ["caption-2"],
                        "insertAfterBlockId": "panel-right",
                    }
                ],
            },
            {
                "pageNumber": 3,
                "blockAssignments": [
                    {
                        "blockId": "caption-3",
                        "role": "caption",
                        "readingOrder": 1,
                        "sectionId": "sec-right",
                        "paragraphId": None,
                        "continuesFrom": None,
                        "hidden": False,
                        "warnings": [],
                    }
                ],
                "visualObjects": [
                    {
                        "objectId": "figure-3",
                        "kind": "figure",
                        "bboxNormalized": [0.15, 0.112, 0.85, 0.85],
                        "captionBlockIds": ["caption-3"],
                        "insertAfterBlockId": "panel-right",
                    }
                ],
            },
        ],
        "doclingDiagnostics": {},
        "warnings": [],
    }

    adapter._absorb_aligned_figure_panel_headings(source, evidence, structure)

    assignments = _assignments(structure)
    assert assignments["panel-left"]["role"] == "noise"
    assert assignments["panel-left"]["hidden"] is True
    assert assignments["panel-right"]["role"] == "noise"
    assert assignments["panel-right"]["hidden"] is True
    assert assignments["caption-2"]["sectionId"] == "sec-main"
    assert assignments["caption-3"]["sectionId"] == "sec-main"
    assert {section["sectionId"] for section in structure["sections"]} == {
        "sec-main"
    }
    for page_number in (2, 3):
        assert structure["pages"][page_number - 1]["visualObjects"][0][
            "insertAfterBlockId"
        ] == "body-1"
    assert structure["pages"][1]["visualObjects"][0]["bboxNormalized"][1] <= 0.101
    assert structure["doclingDiagnostics"]["unabsorbedPanelHeadingBlockIds"] == []
    assert structure["doclingDiagnostics"]["absorbedPanelHeadingGroups"] == [
        {
            "objectId": "figure-2",
            "pageNumber": 2,
            "panelHeadingBlockIds": ["panel-left", "panel-right"],
            "fallbackBlockId": "body-1",
            "fallbackSectionId": "sec-main",
        }
    ]
