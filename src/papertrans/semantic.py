from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


TRANSLATABLE_ROLES = {"abstract", "paragraph", "list_item", "footnote"}
FRONT_ROLES = {"title", "author", "affiliation", "metadata"}
NON_TEXT_ROLES = {"caption", "equation", "algorithm"}


def _join_source_parts(parts: list[str]) -> str:
    """Join geometry blocks without inventing PDF page/column boundaries."""
    result = ""
    for raw in parts:
        value = re.sub(r"\s+", " ", raw).strip()
        if not value:
            continue
        if not result:
            result = value
            continue
        if result.endswith("-") and value[:1].islower():
            result = result[:-1] + value
        elif result.count("[") > result.count("]") and re.match(r"^\d", value):
            result += value
        elif value and all(
            character in "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾" for character in value
        ):
            result += value
        elif value[:1] in ".,;:!?)]}" or result[-1:] in "([{/—":
            result += value
        else:
            result += " " + value
    return result


def _raw_block_map(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        block["blockId"]: {**block, "pageNumber": page["pageNumber"]}
        for page in evidence["pages"]
        for block in page["blocks"]
    }


def _ordered_assignments(structure: dict[str, Any]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for page in sorted(structure["pages"], key=lambda value: value["pageNumber"]):
        for assignment in sorted(page["blockAssignments"], key=lambda value: value["readingOrder"]):
            ordered.append({**assignment, "pageNumber": page["pageNumber"]})
    return ordered


def _unit_from_group(
    unit_id: str,
    assignments: list[dict[str, Any]],
    raw_blocks: dict[str, dict[str, Any]],
    positions: dict[str, int],
) -> dict[str, Any]:
    assignments.sort(key=lambda value: positions[value["blockId"]])
    block_ids = [value["blockId"] for value in assignments]
    role = assignments[0]["role"]
    citations: list[str] = []
    object_references: list[str] = []
    warnings: list[str] = []
    for assignment in assignments:
        for field, destination in (
            ("citations", citations),
            ("objectReferences", object_references),
            ("warnings", warnings),
        ):
            for value in assignment.get(field, []):
                text = str(value)
                if text and text not in destination:
                    destination.append(text)
    pages = list(dict.fromkeys(int(raw_blocks[block_id]["pageNumber"]) for block_id in block_ids))
    original = _join_source_parts([raw_blocks[block_id]["text"] for block_id in block_ids])
    if role == "list_item":
        original = re.sub(r"^[•▪◦‣]\s*", "", original)
    return {
        "id": unit_id,
        "kind": role,
        "sectionId": assignments[0].get("sectionId"),
        "original": original,
        "japanese": "",
        "sourceBlockIds": block_ids,
        "pages": pages,
        "citations": citations,
        "objectReferences": object_references,
        "referenceLabel": assignments[0].get("referenceLabel"),
        "preservedTerms": [],
        "warnings": warnings,
        "confidence": min(float(value.get("confidence", 0)) for value in assignments),
        "position": min(positions[block_id] for block_id in block_ids),
        "endPosition": max(positions[block_id] for block_id in block_ids),
    }


def build_semantic_document(
    evidence: dict[str, Any],
    structure: dict[str, Any],
    visual_objects: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_blocks = _raw_block_map(evidence)
    assignments = _ordered_assignments(structure)
    positions = {value["blockId"]: index for index, value in enumerate(assignments, start=1)}
    assignment_by_id = {value["blockId"]: value for value in assignments}

    front: dict[str, list[dict[str, Any]]] = defaultdict(list)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        if assignment.get("hidden"):
            continue
        role = assignment["role"]
        if role in FRONT_ROLES:
            front[role].append(
                {
                    "blockId": assignment["blockId"],
                    "original": raw_blocks[assignment["blockId"]]["text"].strip(),
                    "page": assignment["pageNumber"],
                    "warnings": assignment.get("warnings", []),
                }
            )
            continue
        if role == "heading" or role in NON_TEXT_ROLES:
            continue
        if role not in TRANSLATABLE_ROLES and role != "reference":
            continue
        unit_id = assignment.get("paragraphId") or assignment["blockId"]
        grouped[str(unit_id)].append(assignment)

    units = {
        unit_id: _unit_from_group(unit_id, values, raw_blocks, positions)
        for unit_id, values in grouped.items()
    }

    sections: list[dict[str, Any]] = []
    section_by_id: dict[str, dict[str, Any]] = {}
    split_intro_units: list[dict[str, Any]] = []
    for value in structure["sections"]:
        title_block_id = value["titleBlockId"]
        title_assignment = assignment_by_id[title_block_id]
        raw_title = raw_blocks[title_block_id]["text"].strip()
        display_title = raw_title
        if value.get("number"):
            escaped_number = re.escape(str(value["number"]))
            number_match = re.match(
                rf"^\s*{escaped_number}[.)]?(?:\s+|$)", raw_title
            )
            mixed_heading = any(
                "merges the introductory prose" in str(warning)
                for warning in title_assignment.get("warnings", [])
            )
            if number_match is None and mixed_heading:
                number_match = re.search(
                    rf"(?:^|\s){escaped_number}[.)]?\s+", raw_title
                )
            if number_match:
                prefix = raw_title[: number_match.start()].strip()
                display_title = raw_title[number_match.end() :].strip()
                if prefix and value.get("parentSectionId"):
                    split_intro_units.append(
                        {
                            "id": f"intro-{value['sectionId']}",
                            "kind": "paragraph",
                            "sectionId": value["parentSectionId"],
                            "original": prefix,
                            "japanese": "",
                            "sourceBlockIds": [title_block_id],
                            "pages": [raw_blocks[title_block_id]["pageNumber"]],
                            "citations": [],
                            "objectReferences": [],
                            "referenceLabel": None,
                            "preservedTerms": [],
                            "warnings": ["A mixed PDF block was split at a verified numbered-heading boundary."],
                            "confidence": float(title_assignment.get("confidence", 0)),
                            "position": positions[title_block_id] - 0.1,
                            "endPosition": positions[title_block_id] - 0.1,
                        }
                    )
        title_unit = {
            "id": f"heading-{value['sectionId']}",
            "kind": "heading",
            "sectionId": value["sectionId"],
            "original": display_title,
            "sourceHeading": raw_title,
            "japanese": "",
            "sourceBlockIds": [title_block_id],
            "pages": [raw_blocks[title_block_id]["pageNumber"]],
            "citations": [],
            "objectReferences": [],
            "referenceLabel": None,
            "preservedTerms": [],
            "warnings": [
                warning
                for warning in title_assignment.get("warnings", [])
                if "merges the introductory prose" not in str(warning)
            ],
            "confidence": float(title_assignment.get("confidence", 0)),
            "position": positions[title_block_id],
            "endPosition": positions[title_block_id],
        }
        section = {
            "id": value["sectionId"],
            "number": value.get("number"),
            "level": int(value["level"]),
            "parentSectionId": value.get("parentSectionId"),
            "pageStart": int(value["pageStart"]),
            "title": title_unit,
            "content": [],
        }
        sections.append(section)
        section_by_id[section["id"]] = section

    caption_text: dict[str, str] = {}
    for visual in visual_objects:
        override = visual.get("captionTextOverride")
        caption_text[visual["objectId"]] = (
            str(override).strip()
            if isinstance(override, str) and override.strip()
            else _join_source_parts(
                [
                    raw_blocks[block_id]["text"]
                    for block_id in visual.get("captionBlockIds", [])
                ]
            )
        )

    rendered_visuals: list[dict[str, Any]] = []
    for visual in visual_objects:
        anchor_id = visual.get("insertAfterBlockId")
        if not anchor_id:
            caption_ids = visual.get("captionBlockIds", [])
            if caption_ids:
                anchor_id = caption_ids[-1]
            else:
                page_assignments = [
                    value for value in assignments if value["pageNumber"] == visual["pageNumber"]
                ]
                anchor_id = page_assignments[0]["blockId"]
        anchor_assignment = assignment_by_id[anchor_id]
        anchor_unit_id = anchor_assignment.get("paragraphId") or anchor_id
        anchor_unit = units.get(str(anchor_unit_id))
        position = (anchor_unit["endPosition"] if anchor_unit else positions[anchor_id]) + 0.1
        rendered_visuals.append(
            {
                **visual,
                "caption": caption_text[visual["objectId"]],
                "sectionId": anchor_assignment.get("sectionId"),
                "position": position,
            }
        )

    unsectioned_units = [
        unit for unit in units.values() if unit.get("sectionId") not in section_by_id
    ]
    unsectioned_visuals = [
        visual
        for visual in rendered_visuals
        if visual.get("sectionId") not in section_by_id
    ]
    if unsectioned_units or unsectioned_visuals:
        base_id = "sec-preamble" if sections else "sec-document"
        synthetic_id = base_id
        suffix = 2
        while synthetic_id in section_by_id:
            synthetic_id = f"{base_id}-{suffix}"
            suffix += 1
        title_text = "Preamble" if sections else "Document"
        pages = [
            int(page)
            for unit in unsectioned_units
            for page in unit.get("pages", [])
        ] + [
            int(visual.get("pageNumber", 1)) for visual in unsectioned_visuals
        ]
        synthetic_section = {
            "id": synthetic_id,
            "number": None,
            "level": 1,
            "parentSectionId": None,
            "pageStart": min(pages, default=1),
            "title": {
                "id": f"heading-{synthetic_id}",
                "kind": "heading",
                "sectionId": synthetic_id,
                "original": title_text,
                "sourceHeading": title_text,
                "japanese": "",
                "sourceBlockIds": [],
                "pages": [min(pages, default=1)],
                "citations": [],
                "objectReferences": [],
                "referenceLabel": None,
                "preservedTerms": [],
                "warnings": [
                    "PaperTrans created a synthetic section so unheaded source content is not dropped."
                ],
                "confidence": 1.0,
                "position": 0,
                "endPosition": 0,
            },
            "content": [],
        }
        sections.insert(0, synthetic_section)
        section_by_id[synthetic_id] = synthetic_section
        for unit in unsectioned_units:
            unit["sectionId"] = synthetic_id
        for visual in unsectioned_visuals:
            visual["sectionId"] = synthetic_id

    for unit in units.values():
        section = section_by_id.get(unit["sectionId"])
        if section:
            section["content"].append({"type": "unit", "position": unit["position"], "value": unit})
    for unit in split_intro_units:
        section = section_by_id.get(unit["sectionId"])
        if section:
            section["content"].append({"type": "unit", "position": unit["position"], "value": unit})
    for visual in rendered_visuals:
        section = section_by_id.get(visual["sectionId"])
        if section:
            section["content"].append({"type": "visual", "position": visual["position"], "value": visual})
    for section in sections:
        section["content"].sort(key=lambda value: (value["position"], value["type"] != "visual"))

    title_entries = front.get("title", [])
    title = (
        _join_source_parts([entry["original"] for entry in title_entries])
        if title_entries
        else Path(evidence["sourceFile"]).stem
    )
    known_affiliations = sorted(
        {entry["original"] for entry in front.get("affiliation", []) if entry["original"]},
        key=len,
        reverse=True,
    )
    for author in front.get("author", []):
        if not any("merged" in str(warning).lower() for warning in author.get("warnings", [])):
            continue
        for affiliation in known_affiliations:
            if author["original"].endswith(affiliation) and author["original"] != affiliation:
                author["original"] = author["original"][: -len(affiliation)].strip()
                author["warnings"] = [
                    "Author and affiliation were separated using an exact affiliation match."
                ]
                break
    title_unit = {
        "id": "paper-title",
        "kind": "title",
        "original": title,
        "japanese": "",
        "sourceBlockIds": [entry["blockId"] for entry in title_entries],
        "pages": [1],
        "citations": [],
        "objectReferences": [],
        "preservedTerms": [],
        "warnings": [
            warning for entry in title_entries for warning in entry.get("warnings", [])
        ],
    }
    return {
        "version": 3,
        "sourceFile": evidence["sourceFile"],
        "pageCount": len(structure["pages"]),
        "sourcePageCount": evidence["pageCount"],
        "partial": len(structure["pages"]) < int(evidence["pageCount"]),
        "status": "structured",
        "model": {
            "structure": structure.get("model", {}).get("name", "gpt-5.6-sol"),
            "translation": None,
            "reasoningEffort": structure.get("model", {}).get("reasoningEffort", "high"),
        },
        "title": title_unit,
        "frontMatter": {
            "authors": front.get("author", []),
            "affiliations": front.get("affiliation", []),
            "metadata": front.get("metadata", []),
        },
        "sections": sections,
        "visualObjects": rendered_visuals,
        "warnings": [
            warning
            for warning in structure.get("warnings", [])
            if not (
                ("Only pages" in str(warning) and "were supplied" in str(warning))
                or ("combines Section" in str(warning) and "heading in one indivisible" in str(warning))
            )
        ],
        "glossary": [],
    }


def merge_semantic_translations(document: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    previous_units = {unit["id"]: unit for unit in iter_translatable_units(previous)}
    previous_by_original: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for previous_unit in iter_translatable_units(previous):
        previous_by_original[str(previous_unit.get("original", ""))].append(previous_unit)
    copied = 0
    for unit in iter_translatable_units(document):
        old = previous_units.get(unit["id"])
        if not old or old.get("original") != unit.get("original"):
            exact_matches = previous_by_original.get(str(unit.get("original", "")), [])
            old = exact_matches[0] if len(exact_matches) == 1 else None
        if not old or old.get("original") != unit.get("original") or not old.get("japanese", "").strip():
            continue
        unit["japanese"] = old["japanese"]
        unit["preservedTerms"] = list(old.get("preservedTerms", []))
        for warning in old.get("warnings", []):
            if warning not in unit["warnings"]:
                unit["warnings"].append(warning)
        copied += 1
    total = sum(1 for _ in iter_translatable_units(document))
    document["model"]["translation"] = previous.get("model", {}).get("translation")
    document["status"] = previous.get("status", "structured") if copied == total else "structured"
    return document


def save_semantic_document(document: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def load_semantic_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_translatable_units(document: dict[str, Any]):
    yield document["title"]
    for section in document["sections"]:
        yield section["title"]
        for item in section["content"]:
            if item["type"] == "unit" and item["value"]["kind"] in TRANSLATABLE_ROLES:
                yield item["value"]
