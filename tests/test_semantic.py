import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import papertrans.semantic_translate as semantic_translation
from papertrans.deterministic_structure import analyze_layout_deterministic, evaluate_structure
from papertrans.hybrid_structure import _merge_reviewed_pages, _stable_cache_payload, select_review_pages
from papertrans.semantic import (
    _is_descendant_section,
    _join_source_parts,
    _select_visual_reference_anchor,
    _should_preserve_paired_figure_parent,
    build_semantic_document,
    iter_translatable_units,
)
from papertrans.semantic_render import render_semantic_document
from papertrans.semantic_translate import _command, _validate_result
from papertrans.structure import validate_structure_batch
from papertrans.structure import _segment_text_block


def sample_semantic_inputs():
    evidence = {
        "sourceFile": "paper.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "blocks": [
                    {"blockId": "p1-b1", "text": "Paper Title"},
                    {"blockId": "p1-b2", "text": "1 Introduction"},
                    {"blockId": "p1-b3", "text": "A paragraph with Figure 1 and [1],"},
                    {"blockId": "p1-b4", "text": "continued across a column."},
                    {"blockId": "p1-b5", "text": "Page 1"},
                    {"blockId": "p1-b6", "text": "Figure 1: Original caption."},
                    {"blockId": "p1-b7", "text": "References"},
                    {"blockId": "p1-b8", "text": "[1] Example reference."},
                ],
            }
        ],
    }
    assignments = [
        ("p1-b1", "title", None, None, False, None),
        ("p1-b2", "heading", "sec-1", None, False, None),
        ("p1-b3", "paragraph", "sec-1", "para-1", False, None),
        ("p1-b4", "paragraph", "sec-1", "para-1", False, None),
        ("p1-b5", "page_number", None, None, True, None),
        ("p1-b6", "caption", "sec-1", "caption-1", False, "Figure 1"),
        ("p1-b7", "heading", "sec-references", None, False, None),
        ("p1-b8", "reference", "sec-references", "ref-1", False, "1"),
    ]
    structure = {
        "warnings": [],
        "sections": [
            {"sectionId": "sec-1", "number": "1", "titleBlockId": "p1-b2", "level": 1, "parentSectionId": None, "pageStart": 1},
            {"sectionId": "sec-references", "number": None, "titleBlockId": "p1-b7", "level": 1, "parentSectionId": None, "pageStart": 1},
        ],
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    {
                        "blockId": block_id,
                        "role": role,
                        "readingOrder": index,
                        "sectionId": section_id,
                        "paragraphId": paragraph_id,
                        "hidden": hidden,
                        "citations": ["[1]"] if block_id == "p1-b3" else [],
                        "objectReferences": ["Figure 1"] if block_id == "p1-b3" else [],
                        "referenceLabel": label,
                        "confidence": 1,
                        "warnings": [],
                    }
                    for index, (block_id, role, section_id, paragraph_id, hidden, label) in enumerate(assignments, 1)
                ],
                "visualObjects": [],
            }
        ],
    }
    visuals = [
        {
            "objectId": "figure-1",
            "kind": "figure",
            "label": "Figure 1",
            "captionBlockIds": ["p1-b6"],
            "insertAfterBlockId": "p1-b4",
            "pageNumber": 1,
            "asset": "assets/figure-1.png",
            "bboxNormalized": [0.1, 0.1, 0.9, 0.5],
            "bboxPdf": [10, 10, 90, 50],
            "confidence": 1,
            "warnings": [],
        }
    ]
    return evidence, structure, visuals


def test_builds_real_sections_and_semantic_paragraphs():
    document = build_semantic_document(*sample_semantic_inputs())
    assert document["sections"][0]["title"]["original"] == "Introduction"
    content = document["sections"][0]["content"]
    assert [item["type"] for item in content] == ["unit", "visual"]
    assert content[0]["value"]["sourceBlockIds"] == ["p1-b3", "p1-b4"]
    assert "continued across a column" in content[0]["value"]["original"]
    assert "Page 1" not in json.dumps(document)
    assert content[1]["value"]["caption"] == "Figure 1: Original caption."


def test_trailing_unheaded_figures_do_not_become_reference_children(
    tmp_path: Path,
):
    evidence, structure, visuals = sample_semantic_inputs()
    visuals[0]["label"] = "Figure 99"
    visuals[0]["insertAfterBlockId"] = "p1-b8"

    document = build_semantic_document(evidence, structure, visuals)

    supplemental = document["sections"][-1]
    assert supplemental["syntheticUnheaded"] is True
    assert supplemental["content"][0]["value"]["label"] == "Figure 99"
    references = next(
        section
        for section in document["sections"]
        if section["title"]["original"] == "References"
    )
    assert all(item["type"] != "visual" for item in references["content"])
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "figure-1.png").write_bytes(b"png")
    html = render_semantic_document(document, tmp_path, tmp_path / "html")
    assert ">Supplemental Material</h" not in html.read_text(encoding="utf-8")
    markdown_qa = json.loads(
        (tmp_path / "html" / "markdown-qa.json").read_text(encoding="utf-8")
    )
    assert markdown_qa["status"] == "passed"


def test_join_source_parts_only_closes_a_verified_singleton_quote():
    assert _join_source_parts(["RESPONSE: ' {}", "'"]) == "RESPONSE: ' {}'"
    assert _join_source_parts(["He said", '"hello"']) == 'He said "hello"'
    assert _join_source_parts(["This is", "'quoted'"]) == "This is 'quoted'"
    assert _join_source_parts(["Don't say", "'", "hello", "'"]) == (
        "Don't say 'hello'"
    )
    assert _join_source_parts(["the models'", "outputs differ"]) == (
        "the models' outputs differ"
    )


def test_verbatim_unit_preserves_internal_newlines_and_indentation():
    evidence = {
        "sourceFile": "code.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "blocks": [
                    {"blockId": "code", "text": "if x:\n    y = 1\n    return y"}
                ],
            }
        ],
    }
    structure = {
        "warnings": [],
        "sections": [],
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    {
                        "blockId": "code",
                        "role": "verbatim",
                        "readingOrder": 1,
                        "sectionId": None,
                        "paragraphId": "verbatim-code",
                        "hidden": False,
                        "confidence": 1,
                        "warnings": [],
                    }
                ],
                "visualObjects": [],
            }
        ],
    }

    document = build_semantic_document(evidence, structure, [])

    assert document["sections"][0]["content"][0]["value"]["original"] == (
        "if x:\n    y = 1\n    return y"
    )


def test_visuals_use_latest_preceding_exact_reference_as_semantic_anchor():
    block_specs = [
        ("title", "Paper", "title", None, None),
        ("heading-j", "J. Results", "heading", "sec-j", None),
        ("intro-j", "Table 7 shows detailed results.", "paragraph", "sec-j", "p-j"),
        ("heading-m", "M. Evaluation", "heading", "sec-m", None),
        ("intro-m", "We compare both methods in Table 8.", "paragraph", "sec-m", "p-m"),
        ("heading-o", "O. Prompts", "heading", "sec-o", None),
        ("long-a", "A paragraph starts here", "paragraph", "sec-o", "p-o"),
        ("footnote", "Table note", "footnote", "sec-o", "p-note"),
        ("long-b", "and continues later.", "paragraph", "sec-o", "p-o"),
    ]
    evidence = {
        "sourceFile": "floats.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "blocks": [
                    {"blockId": block_id, "text": text}
                    for block_id, text, _role, _section, _paragraph in block_specs
                ],
            }
        ],
    }
    structure = {
        "warnings": [],
        "sections": [
            {
                "sectionId": section_id,
                "number": None,
                "titleBlockId": heading_id,
                "level": 1,
                "parentSectionId": None,
                "pageStart": 1,
            }
            for section_id, heading_id in (
                ("sec-j", "heading-j"),
                ("sec-m", "heading-m"),
                ("sec-o", "heading-o"),
            )
        ],
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    {
                        "blockId": block_id,
                        "role": role,
                        "readingOrder": index,
                        "sectionId": section_id,
                        "paragraphId": paragraph_id,
                        "hidden": False,
                        "citations": [],
                        "objectReferences": (
                            ["Table 7"]
                            if block_id == "intro-j"
                            else ["Table 8"]
                            if block_id == "intro-m"
                            else []
                        ),
                        "referenceLabel": None,
                        "confidence": 1,
                        "warnings": [],
                    }
                    for index, (
                        block_id,
                        _text,
                        role,
                        section_id,
                        paragraph_id,
                    ) in enumerate(block_specs, 1)
                ],
                "visualObjects": [],
            }
        ],
    }
    visuals = [
        {
            "objectId": "table-7",
            "kind": "table",
            "label": "Table 7",
            "captionBlockIds": [],
            "insertAfterBlockId": "long-a",
            "pageNumber": 1,
            "asset": "assets/table-7.png",
            "bboxNormalized": [0.1, 0.1, 0.9, 0.4],
            "bboxPdf": [10, 10, 90, 40],
            "confidence": 1,
            "warnings": [],
        },
        {
            "objectId": "table-8",
            "kind": "table",
            "label": "Table 8",
            "captionBlockIds": [],
            "insertAfterBlockId": "footnote",
            "pageNumber": 1,
            "asset": "assets/table-8.png",
            "bboxNormalized": [0.1, 0.6, 0.9, 0.7],
            "bboxPdf": [10, 60, 90, 70],
            "confidence": 1,
            "warnings": [],
        },
    ]

    document = build_semantic_document(evidence, structure, visuals)

    content_by_section = {
        section["id"]: section["content"] for section in document["sections"]
    }
    assert [
        item["value"]["objectId"]
        for item in content_by_section["sec-j"]
        if item["type"] == "visual"
    ] == ["table-7"]
    assert [
        item["value"]["objectId"]
        for item in content_by_section["sec-m"]
        if item["type"] == "visual"
    ] == ["table-8"]
    assert not any(
        item["type"] == "visual" for item in content_by_section["sec-o"]
    )
    rendered = {visual["objectId"]: visual for visual in document["visualObjects"]}
    assert rendered["table-7"]["insertAfterBlockId"] == "intro-j"
    assert rendered["table-8"]["insertAfterBlockId"] == "intro-m"


def test_synthesizes_section_for_document_without_headings():
    evidence = {
        "sourceFile": "unheaded.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "blocks": [{"blockId": "p1-body", "text": "Unheaded body text."}],
            }
        ],
    }
    structure = {
        "warnings": [],
        "sections": [],
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [
                    {
                        "blockId": "p1-body",
                        "role": "paragraph",
                        "readingOrder": 1,
                        "sectionId": None,
                        "paragraphId": "p1-body",
                        "hidden": False,
                        "confidence": 1,
                        "warnings": [],
                    }
                ],
                "visualObjects": [],
            }
        ],
    }

    document = build_semantic_document(evidence, structure, [])

    assert document["sections"][0]["title"]["original"] == "Document"
    assert document["sections"][0]["content"][0]["value"]["original"] == "Unheaded body text."


def test_section_title_strips_only_a_leading_number_and_delimiter():
    cases = [
        ("1. Introduction", "1", "Introduction"),
        ("3.1 Results", "3.1", "Results"),
        ("Results improved by 3.1 percent", "3.1", "Results improved by 3.1 percent"),
    ]
    for raw_title, number, expected in cases:
        evidence, structure, visuals = sample_semantic_inputs()
        evidence["pages"][0]["blocks"][1]["text"] = raw_title
        structure["sections"][0]["number"] = number

        document = build_semantic_document(evidence, structure, visuals)

        assert document["sections"][0]["title"]["original"] == expected
        assert document["sections"][0]["title"]["sourceHeading"] == raw_title


def test_splits_author_from_known_affiliation():
    evidence, structure, visuals = sample_semantic_inputs()
    evidence["pages"][0]["blocks"].insert(1, {"blockId": "p1-author", "text": "Ada Lovelace Example University"})
    evidence["pages"][0]["blocks"].insert(2, {"blockId": "p1-affiliation", "text": "Example University"})
    assignments = structure["pages"][0]["blockAssignments"]
    for item in assignments:
        if item["blockId"] != "p1-b1":
            item["readingOrder"] += 2
    assignments[1:1] = [
        {"blockId": "p1-author", "role": "author", "readingOrder": 2, "sectionId": None, "paragraphId": None, "hidden": False, "citations": [], "objectReferences": [], "referenceLabel": None, "confidence": .8, "warnings": ["Author and affiliation are merged."]},
        {"blockId": "p1-affiliation", "role": "affiliation", "readingOrder": 3, "sectionId": None, "paragraphId": None, "hidden": False, "citations": [], "objectReferences": [], "referenceLabel": None, "confidence": 1, "warnings": []},
    ]
    document = build_semantic_document(evidence, structure, visuals)
    assert document["frontMatter"]["authors"][0]["original"] == "Ada Lovelace"


def test_semantic_renderer_links_citations_and_objects(tmp_path: Path):
    document = build_semantic_document(*sample_semantic_inputs())
    document["sections"][0]["content"][1]["value"]["caption"] += (
        " https://example.org/caption."
    )
    units = list(iter_translatable_units(document))
    for unit in units:
        unit["japanese"] = unit["original"]
    paragraph = next(unit for unit in units if unit["kind"] == "paragraph")
    paragraph["original"] += (
        ' See "https://example.org/a-path?x=1&y=2" now.'
    )
    paragraph["japanese"] = paragraph["original"] + "（https://example.org/japanese）"
    paragraph["preservedTerms"] = ["https://example.org/term"]
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "figure-1.png").write_bytes(b"png")
    index = render_semantic_document(document, tmp_path, tmp_path / "html")
    text = index.read_text(encoding="utf-8")
    markdown = (tmp_path / "html" / "index.md").read_text(encoding="utf-8")
    assert 'href="#ref-1"' in text
    assert 'href="#visual-figure-1"' in text
    assert (
        '<a class="external" href="https://example.org/a-path?x=1&amp;y=2" '
        'rel="noopener noreferrer">https://example.org/a-path?x=1&amp;y=2</a>'
    ) in text
    assert "https://example.org/a-path?x=1&amp;y=2&amp;quot" not in text
    assert (
        '<a class="external" href="https://example.org/japanese" '
        'rel="noopener noreferrer">https://example.org/japanese</a>）'
    ) in text
    assert (
        '<span class="term"><a class="external" href="https://example.org/term" '
        'rel="noopener noreferrer">https://example.org/term</a></span>'
    ) in text
    assert "<figcaption>" in text
    assert '<a class="external" href="https://example.org/caption"' in text
    assert 'class="paper-section level-1"' in text
    assert "Page 1" not in text
    assert "# Paper Title" in markdown
    assert "## 1 Introduction" in markdown
    assert "![Figure 1](assets/figure-1.png)" in markdown
    assert '<a id="ref-1"></a>' in markdown
    assert "Page 1" not in markdown
    markdown_qa = json.loads(
        (tmp_path / "html" / "markdown-qa.json").read_text(encoding="utf-8")
    )
    assert markdown_qa == {
        "schemaVersion": 1,
        "status": "passed",
        "blocks": markdown_qa["blocks"],
        "anchors": markdown_qa["anchors"],
        "duplicateAnchors": [],
        "unresolvedInternalLinks": [],
        "missingLocalAssets": [],
    }


def test_semantic_translation_is_explicitly_sol_high(tmp_path: Path):
    command = _command(tmp_path, tmp_path / "schema.json")
    assert command[command.index("-m") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="high"' in command


def test_semantic_translation_rejects_missing_units():
    units = [{"id": "one"}, {"id": "two"}]
    result = {"translations": [{"blockId": "one", "japanese": "一", "preservedTerms": [], "warnings": []}]}
    try:
        _validate_result(units, result)
    except ValueError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("missing unit was accepted")


def test_semantic_translation_runs_chunks_in_parallel_and_records_metrics(tmp_path: Path, monkeypatch):
    document = build_semantic_document(*sample_semantic_inputs())
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def fake_run(command, input, **kwargs):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        payload = json.loads(input)
        result = {
            "translations": [
                {
                    "blockId": block["blockId"],
                    "japanese": block["text"],
                    "preservedTerms": [],
                    "warnings": [],
                }
                for block in payload["blocks"]
            ]
        }
        with lock:
            active -= 1
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr(semantic_translation.subprocess, "run", fake_run)
    metrics_path = tmp_path / "metrics.json"
    progress: list[int] = []
    semantic_translation.translate_semantic_document(
        document,
        tmp_path / "document.json",
        tmp_path,
        tmp_path / "cache",
        max_characters=1,
        max_workers=3,
        metrics_path=metrics_path,
        progress_callback=lambda current: progress.append(
            sum(bool(unit.get("japanese")) for unit in iter_translatable_units(current))
        ),
    )
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))["stages"]["semantic_translation"]
    assert maximum_active >= 2
    assert metrics["details"]["workers"] == 3
    assert metrics["details"]["modelCalls"] == 4
    assert metrics["details"]["translatedUnits"] == 4
    assert progress[0] == 0
    assert progress[-1] == 4
    assert progress == sorted(progress)


def test_structure_validation_rejects_missing_blocks():
    pages = [{"pageNumber": 1, "blocks": [{"blockId": "p1-b1"}, {"blockId": "p1-b2"}]}]
    result = {
        "pages": [
            {
                "pageNumber": 1,
                "blockAssignments": [{"blockId": "p1-b1", "readingOrder": 1}],
                "visualObjects": [],
            }
        ]
    }
    try:
        validate_structure_batch(pages, result)
    except ValueError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("missing block was accepted")


def test_structure_validation_accepts_previous_page_visual_anchor():
    pages = [{"pageNumber": 2, "blocks": [{"blockId": "p2-b1"}]}]
    result = {
        "pages": [
            {
                "pageNumber": 2,
                "blockAssignments": [{"blockId": "p2-b1", "readingOrder": 1}],
                "visualObjects": [
                    {
                        "objectId": "figure-2",
                        "bboxNormalized": [0.1, 0.1, 0.8, 0.6],
                        "captionBlockIds": [],
                        "insertAfterBlockId": "p1-b39",
                    }
                ],
            }
        ]
    }
    validate_structure_batch(pages, result, allowed_anchor_ids={"p1-b39"})


def test_deterministic_structure_hides_visual_text_and_keeps_caption(tmp_path: Path):
    evidence = {
        "version": 3,
        "sourceFile": "paper.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "drawingClusters": [[0.52, 0.2, 0.9, 0.5]],
                "imageRegions": [],
                "blocks": [
                    {
                        "blockId": "p1-b1",
                        "text": "A Useful Paper",
                        "bboxNormalized": [0.2, 0.05, 0.8, 0.08],
                        "fontSizeMax": 16,
                        "bold": True,
                    },
                    {
                        "blockId": "p1-b2",
                        "text": "1 Introduction",
                        "bboxNormalized": [0.1, 0.15, 0.35, 0.18],
                        "fontSizeMax": 12,
                        "bold": True,
                    },
                    {
                        "blockId": "p1-b3",
                        "text": "The body paragraph contains enough words to establish the normal body font for this synthetic paper fixture.",
                        "bboxNormalized": [0.1, 0.2, 0.45, 0.4],
                        "fontSizeMax": 10,
                        "bold": False,
                    },
                    {
                        "blockId": "p1-b4",
                        "text": "Embedded chart label",
                        "bboxNormalized": [0.6, 0.3, 0.75, 0.33],
                        "fontSizeMax": 5,
                        "bold": False,
                    },
                    {
                        "blockId": "p1-b5",
                        "text": "Figure 1: Original caption.",
                        "bboxNormalized": [0.52, 0.51, 0.9, 0.54],
                        "fontSizeMax": 9,
                        "bold": False,
                    },
                ],
            }
        ],
    }
    output = tmp_path / "structure.json"
    result = analyze_layout_deterministic(evidence, output)
    assignments = {
        value["blockId"]: value for value in result["pages"][0]["blockAssignments"]
    }
    assert assignments["p1-b2"]["role"] == "heading"
    assert assignments["p1-b4"]["role"] == "noise"
    assert assignments["p1-b4"]["hidden"] is True
    assert assignments["p1-b5"]["role"] == "caption"
    assert result["pages"][0]["visualObjects"][0]["label"] == "Figure 1"
    assert output.exists()


def test_deterministic_structure_recognizes_appendix_object_labels(tmp_path: Path):
    evidence = {
        "version": 4,
        "sourceFile": "appendix.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "drawingClusters": [[0.1, 0.18, 0.9, 0.46]],
                "imageRegions": [],
                "blocks": [
                    {
                        "blockId": "p1-b1",
                        "text": "Appendix B Additional Results",
                        "bboxNormalized": [0.1, 0.08, 0.5, 0.11],
                        "fontSizeMax": 12,
                        "bold": True,
                    },
                    {
                        "blockId": "p1-b2",
                        "text": "Embedded legend",
                        "bboxNormalized": [0.3, 0.25, 0.5, 0.28],
                        "fontSizeMax": 6,
                        "bold": False,
                    },
                    {
                        "blockId": "p1-b3",
                        "text": "Figure B.1: Additional experiment results.",
                        "bboxNormalized": [0.1, 0.48, 0.9, 0.51],
                        "fontSizeMax": 9,
                        "bold": False,
                    },
                    {
                        "blockId": "p1-b4",
                        "text": "As shown in Figure B.1, the result is stable.",
                        "bboxNormalized": [0.1, 0.55, 0.9, 0.59],
                        "fontSizeMax": 10,
                        "bold": False,
                    },
                ],
            }
        ],
    }
    result = analyze_layout_deterministic(evidence, tmp_path / "structure.json")
    visual = result["pages"][0]["visualObjects"][0]
    assignments = {
        value["blockId"]: value for value in result["pages"][0]["blockAssignments"]
    }
    assert visual["label"] == "Figure B.1"
    assert assignments["p1-b3"]["role"] == "caption"
    assert assignments["p1-b4"]["objectReferences"] == ["Figure B.1"]


def test_deterministic_structure_escalates_unresolved_caption(tmp_path: Path):
    evidence = {
        "version": 4,
        "sourceFile": "missing-visual.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "drawingClusters": [],
                "imageRegions": [],
                "blocks": [
                    {
                        "blockId": "p1-b1",
                        "text": "Figure C.2: Missing body.",
                        "bboxNormalized": [0.1, 0.5, 0.9, 0.53],
                        "fontSizeMax": 9,
                        "bold": False,
                    }
                ],
            }
        ],
    }
    result = analyze_layout_deterministic(evidence, tmp_path / "structure.json")
    assert result["analysis"]["uncertainPages"] == [1]
    assert any("Figure C.2" in warning for warning in result["warnings"])


def test_deterministic_structure_escalates_unlabelled_drawing_group(tmp_path: Path):
    evidence = {
        "version": 4,
        "sourceFile": "unlabelled.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "drawingClusters": [
                    [0.22, 0.42, 0.34, 0.44],
                    [0.11, 0.46, 0.46, 0.49],
                ],
                "imageRegions": [],
                "blocks": [
                    {
                        "blockId": "p1-b1",
                        "text": "A paragraph before an unlabelled diagram with enough prose to establish the body font size.",
                        "bboxNormalized": [0.1, 0.2, 0.46, 0.35],
                        "fontSizeMax": 10,
                        "bold": False,
                    }
                ],
            }
        ],
    }
    result = analyze_layout_deterministic(evidence, tmp_path / "structure.json")
    assert result["analysis"]["uncertainPages"] == [1]
    assert any("unassociated visual region" in warning for warning in result["warnings"])


def test_deterministic_structure_does_not_treat_list_or_numeric_cells_as_headings(tmp_path: Path):
    evidence = {
        "version": 4,
        "sourceFile": "numbered-content.pdf",
        "pageCount": 1,
        "pages": [
            {
                "pageNumber": 1,
                "drawingClusters": [],
                "imageRegions": [],
                "blocks": [
                    {
                        "blockId": "p1-b1",
                        "text": "1 Method",
                        "bboxNormalized": [0.1, 0.1, 0.4, 0.14],
                        "fontSizeMax": 12,
                        "bold": True,
                    },
                    {
                        "blockId": "p1-b2",
                        "text": "2. Stage progression monitoring",
                        "bboxNormalized": [0.1, 0.2, 0.6, 0.24],
                        "fontSizeMax": 10,
                        "bold": True,
                    },
                    {
                        "blockId": "p1-b3",
                        "text": "60.0 56.7",
                        "bboxNormalized": [0.1, 0.3, 0.3, 0.34],
                        "fontSizeMax": 11,
                        "bold": True,
                    },
                    {
                        "blockId": "p1-b4",
                        "text": "A sufficiently long body paragraph establishes the normal font used by this fixture.",
                        "bboxNormalized": [0.1, 0.4, 0.8, 0.48],
                        "fontSizeMax": 10,
                        "bold": False,
                    },
                ],
            }
        ],
    }
    result = analyze_layout_deterministic(evidence, tmp_path / "structure.json")
    assignments = {
        value["blockId"]: value for value in result["pages"][0]["blockAssignments"]
    }
    assert [section["number"] for section in result["sections"]] == ["1"]
    assert assignments["p1-b2"]["role"] != "heading"
    assert assignments["p1-b3"]["role"] != "heading"


def test_structure_evaluation_reports_role_and_visual_accuracy(tmp_path: Path):
    evidence, structure, _ = sample_semantic_inputs()
    candidate = json.loads(json.dumps(structure))
    candidate["pages"][0]["blockAssignments"][0]["role"] = "paragraph"
    metrics = evaluate_structure(structure, candidate)
    assert metrics["blocks"]["coverage"] == 1
    assert metrics["blocks"]["roleAccuracy"] == 7 / 8
    assert metrics["headings"]["f1"] == 1


def test_layout_extraction_splits_mixed_body_heading_and_equation_lines():
    def line(text, bbox, font="Times-Regular", flags=4):
        return {
            "bbox": bbox,
            "spans": [{"text": text, "bbox": bbox, "font": font, "size": 10.0, "flags": flags}],
        }

    raw = {
        "type": 0,
        "bbox": [10, 10, 190, 70],
        "lines": [
            line("Previous paragraph.", [10, 10, 100, 20]),
            line("3.2", [10, 25, 30, 35], "Times-Medium", 20),
            line("Threat Model", [40, 25, 100, 35], "Times-Medium", 20),
            line("x = y + z", [40, 40, 100, 48], "CMMI10", 6),
            line("Following prose.", [10, 55, 100, 65]),
        ],
    }
    segments = _segment_text_block(raw)
    assert [segment["lines"][0]["spans"][0]["text"] for segment in segments] == [
        "Previous paragraph.",
        "3.2",
        "x = y + z",
        "Following prose.",
    ]


def test_hybrid_review_selects_only_low_confidence_pages():
    baseline = {
        "pages": [{"pageNumber": 1}, {"pageNumber": 2}, {"pageNumber": 3}],
        "analysis": {"pageConfidence": {"1": 0.92, "2": 0.52, "3": 0.68}},
    }
    assert select_review_pages(baseline, confidence_threshold=0.7) == [2, 3]
    assert select_review_pages(baseline, explicit_pages=[3, 9]) == [3]
    assert select_review_pages(baseline, confidence_threshold=0.7, max_review_pages=1) == [2]


def test_hybrid_cache_ignores_confidence_only_changes():
    payload = {
        "pages": [{"pageNumber": 1, "blocks": [{"blockId": "p1-b1", "text": "Body"}]}],
        "deterministicProposal": {
            "pageNumber": 1,
            "blockAssignments": [
                {
                    "blockId": "p1-b1",
                    "role": "paragraph",
                    "sectionId": "sec-1",
                    "paragraphId": "para-1",
                    "hidden": False,
                    "confidence": 0.6,
                }
            ],
            "visualObjects": [],
        },
        "previousContext": {"sections": [], "tailAssignments": []},
    }
    before = _stable_cache_payload(payload)
    payload["deterministicProposal"]["blockAssignments"][0]["confidence"] = 0.95
    assert _stable_cache_payload(payload) == before


def test_hybrid_merge_removes_resolved_deterministic_warnings():
    assignment = {
        "blockId": "p2-b1",
        "role": "paragraph",
        "readingOrder": 1,
        "sectionId": "sec-test",
        "paragraphId": "para-1",
        "continuesFrom": None,
        "hidden": False,
        "citations": [],
        "objectReferences": [],
        "referenceLabel": None,
        "confidence": 0.95,
        "warnings": [],
    }
    baseline = {
        "pages": [{"pageNumber": 2, "blockAssignments": [assignment], "visualObjects": []}],
        "sections": [],
        "warnings": [
            "Page 2: unresolved original visual objects: Table A.1",
            "Deterministic analysis marked pages requiring semantic review: 2",
        ],
        "analysis": {"pageConfidence": {"2": 0.0}},
    }
    reviewed = {
        2: {
            "pages": [{"pageNumber": 2, "blockAssignments": [assignment], "visualObjects": []}],
            "sections": [],
            "warnings": ["A real semantic warning remains."],
        }
    }
    result = _merge_reviewed_pages(baseline, reviewed, "gpt-5.6-sol", "high")
    assert result["analysis"]["unresolvedPages"] == []
    assert result["warnings"] == ["Page 2: A real semantic warning remains."]


def test_hybrid_merge_orders_sections_by_page_reading_order():
    def assignment(block_id: str, reading_order: int):
        return {
            "blockId": block_id,
            "role": "heading",
            "readingOrder": reading_order,
            "sectionId": f"sec-{block_id}",
            "paragraphId": None,
            "continuesFrom": None,
            "hidden": False,
            "citations": [],
            "objectReferences": [],
            "referenceLabel": None,
            "confidence": 0.95,
            "warnings": [],
        }

    baseline = {
        "pages": [
            {
                "pageNumber": 2,
                "blockAssignments": [assignment("later", 8), assignment("earlier", 3)],
                "visualObjects": [],
            }
        ],
        "sections": [
            {
                "sectionId": "sec-later",
                "number": "3",
                "titleBlockId": "later",
                "level": 1,
                "parentSectionId": None,
                "pageStart": 2,
            },
            {
                "sectionId": "sec-earlier",
                "number": "2.1",
                "titleBlockId": "earlier",
                "level": 2,
                "parentSectionId": None,
                "pageStart": 2,
            },
        ],
        "warnings": [],
        "analysis": {"pageConfidence": {"2": 0.95}},
    }
    result = _merge_reviewed_pages(baseline, {}, "gpt-5.6-sol", "high")
    assert [section["sectionId"] for section in result["sections"]] == [
        "sec-earlier",
        "sec-later",
    ]


def _xref_unit(
    unit_id: str,
    position: float,
    section_id: str,
    page: int,
    references: list[str],
    original: str = "",
) -> dict:
    return {
        "id": unit_id,
        "kind": "paragraph",
        "endPosition": position,
        "sectionId": section_id,
        "pages": [page],
        "objectReferences": references,
        "original": original,
    }


def test_visual_xref_anchor_prefers_exact_reference_on_the_visual_page() -> None:
    units = [
        _xref_unit("early", 155, "sec-results", 6, ["Fig. 4"]),
        _xref_unit("discussion-a", 247, "sec-discussion", 7, ["Figure 4"]),
        _xref_unit("discussion-b", 248, "sec-discussion", 7, ["Figure 4"]),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Figure 4",
        160.1,
        {"sec-results", "sec-discussion"},
        visual_page=7,
    )

    assert anchor is not None
    assert anchor["id"] == "discussion-b"


def test_visual_xref_anchor_does_not_drift_to_a_later_page_remention() -> None:
    units = [
        _xref_unit("primary", 236, "sec-primary", 5, ["Table 1"]),
        _xref_unit("later", 244, "sec-later", 6, ["Table 1"]),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 1",
        227.1,
        {"sec-primary", "sec-later"},
        visual_page=5,
    )

    assert anchor is not None
    assert anchor["id"] == "primary"


def test_visual_xref_anchor_finishes_a_tight_future_reference_cluster() -> None:
    units = [
        _xref_unit("top", 95, "sec-top", 6, ["Table 2"]),
        _xref_unit("bottom", 97, "sec-bottom", 6, ["Table 2"]),
        _xref_unit("later-reminder", 110, "sec-later", 6, ["Table 2"]),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 2",
        84.1,
        {"sec-top", "sec-bottom", "sec-later"},
        visual_page=6,
    )

    assert anchor is not None
    assert anchor["id"] == "bottom"


def test_visual_xref_anchor_ignores_a_remote_retrospective_mention() -> None:
    units = [
        _xref_unit("remote", 137, "sec-retrospective", 10, ["Table 1"]),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 1",
        24.1,
        {"sec-source", "sec-retrospective"},
        visual_page=2,
    )

    assert anchor is None


def test_visual_xref_anchor_allows_a_two_page_forward_appendix_float() -> None:
    units = [
        _xref_unit("forward", 180, "sec-appendix-d2", 18, ["Table 10"]),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 10",
        200.1,
        {"sec-appendix-d2", "sec-local"},
        visual_page=20,
    )

    assert anchor is not None
    assert anchor["id"] == "forward"


def test_visual_xref_anchor_rejects_a_two_page_later_comparison() -> None:
    units = [
        _xref_unit("later-comparison", 220, "sec-comparison", 10, ["Table 5"]),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 5",
        180.1,
        {"sec-source", "sec-comparison"},
        visual_page=8,
    )

    assert anchor is None


def test_visual_xref_anchor_prefers_a_defining_reference_over_a_remention() -> None:
    units = [
        _xref_unit(
            "configuration-intro",
            28,
            "sec-configurations",
            2,
            ["Table 1"],
            "The ConvNet configurations are outlined in Table 1, one per column.",
        ),
        _xref_unit(
            "discussion-remention",
            37,
            "sec-discussion",
            3,
            ["Table 1"],
            "We use a 1 x 1 layer (configuration C, Table 1).",
        ),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 1",
        30.1,
        {"sec-configurations", "sec-discussion"},
        visual_page=3,
    )

    assert anchor is not None
    assert anchor["id"] == "configuration-intro"


def test_visual_xref_anchor_allows_a_three_page_defining_appendix_reference() -> None:
    units = [
        _xref_unit(
            "appendix-intro",
            615,
            "sec-appendix-j",
            17,
            ["Table 7"],
            "Table 7 shows the results evaluated on OR-Bench-80K.",
        ),
        _xref_unit(
            "earlier-reminder",
            317,
            "sec-results",
            7,
            ["Table 7"],
            "The results in Table 7 are a better indicator.",
        ),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 7",
        880.1,
        {"sec-appendix-j", "sec-results", "sec-appendix-o"},
        visual_page=20,
    )

    assert anchor is not None
    assert anchor["id"] == "appendix-intro"


def test_table_result_discussion_does_not_replace_its_preceding_intro() -> None:
    units = [
        _xref_unit(
            "comparison-intro",
            287,
            "sec-results",
            6,
            ["Table 3"],
            "In Table 3 we compare three shortcut options.",
        ),
        _xref_unit(
            "result-discussion",
            301,
            "sec-results",
            6,
            ["Table 3"],
            "Table 3 shows that all three options beat the plain counterpart.",
        ),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 3",
        290.1,
        {"sec-results"},
        visual_page=6,
    )

    assert anchor is not None
    assert anchor["id"] == "comparison-intro"


def test_remote_result_sentence_does_not_erase_a_local_table_owner() -> None:
    units = [
        _xref_unit(
            "local-owner",
            416,
            "sec-object-detection",
            8,
            ["Table 7"],
            "Our method has good generalization; Table 7 and 8 show detection results.",
        ),
        _xref_unit(
            "later-result",
            454,
            "sec-pascal",
            10,
            ["Table 7"],
            "Table 7 shows the PASCAL results.",
        ),
    ]

    anchor = _select_visual_reference_anchor(
        units,
        "Table 7",
        412.1,
        {"sec-object-detection", "sec-pascal"},
        visual_page=8,
    )

    assert anchor is not None
    assert anchor["id"] == "local-owner"


def test_section_descendant_check_is_strict_and_transitive() -> None:
    sections = {
        "attention": {"parentSectionId": "architecture"},
        "scaled": {"parentSectionId": "attention"},
        "architecture": {"parentSectionId": None},
    }

    assert _is_descendant_section("scaled", "attention", sections)
    assert _is_descendant_section("scaled", "architecture", sections)
    assert not _is_descendant_section("attention", "attention", sections)
    assert not _is_descendant_section("architecture", "scaled", sections)


def test_only_paired_figures_preserve_a_physical_parent_section() -> None:
    sections = {
        "attention": {"parentSectionId": "architecture"},
        "scaled": {"parentSectionId": "attention"},
        "architecture": {"parentSectionId": None},
    }
    child_anchor = {"sectionId": "scaled", "endPosition": 67}
    composite = {"kind": "figure"}

    assert _should_preserve_paired_figure_parent(
        composite,
        "Figure 2: (left) Scaled attention. (right) Multi-head attention.",
        child_anchor,
        65.1,
        "attention",
        sections,
    )
    assert not _should_preserve_paired_figure_parent(
        {"kind": "table"},
        "Table 2: left and right results.",
        child_anchor,
        65.1,
        "attention",
        sections,
    )
    assert not _should_preserve_paired_figure_parent(
        composite,
        "Figure 5: Classification results.",
        child_anchor,
        65.1,
        "attention",
        sections,
    )
