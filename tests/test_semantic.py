import json
from pathlib import Path

from papertrans.semantic import build_semantic_document, iter_translatable_units
from papertrans.semantic_render import render_semantic_document
from papertrans.semantic_translate import _command, _validate_result


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
