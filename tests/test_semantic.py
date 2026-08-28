import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import papertrans.semantic_translate as semantic_translation
from papertrans.deterministic_structure import analyze_layout_deterministic, evaluate_structure
from papertrans.semantic import build_semantic_document, iter_translatable_units
from papertrans.semantic_render import render_semantic_document
from papertrans.semantic_translate import _command, _validate_result
from papertrans.structure import validate_structure_batch


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
    units = list(iter_translatable_units(document))
    for unit in units:
        unit["japanese"] = unit["original"]
    index = render_semantic_document(document, tmp_path, tmp_path / "html")
    text = index.read_text(encoding="utf-8")
    assert 'href="#ref-1"' in text
    assert 'href="#visual-figure-1"' in text
    assert 'class="paper-section level-1"' in text
    assert "Page 1" not in text


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
    semantic_translation.translate_semantic_document(
        document,
        tmp_path / "document.json",
        tmp_path,
        tmp_path / "cache",
        max_characters=1,
        max_workers=3,
        metrics_path=metrics_path,
    )
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))["stages"]["semantic_translation"]
    assert maximum_active >= 2
    assert metrics["details"]["workers"] == 3
    assert metrics["details"]["modelCalls"] == 4
    assert metrics["details"]["translatedUnits"] == 4


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


def test_structure_evaluation_reports_role_and_visual_accuracy(tmp_path: Path):
    evidence, structure, _ = sample_semantic_inputs()
    candidate = json.loads(json.dumps(structure))
    candidate["pages"][0]["blockAssignments"][0]["role"] = "paragraph"
    metrics = evaluate_structure(structure, candidate)
    assert metrics["blocks"]["coverage"] == 1
    assert metrics["blocks"]["roleAccuracy"] == 7 / 8
    assert metrics["headings"]["f1"] == 1
