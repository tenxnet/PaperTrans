from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


TRANSLATABLE_ROLES = {"abstract", "paragraph", "list_item", "footnote"}
FRONT_ROLES = {"title", "author", "affiliation", "metadata"}
NON_TEXT_ROLES = {"caption", "equation", "algorithm"}
PRESERVED_TEXT_ROLES = {"verbatim"}


def _canonical_object_reference(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    match = re.fullmatch(
        r"(Figures?|Figs?\.?|Tables?|Algorithms?|Equations?|Eqs?\.?)\s*"
        r"\(?((?:[A-Z]\.)?\d+(?:[.\-][A-Za-z0-9]+)*)\)?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return normalized.casefold()
    raw_kind = match.group(1).casefold().rstrip(".")
    if raw_kind.startswith("fig"):
        kind = "figure"
    elif raw_kind.startswith("table"):
        kind = "table"
    elif raw_kind.startswith("algorithm"):
        kind = "algorithm"
    else:
        kind = "equation"
    return f"{kind} {match.group(2).casefold()}"


def _is_references_section(section: dict[str, Any] | None) -> bool:
    if not section:
        return False
    title = section.get("title", {})
    values = (
        str(title.get("original", "")),
        str(title.get("sourceHeading", "")),
    )
    return any(
        re.fullmatch(r"\s*(?:\d+[.)]?\s*)?references\s*", value, re.IGNORECASE)
        for value in values
    )


def _visual_reference_introduction_score(
    unit: dict[str, Any], label: str
) -> int:
    """Rank prose that introduces a visual above incidental rementions."""

    text = re.sub(r"\s+", " ", str(unit.get("original", ""))).strip()
    canonical = _canonical_object_reference(label)
    match = re.fullmatch(
        r"(figure|table|algorithm|equation)\s+(.+)", canonical, re.IGNORECASE
    )
    if not text or match is None:
        return 0
    kind, number = match.groups()
    kind_pattern = {
        "figure": r"(?:Figure|Fig\.)",
        "table": r"Table",
        "algorithm": r"Algorithm",
        "equation": r"(?:Equation|Eq\.)",
    }[kind.casefold()]
    label_pattern = (
        rf"(?:appendix\s+)?{kind_pattern}\s*\(?{re.escape(number)}\)?"
    )
    if re.search(
        rf"(?:^|[.!?]\s+){label_pattern}\s+"
        r"(?:shows?|presents?|summari[sz]es?|reports?|lists?|provides?|"
        r"compares?|contains?|details?|illustrates?|depicts?|outlines?)\b",
        text,
        re.IGNORECASE,
    ):
        return 4
    if re.search(
        r"\b(?:outlined|detailed)\s+(?:directly\s+)?(?:in|by)\s+"
        rf"{label_pattern}\b",
        text,
        re.IGNORECASE,
    ):
        return 5
    if re.search(
        r"\b(?:shown|presented|summari[sz]ed|reported|listed|provided|"
        r"compared|illustrated|depicted)\s+"
        rf"(?:directly\s+)?(?:in|by)\s+{label_pattern}\b",
        text,
        re.IGNORECASE,
    ):
        return 3
    if re.search(
        rf"\b(?:as\s+)?(?:shown|seen|reported|listed|depicted)\s+in\s+"
        rf"{label_pattern}\b",
        text,
        re.IGNORECASE,
    ):
        return 2
    return 1


def _is_descendant_section(
    section_id: str | None,
    ancestor_id: str | None,
    section_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Return whether section_id is strictly below ancestor_id."""

    current = str(section_id or "")
    ancestor = str(ancestor_id or "")
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        parent = str(section_by_id.get(current, {}).get("parentSectionId") or "")
        if parent == ancestor and ancestor:
            return True
        current = parent
    return False


def _should_preserve_paired_figure_parent(
    visual: dict[str, Any],
    caption: str,
    semantic_anchor: dict[str, Any] | None,
    physical_position: float,
    physical_section_id: str | None,
    section_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Keep a left/right composite with the parent that owns both panels."""

    return bool(
        semantic_anchor is not None
        and float(semantic_anchor["endPosition"]) >= physical_position
        and visual.get("kind") == "figure"
        and re.search(r"\bleft\b", caption, re.IGNORECASE)
        and re.search(r"\bright\b", caption, re.IGNORECASE)
        and _is_descendant_section(
            semantic_anchor.get("sectionId"),
            physical_section_id,
            section_by_id,
        )
    )


def _select_visual_reference_anchor(
    units: list[dict[str, Any]],
    label: str,
    physical_position: float,
    section_ids: set[str],
    visual_page: int | None = None,
) -> dict[str, Any] | None:
    """Choose a close exact xref, including a float placed before its prose."""

    canonical_label = _canonical_object_reference(label)
    candidates = [
        unit
        for unit in units
        if unit.get("kind") in TRANSLATABLE_ROLES
        and unit.get("sectionId") in section_ids
        and any(
            _canonical_object_reference(str(reference)) == canonical_label
            for reference in unit.get("objectReferences", [])
        )
    ]
    if not candidates:
        return None
    introduction_scores = {
        id(unit): _visual_reference_introduction_score(unit, label)
        for unit in candidates
    }
    best_introduction_score = max(introduction_scores.values(), default=0)
    if best_introduction_score >= 5:
        candidates = [
            unit
            for unit in candidates
            if introduction_scores[id(unit)] == best_introduction_score
        ]
    if visual_page is not None:
        page_distances = {
            id(unit): min(
                (abs(int(page) - visual_page) for page in unit.get("pages", [])),
                default=10**9,
            )
            for unit in candidates
        }
        closest_page_distance = min(page_distances.values())
        # A remote mention must not pull a visual out of the section that owns
        # its source-page caption.  We still allow a two-page *preceding*
        # reference because long appendix tables commonly float forward by two
        # pages; a following mention at that distance is retrospective.
        if closest_page_distance > 2:
            if best_introduction_score < 4 or not any(
                float(unit["endPosition"]) < physical_position
                for unit in candidates
                if page_distances[id(unit)] == closest_page_distance
            ):
                return None
        if closest_page_distance == 2 and not any(
            float(unit["endPosition"]) < physical_position
            for unit in candidates
            if page_distances[id(unit)] == closest_page_distance
        ):
            return None
        candidates = [
            unit
            for unit in candidates
            if page_distances[id(unit)] == closest_page_distance
        ]
    preceding = [
        unit
        for unit in candidates
        if float(unit["endPosition"]) < physical_position
    ]
    following = [
        unit
        for unit in candidates
        if float(unit["endPosition"]) >= physical_position
    ]
    if canonical_label.startswith("figure ") and not preceding:
        defining_following = [
            unit
            for unit in following
            if introduction_scores.get(id(unit), 0) >= 4
        ]
        if defining_following:
            following = defining_following
    latest_preceding = (
        max(preceding, key=lambda unit: float(unit["endPosition"]))
        if preceding
        else None
    )
    first_following = (
        min(following, key=lambda unit: float(unit["endPosition"]))
        if following
        else None
    )
    if latest_preceding is not None and (
        first_following is None
        or physical_position - float(latest_preceding["endPosition"])
        <= float(first_following["endPosition"]) - physical_position + 4.0
    ):
        return latest_preceding
    if first_following is None:
        return latest_preceding
    first_position = float(first_following["endPosition"])
    local_following_cluster = [
        unit
        for unit in following
        if float(unit["endPosition"]) <= first_position + 3.0
    ]
    return max(
        local_following_cluster, key=lambda unit: float(unit["endPosition"])
    )


def _straight_quote_delimiter_count(text: str, quote: str) -> int:
    return sum(
        1
        for index, character in enumerate(text)
        if character == quote
        and not (
            index > 0
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        )
    )


def _ends_with_unmatched_opening_quote(text: str) -> bool:
    if text.endswith(("'", '"')):
        return (
            (len(text) == 1 or not text[-2].isalnum())
            and _straight_quote_delimiter_count(text, text[-1]) % 2 == 1
        )
    if text.endswith("‘"):
        return text.count("‘") > text.count("’")
    if text.endswith("“"):
        return text.count("“") > text.count("”")
    return False


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
        elif (
            (
                value in {"'", '"'}
                and _straight_quote_delimiter_count(result, value) % 2 == 1
            )
            or (value == "’" and result.count("‘") > result.count("’"))
            or (value == "”" and result.count("“") > result.count("”"))
        ):
            result += value
        elif (
            value[:1] in ".,;:!?)]}"
            or result[-1:] in "([{/—"
            or _ends_with_unmatched_opening_quote(result)
        ):
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
    external_links: list[str] = []
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
        for value in (
            list(assignment.get("externalLinks", []))
            + list(raw_blocks[assignment["blockId"]].get("externalLinks", []))
        ):
            text = str(value)
            if text and text not in external_links:
                external_links.append(text)
    pages = list(dict.fromkeys(int(raw_blocks[block_id]["pageNumber"]) for block_id in block_ids))
    source_parts = [str(raw_blocks[block_id]["text"]) for block_id in block_ids]
    original = (
        "\n".join(source_parts)
        if role in PRESERVED_TEXT_ROLES
        else _join_source_parts(source_parts)
    )
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
        "externalLinks": external_links,
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
                    "externalLinks": list(
                        dict.fromkeys(
                            str(value)
                            for value in (
                                list(assignment.get("externalLinks", []))
                                + list(
                                    raw_blocks[assignment["blockId"]].get(
                                        "externalLinks", []
                                    )
                                )
                            )
                            if str(value)
                        )
                    ),
                }
            )
            continue
        if role == "heading" or role in NON_TEXT_ROLES:
            continue
        if (
            role not in TRANSLATABLE_ROLES
            and role not in PRESERVED_TEXT_ROLES
            and role != "reference"
        ):
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
        physical_position = (
            anchor_unit["endPosition"] if anchor_unit else positions[anchor_id]
        ) + 0.1
        label = str(visual.get("label", "")).strip()
        semantic_anchor = (
            _select_visual_reference_anchor(
                list(units.values()),
                label,
                physical_position,
                set(section_by_id),
                int(visual.get("pageNumber", 0)) or None,
            )
            if label
            else None
        )
        if _should_preserve_paired_figure_parent(
            visual,
            caption_text.get(str(visual.get("objectId")), ""),
            semantic_anchor,
            physical_position,
            anchor_assignment.get("sectionId"),
            section_by_id,
        ):
            # A paired left/right figure physically placed in a parent section
            # may describe multiple child subsections.  A later child-level
            # remention must not steal that shared visual from its parent.
            semantic_anchor = None
        position = (
            float(semantic_anchor["endPosition"]) + 0.1
            if semantic_anchor is not None
            else physical_position
        )
        resolved_anchor_unit = semantic_anchor or anchor_unit
        resolved_insert_after = anchor_id
        if resolved_anchor_unit is not None:
            source_block_ids = list(
                resolved_anchor_unit.get("sourceBlockIds", [])
            )
            if source_block_ids:
                resolved_insert_after = str(source_block_ids[-1])
        rendered_visuals.append(
            {
                **visual,
                "caption": caption_text[visual["objectId"]],
                "insertAfterBlockId": resolved_insert_after,
                "sectionId": (
                    semantic_anchor.get("sectionId")
                    if semantic_anchor is not None
                    else "__papertrans_supplemental__"
                    if _is_references_section(
                        section_by_id.get(anchor_assignment.get("sectionId"))
                    )
                    and visual.get("kind") in {"figure", "table", "algorithm"}
                    else anchor_assignment.get("sectionId")
                ),
                "position": position,
            }
        )

    supplemental_visuals = [
        visual
        for visual in rendered_visuals
        if visual.get("sectionId") == "__papertrans_supplemental__"
    ]
    if supplemental_visuals:
        synthetic_id = "sec-supplemental-material"
        suffix = 2
        while synthetic_id in section_by_id:
            synthetic_id = f"sec-supplemental-material-{suffix}"
            suffix += 1
        page_start = min(
            int(visual.get("pageNumber", 1)) for visual in supplemental_visuals
        )
        synthetic_section = {
            "id": synthetic_id,
            "number": None,
            "level": 1,
            "parentSectionId": None,
            "pageStart": page_start,
            "syntheticUnheaded": True,
            "title": {
                "id": f"heading-{synthetic_id}",
                "kind": "heading",
                "sectionId": synthetic_id,
                "original": "Supplemental Material",
                "sourceHeading": "",
                "japanese": "",
                "sourceBlockIds": [],
                "pages": [page_start],
                "citations": [],
                "objectReferences": [],
                "externalLinks": [],
                "referenceLabel": None,
                "preservedTerms": [],
                "warnings": [
                    "PaperTrans detached unheaded trailing source figures "
                    "from the References section."
                ],
                "confidence": 1.0,
                "position": 0,
                "endPosition": 0,
            },
            "content": [],
        }
        sections.append(synthetic_section)
        section_by_id[synthetic_id] = synthetic_section
        for visual in supplemental_visuals:
            visual["sectionId"] = synthetic_id

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
        section["content"].sort(
            key=lambda item: (
                item["position"],
                item["type"] != "visual",
                (
                    int(item["value"].get("pageNumber", 0)),
                    float(item["value"].get("bboxNormalized", [0, 0])[1]),
                    str(item["value"].get("objectId", "")),
                )
                if item["type"] == "visual"
                else (0, 0.0, ""),
            )
        )

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
