from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from .metrics import record_stage, utc_now


SPECIAL_HEADINGS = {
    "abstract": ("abstract", 1),
    "acknowledgment": ("acknowledgments", 1),
    "acknowledgments": ("acknowledgments", 1),
    "acknowledgement": ("acknowledgments", 1),
    "acknowledgements": ("acknowledgments", 1),
    "references": ("references", 1),
    "bibliography": ("references", 1),
}
NUMBERED_HEADING = re.compile(r"^((?:[1-9]\d*)(?:\.[1-9]?\d*){0,5})[.)]?\s+(.+)$")
APPENDIX_HEADING = re.compile(r"^Appendix\s+([A-Z](?:\.\d+)*)[.)]?\s+(.+)$", re.IGNORECASE)
BARE_APPENDIX_HEADING = re.compile(r"^([A-Z](?:\.\d+)*)[.)]?\s+([A-Z][A-Za-z].+)$")
CAPTION = re.compile(
    r"^\s*(Figure|Fig\.?|Table|Algorithm)\s*([A-Z]?\d+(?:[.\-][A-Za-z0-9]+)*)\s*[:.\-]?\s*",
    re.IGNORECASE,
)
NUMERIC_CITATION = re.compile(r"\[(?:\d+[a-z]?(?:\s*[-,;]\s*\d+[a-z]?)*|\d+\s*(?:–|—)\s*\d+)\]")
AUTHOR_YEAR_CITATION = re.compile(
    r"\((?:[A-Z][A-Za-z'’\-]+(?:\s+et\s+al\.)?(?:\s+and\s+[A-Z][A-Za-z'’\-]+)?\s*,?\s*(?:19|20)\d{2}[a-z]?(?:\s*;\s*)?)+\)"
)
OBJECT_REFERENCE = re.compile(
    r"\b(?:Figure|Fig\.?|Table|Algorithm|Equation|Eq\.?)\s*\(?[A-Z]?\d+(?:[.\-][A-Za-z0-9]+)*\)?",
    re.IGNORECASE,
)
PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}$", re.IGNORECASE)
REFERENCE_LABEL = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s*")
LIST_ITEM = re.compile(r"^\s*(?:[•▪◦‣]|[-–—]|\([a-z0-9]+\)|[a-z0-9]+[.)])\s+", re.IGNORECASE)


def _rect(value: list[float]) -> tuple[float, float, float, float]:
    return tuple(float(part) for part in value)  # type: ignore[return-value]


def _area(rect: tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _union(rects: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(value[0] for value in rects),
        min(value[1] for value in rects),
        max(value[2] for value in rects),
        max(value[3] for value in rects),
    )


def _intersection_area(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def _contains_center(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    pad: float = 0.004,
) -> bool:
    center_x = (inner[0] + inner[2]) / 2
    center_y = (inner[1] + inner[3]) / 2
    return outer[0] - pad <= center_x <= outer[2] + pad and outer[1] - pad <= center_y <= outer[3] + pad


def _body_font(pages: list[dict[str, Any]]) -> float:
    likely_body = [
        (float(block.get("fontSizeMax", 0)), len(str(block.get("text", ""))))
        for page in pages
        for block in page["blocks"]
        if len(str(block.get("text", ""))) >= 80
        and 0.07 < float(block["bboxNormalized"][1]) < 0.94
        and 6 <= float(block.get("fontSizeMax", 0)) <= 15
    ]
    if likely_body:
        weighted_buckets: Counter[float] = Counter()
        for size, text_length in likely_body:
            weighted_buckets[round(size * 2) / 2] += min(text_length, 500)
        return float(weighted_buckets.most_common(1)[0][0])
    all_fonts = [
        float(block.get("fontSizeMax", 0))
        for page in pages
        for block in page["blocks"]
        if 6 <= float(block.get("fontSizeMax", 0)) <= 15
    ]
    return float(median(all_fonts)) if all_fonts else 10.0


def _canonical_furniture(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip().lower()
    return re.sub(r"\d+", "#", value)


def _repeated_furniture(pages: list[dict[str, Any]]) -> set[str]:
    per_page: list[set[str]] = []
    for page in pages:
        values = {
            _canonical_furniture(str(block["text"]))
            for block in page["blocks"]
            if float(block["bboxNormalized"][1]) <= 0.075
            or float(block["bboxNormalized"][3]) >= 0.945
        }
        per_page.append(values)
    counts = Counter(value for values in per_page for value in values if value)
    threshold = max(2, math.ceil(len(pages) * 0.12))
    return {value for value, count in counts.items() if count >= threshold}


def _heading(block: dict[str, Any], body_font: float) -> tuple[str | None, int, float] | None:
    text = str(block["text"]).strip()
    normalized = re.sub(r"\s+", " ", text).strip().rstrip(":").lower()
    if normalized in SPECIAL_HEADINGS:
        section_name, level = SPECIAL_HEADINGS[normalized]
        return None, level, 0.99
    numbered = NUMBERED_HEADING.match(text)
    if numbered:
        number = numbered.group(1).rstrip(".")
        title = numbered.group(2).strip()
        level = min(6, number.count(".") + 1)
        font_support = bool(block.get("bold")) or float(block.get("fontSizeMax", 0)) >= body_font * 1.04
        if len(title) <= 150 and font_support:
            return number, level, 0.97
    appendix = APPENDIX_HEADING.match(text) or BARE_APPENDIX_HEADING.match(text)
    if appendix and (block.get("bold") or float(block.get("fontSizeMax", 0)) >= body_font * 1.04):
        number = appendix.group(1).upper()
        return number, min(6, number.count(".") + 1), 0.9
    if (
        bool(block.get("bold"))
        and float(block.get("fontSizeMax", 0)) >= body_font * 1.08
        and len(text) <= 90
        and not text.endswith((".", ",", ";"))
    ):
        return None, 1, 0.9
    return None


def _section_slug(number: str | None, text: str) -> str:
    if number:
        base = number.lower().replace(".", "-")
    else:
        base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "section"
    return f"sec-{base}"


def _visual_candidates(page: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    values: list[tuple[float, float, float, float]] = []
    for raw in page.get("drawingClusters", []):
        candidate = _rect(raw)
        if _area(candidate) >= 0.001:
            values.append(candidate)
    for raw in page.get("imageRegions", []):
        candidate = _rect(raw)
        if _area(candidate) >= 0.008 and not any(
            _intersection_area(candidate, existing) / max(_area(candidate), 1e-9) >= 0.8
            for existing in values
        ):
            values.append(candidate)
    return values


def _visual_score(
    kind: str,
    caption: tuple[float, float, float, float],
    candidate: tuple[float, float, float, float],
) -> float:
    overlap = max(0.0, min(caption[2], candidate[2]) - max(caption[0], candidate[0]))
    overlap /= max(0.02, min(caption[2] - caption[0], candidate[2] - candidate[0]))
    if overlap < 0.15:
        return -1.0
    above_gap = caption[1] - candidate[3]
    below_gap = candidate[1] - caption[3]
    if kind == "table":
        primary_gap, secondary_gap = below_gap, above_gap
    else:
        primary_gap, secondary_gap = above_gap, below_gap
    if -0.015 <= primary_gap <= 0.22:
        return 2.0 + overlap - max(0.0, primary_gap) * 5
    if -0.015 <= secondary_gap <= 0.12:
        return 0.8 + overlap - max(0.0, secondary_gap) * 5
    return -1.0


def _fallback_visual_rect(
    page: dict[str, Any],
    caption_block: dict[str, Any],
    kind: str,
    body_font: float,
) -> tuple[float, float, float, float] | None:
    caption = _rect(caption_block["bboxNormalized"])
    above: list[tuple[float, float, float, float]] = []
    below: list[tuple[float, float, float, float]] = []
    for block in page["blocks"]:
        if block["blockId"] == caption_block["blockId"]:
            continue
        rect = _rect(block["bboxNormalized"])
        small = float(block.get("fontSizeMax", body_font)) <= body_font * 0.82
        horizontal = _intersection_area((caption[0], 0, caption[2], 1), rect) > 0
        if not (small and horizontal):
            continue
        if kind == "table" and -0.02 <= rect[1] - caption[3] <= 0.35:
            below.append(rect)
        elif kind == "table" and -0.02 <= caption[1] - rect[3] <= 0.25:
            above.append(rect)
        elif kind != "table" and -0.02 <= caption[1] - rect[3] <= 0.35:
            above.append(rect)
    if kind != "table":
        return _union(above) if len(above) >= 2 else None
    above_gap = min((caption[1] - rect[3] for rect in above), default=math.inf)
    below_gap = min((rect[1] - caption[3] for rect in below), default=math.inf)
    preferred = above if above_gap <= below_gap else below
    return _union(preferred) if len(preferred) >= 2 else None


def _detect_visuals(
    page: dict[str, Any],
    body_font: float,
    previous_anchor: str | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    candidates = _visual_candidates(page)
    visuals: list[dict[str, Any]] = []
    visual_block_ids: set[str] = set()
    prior_visible = previous_anchor
    for block in page["blocks"]:
        caption_match = CAPTION.match(str(block["text"]))
        if not caption_match:
            if not any(_contains_center(candidate, _rect(block["bboxNormalized"])) for candidate in candidates):
                prior_visible = block["blockId"]
            continue
        raw_kind = caption_match.group(1).lower().rstrip(".")
        kind = "figure" if raw_kind in {"figure", "fig"} else raw_kind
        label_prefix = "Figure" if kind == "figure" else kind.title()
        label = f"{label_prefix} {caption_match.group(2)}"
        caption_rect = _rect(block["bboxNormalized"])
        ranked = sorted(
            ((_visual_score(kind, caption_rect, candidate), candidate) for candidate in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        warnings: list[str] = []
        if ranked and ranked[0][0] >= 0:
            rect = ranked[0][1]
            confidence = 0.9 if ranked[0][0] >= 1.5 else 0.72
        else:
            fallback = _fallback_visual_rect(page, block, kind, body_font)
            if fallback is None:
                warnings.append(f"No reliable original {kind} body region was found near {label}.")
                continue
            rect = fallback
            if kind == "table" and rect[2] - rect[0] >= 0.5:
                confidence = 0.76
                warnings.append("Table body was inferred from aligned original text geometry.")
            else:
                confidence = 0.52
                warnings.append("Visual body was inferred from small-font embedded text and requires review.")
        object_id = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        visuals.append(
            {
                "objectId": object_id,
                "kind": kind,
                "label": label,
                "bboxNormalized": [round(value, 5) for value in rect],
                "captionBlockIds": [block["blockId"]],
                "insertAfterBlockId": prior_visible,
                "confidence": confidence,
                "warnings": warnings,
            }
        )
        for candidate_block in page["blocks"]:
            if candidate_block["blockId"] != block["blockId"] and _contains_center(
                rect, _rect(candidate_block["bboxNormalized"])
            ):
                visual_block_ids.add(candidate_block["blockId"])
        prior_visible = block["blockId"]
    return visuals, visual_block_ids


def _looks_like_equation(block: dict[str, Any], body_font: float) -> bool:
    text = str(block["text"]).strip()
    if (
        not text
        or len(text) > 260
        or CAPTION.match(text)
        or re.match(r"^(?:where|given|when|with|the)\b", text, re.IGNORECASE)
    ):
        return False
    rect = _rect(block["bboxNormalized"])
    centered = rect[0] > 0.08 and rect[2] < 0.92 and (rect[2] - rect[0]) < 0.8
    has_operator = bool(re.search(r"[=≈≃≤≥∑∏∫√±×÷∂∇]|\b(?:argmax|argmin|log|exp)\b", text))
    equation_number = bool(re.search(r"\(\d+[a-z]?\)\s*$", text))
    symbol_count = len(re.findall(r"[=+*/<>_^{}()[\]∑∏∫√±×÷∂∇]", text))
    word_count = len(re.findall(r"[A-Za-z]{4,}", text))
    math_font = any(
        token in str(font).lower()
        for font in block.get("fonts", [])
        for token in ("cmmi", "cmsy", "cmex", "math", "symbol")
    )
    control_glyph = any(ord(character) < 32 for character in text)
    return centered and word_count <= 8 and float(block.get("fontSizeMax", body_font)) <= body_font * 1.15 and (
        (has_operator and (equation_number or symbol_count >= 2)) or math_font or control_glyph
    )


def _detect_equation_groups(
    page: dict[str, Any],
    body_font: float,
    excluded_ids: set[str],
    previous_anchor: str | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    candidates = [
        block
        for block in page["blocks"]
        if block["blockId"] not in excluded_ids and _looks_like_equation(block, body_font)
    ]
    candidates.sort(key=lambda block: (float(block["bboxNormalized"][1]), float(block["bboxNormalized"][0])))
    groups: list[list[dict[str, Any]]] = []
    for block in candidates:
        rect = _rect(block["bboxNormalized"])
        if groups:
            group_rect = _union([_rect(value["bboxNormalized"]) for value in groups[-1]])
            if rect[1] <= group_rect[3] + 0.018:
                groups[-1].append(block)
                continue
        groups.append([block])

    visuals: list[dict[str, Any]] = []
    equation_ids: set[str] = set()
    page_blocks = list(page["blocks"])
    for index, group in enumerate(groups, start=1):
        equation_ids.update(str(block["blockId"]) for block in group)
        rect = _union([_rect(block["bboxNormalized"]) for block in group])
        rect = (
            max(0.0, rect[0] - 0.003),
            max(0.0, rect[1] - 0.003),
            min(1.0, rect[2] + 0.003),
            min(1.0, rect[3] + 0.003),
        )
        first_position = page_blocks.index(group[0])
        anchor = previous_anchor
        for previous in reversed(page_blocks[:first_position]):
            if previous["blockId"] not in excluded_ids and previous["blockId"] not in equation_ids:
                anchor = previous["blockId"]
                break
        combined_text = " ".join(str(block["text"]) for block in group)
        label_match = re.search(r"\((\d+[a-z]?)\)\s*$", combined_text)
        visuals.append(
            {
                "objectId": f"equation-p{page['pageNumber']}-{index}",
                "kind": "equation",
                "label": f"Equation {label_match.group(1)}" if label_match else None,
                "bboxNormalized": [round(value, 5) for value in rect],
                "captionBlockIds": [],
                "insertAfterBlockId": anchor,
                "confidence": 0.84,
                "warnings": ["Equation is preserved from exact original PDF coordinates."],
            }
        )
    return visuals, equation_ids


def _citations(text: str) -> list[str]:
    return list(dict.fromkeys(NUMERIC_CITATION.findall(text) + AUTHOR_YEAR_CITATION.findall(text)))


def _object_references(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in OBJECT_REFERENCE.finditer(text)))


def analyze_layout_deterministic(
    evidence: dict[str, Any],
    output_json: Path,
    max_pages: int | None = None,
    metrics_path: Path | None = None,
) -> dict[str, Any]:
    """Build a complete structure proposal without invoking a language model."""
    stage_started: datetime = utc_now()
    pages = evidence["pages"][:max_pages] if max_pages else evidence["pages"]
    body_font = _body_font(pages)
    repeated = _repeated_furniture(pages)
    sections: list[dict[str, Any]] = []
    section_ids: set[str] = set()
    section_by_number: dict[str, str] = {}
    result_pages: list[dict[str, Any]] = []
    warnings: list[str] = []
    current_section: str | None = None
    current_section_title = ""
    paragraph_counter = 0
    previous_body: dict[str, Any] | None = None
    last_visible_block_id: str | None = None
    page_confidences: dict[int, float] = {}

    for page in pages:
        page_number = int(page["pageNumber"])
        visuals, visual_block_ids = _detect_visuals(page, body_font, last_visible_block_id)
        equation_visuals, equation_block_ids = _detect_equation_groups(
            page, body_font, visual_block_ids, last_visible_block_id
        )
        visuals.extend(equation_visuals)
        caption_ids = {block_id for visual in visuals for block_id in visual["captionBlockIds"]}
        assignments: list[dict[str, Any]] = []
        first_heading_y = min(
            (
                float(block["bboxNormalized"][1])
                for block in page["blocks"]
                if (
                    (heading := _heading(block, body_font)) is not None
                    and heading[2] >= 0.9
                    and block["blockId"] not in visual_block_ids
                )
            ),
            default=1.0,
        )
        seen_title = False
        seen_author = False

        for reading_order, block in enumerate(page["blocks"], start=1):
            block_id = str(block["blockId"])
            text = str(block["text"]).strip()
            rect = _rect(block["bboxNormalized"])
            role = "paragraph"
            section_id = current_section
            paragraph_id: str | None = None
            continues_from: str | None = None
            hidden = False
            confidence = 0.82
            block_warnings: list[str] = []
            reference_label: str | None = None

            if block_id in visual_block_ids:
                role = "noise"
                hidden = True
                confidence = 0.98
                block_warnings.append("Text is embedded in an original visual object and is represented by its crop.")
            elif block_id in caption_ids:
                role = "caption"
                confidence = 0.98
            else:
                canonical = _canonical_furniture(text)
                if PAGE_NUMBER.match(text) and (rect[1] > 0.9 or rect[3] < 0.1):
                    role = "page_number"
                    section_id = None
                    hidden = True
                    confidence = 0.99
                elif canonical in repeated and (rect[1] <= 0.075 or rect[3] >= 0.945):
                    role = "header" if rect[1] <= 0.075 else "footer"
                    section_id = None
                    hidden = True
                    confidence = 0.98
                elif "arxiv" in text.lower() or "doi.org" in text.lower():
                    role = "metadata"
                    section_id = None
                    hidden = rect[1] > 0.85 or rect[3] < 0.12
                    confidence = 0.96
                else:
                    front_matter = page_number == 1 and rect[1] < first_heading_y and current_section is None
                    if front_matter:
                        max_font = float(block.get("fontSizeMax", 0))
                        if max_font >= body_font * 1.16 and rect[0] < 0.4 and rect[2] > 0.6:
                            role = "title"
                            section_id = None
                            paragraph_id = "front-title"
                            confidence = 0.94
                            seen_title = True
                        elif seen_title and not seen_author and ("," in text or bool(block.get("bold"))):
                            role = "author"
                            section_id = None
                            paragraph_id = "front-authors"
                            confidence = 0.84
                            seen_author = True
                        elif seen_title:
                            role = "affiliation"
                            section_id = None
                            paragraph_id = "front-affiliations"
                            confidence = 0.82
                        else:
                            role = "metadata"
                            section_id = None
                            confidence = 0.55
                            block_warnings.append("Front-matter role is ambiguous.")
                        heading = None
                    else:
                        heading = _heading(block, body_font)
                    if heading is not None:
                        number, level, confidence = heading
                        slug = _section_slug(number, text)
                        original_slug = slug
                        suffix = 2
                        while slug in section_ids:
                            slug = f"{original_slug}-{suffix}"
                            suffix += 1
                        parent = None
                        if number and "." in number:
                            parent = section_by_number.get(number.rsplit(".", 1)[0])
                        role = "heading"
                        section_id = slug
                        current_section = slug
                        current_section_title = text.lower()
                        section_ids.add(slug)
                        if number:
                            section_by_number[number] = slug
                        sections.append(
                            {
                                "sectionId": slug,
                                "number": number,
                                "titleBlockId": block_id,
                                "level": level,
                                "parentSectionId": parent,
                                "pageStart": page_number,
                            }
                        )
                        previous_body = None
                    elif current_section_title in {"references", "bibliography"} or current_section == "sec-references":
                        role = "reference"
                        match = REFERENCE_LABEL.match(text)
                        reference_label = next((value for value in match.groups() if value), None) if match else None
                        paragraph_counter += 1
                        paragraph_id = f"reference-{reference_label or paragraph_counter}"
                        confidence = 0.93 if reference_label else 0.75
                    elif block_id in equation_block_ids:
                        role = "equation"
                        confidence = 0.84
                    else:
                        if current_section and "abstract" in current_section:
                            role = "abstract"
                        elif LIST_ITEM.match(text):
                            role = "list_item"
                        elif rect[1] > 0.88 and float(block.get("fontSizeMax", body_font)) < body_font * 0.85:
                            role = "footnote"
                            confidence = 0.7
                        paragraph_counter += 1
                        paragraph_id = f"para-{paragraph_counter}"
                        if previous_body is not None:
                            previous_text = str(previous_body["text"]).rstrip()
                            grammatical_continuation = bool(text[:1].islower()) or (
                                previous_text
                                and previous_text[-1] not in ".!?;:)]}”’"
                            )
                            page_continuation = previous_body["pageNumber"] != page_number and reading_order <= 3
                            same_page_continuation = (
                                previous_body["pageNumber"] == page_number
                                and abs(float(previous_body["rect"][0]) - rect[0]) < 0.03
                                and float(previous_body["rect"][3]) <= rect[1]
                                and rect[1] - float(previous_body["rect"][3]) < 0.025
                            )
                            if grammatical_continuation and (page_continuation or same_page_continuation):
                                paragraph_id = previous_body["paragraphId"]
                                continues_from = previous_body["blockId"]
                                confidence = min(confidence, 0.74)

            assignment = {
                "blockId": block_id,
                "role": role,
                "readingOrder": reading_order,
                "sectionId": section_id,
                "paragraphId": paragraph_id,
                "continuesFrom": continues_from,
                "hidden": hidden,
                "citations": _citations(text),
                "objectReferences": _object_references(text),
                "referenceLabel": reference_label,
                "confidence": confidence,
                "warnings": block_warnings,
            }
            assignments.append(assignment)
            if not hidden and role not in {"caption", "heading", "equation", "metadata"}:
                last_visible_block_id = block_id
            if not hidden and role in {"abstract", "paragraph", "list_item", "footnote"}:
                previous_body = {
                    "blockId": block_id,
                    "paragraphId": paragraph_id,
                    "text": text,
                    "rect": rect,
                    "pageNumber": page_number,
                }

        confidences = [float(value["confidence"]) for value in assignments if not value["hidden"]]
        confidences.extend(float(value["confidence"]) for value in visuals)
        page_confidences[page_number] = min(confidences, default=1.0)
        result_pages.append(
            {
                "pageNumber": page_number,
                "blockAssignments": assignments,
                "visualObjects": visuals,
            }
        )

    uncertain_pages = [page for page, confidence in page_confidences.items() if confidence < 0.7]
    if uncertain_pages:
        warnings.append(
            "Deterministic analysis marked pages requiring semantic review: "
            + ", ".join(str(value) for value in uncertain_pages)
        )
    result = {
        "version": 3,
        "sourceFile": evidence["sourceFile"],
        "model": {"name": "deterministic-hybrid-v1", "reasoningEffort": "none"},
        "pages": result_pages,
        "sections": sections,
        "warnings": warnings,
        "analysis": {
            "bodyFontSize": round(body_font, 3),
            "uncertainPages": uncertain_pages,
            "pageConfidence": {str(key): round(value, 3) for key, value in page_confidences.items()},
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    record_stage(
        metrics_path,
        "deterministic_structure",
        stage_started,
        utc_now(),
        {
            "pages": len(result_pages),
            "sections": len(sections),
            "visualObjects": sum(len(page["visualObjects"]) for page in result_pages),
            "uncertainPages": len(uncertain_pages),
        },
    )
    return result


def _iou(left: list[float], right: list[float]) -> float:
    left_rect = _rect(left)
    right_rect = _rect(right)
    intersection = _intersection_area(left_rect, right_rect)
    union = _area(left_rect) + _area(right_rect) - intersection
    return intersection / union if union else 0.0


def evaluate_structure(gold: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    gold_page_numbers = {int(page["pageNumber"]) for page in gold["pages"]}
    gold_assignments = {
        value["blockId"]: value
        for page in gold["pages"]
        for value in page["blockAssignments"]
    }
    candidate_assignments = {
        value["blockId"]: value
        for page in candidate["pages"]
        if int(page["pageNumber"]) in gold_page_numbers
        for value in page["blockAssignments"]
    }
    shared = sorted(set(gold_assignments) & set(candidate_assignments))
    role_correct = sum(gold_assignments[key]["role"] == candidate_assignments[key]["role"] for key in shared)
    hidden_correct = sum(
        bool(gold_assignments[key]["hidden"]) == bool(candidate_assignments[key]["hidden"])
        for key in shared
    )
    gold_headings = {key for key, value in gold_assignments.items() if value["role"] == "heading"}
    candidate_headings = {key for key, value in candidate_assignments.items() if value["role"] == "heading"}
    heading_true_positive = len(gold_headings & candidate_headings)
    heading_precision = heading_true_positive / len(candidate_headings) if candidate_headings else 1.0
    heading_recall = heading_true_positive / len(gold_headings) if gold_headings else 1.0
    gold_sections = {
        str(section.get("number") or section["sectionId"]).lower()
        for section in gold["sections"]
        if int(section["pageStart"]) in gold_page_numbers
    }
    candidate_sections = {
        str(section.get("number") or section["sectionId"]).lower()
        for section in candidate["sections"]
        if int(section["pageStart"]) in gold_page_numbers
    }
    section_true_positive = len(gold_sections & candidate_sections)
    section_precision = section_true_positive / len(candidate_sections) if candidate_sections else 1.0
    section_recall = section_true_positive / len(gold_sections) if gold_sections else 1.0

    gold_visuals = [
        {**value, "pageNumber": int(page["pageNumber"])}
        for page in gold["pages"]
        for value in page["visualObjects"]
    ]
    candidate_visuals = [
        {**value, "pageNumber": int(page["pageNumber"])}
        for page in candidate["pages"]
        if int(page["pageNumber"]) in gold_page_numbers
        for value in page["visualObjects"]
    ]
    unused_candidates = set(range(len(candidate_visuals)))
    visual_matches: list[dict[str, Any]] = []
    for gold_visual in gold_visuals:
        gold_label = str(gold_visual.get("label") or "").lower()
        possible = [
            index
            for index in unused_candidates
            if candidate_visuals[index]["pageNumber"] == gold_visual["pageNumber"]
            and candidate_visuals[index]["kind"] == gold_visual["kind"]
            and (
                not gold_label
                or str(candidate_visuals[index].get("label") or "").lower() == gold_label
            )
        ]
        if not possible:
            continue
        best = max(
            possible,
            key=lambda index: _iou(
                gold_visual["bboxNormalized"], candidate_visuals[index]["bboxNormalized"]
            ),
        )
        unused_candidates.remove(best)
        score = _iou(gold_visual["bboxNormalized"], candidate_visuals[best]["bboxNormalized"])
        visual_matches.append(
            {
                "gold": gold_visual.get("label") or gold_visual.get("objectId"),
                "candidate": candidate_visuals[best].get("label")
                or candidate_visuals[best].get("objectId"),
                "page": gold_visual["pageNumber"],
                "iou": score,
            }
        )
    return {
        "blocks": {
            "gold": len(gold_assignments),
            "candidate": len(candidate_assignments),
            "shared": len(shared),
            "coverage": len(shared) / len(gold_assignments) if gold_assignments else 1.0,
            "roleAccuracy": role_correct / len(shared) if shared else 0.0,
            "hiddenAccuracy": hidden_correct / len(shared) if shared else 0.0,
        },
        "headings": {
            "gold": len(gold_headings),
            "candidate": len(candidate_headings),
            "precision": heading_precision,
            "recall": heading_recall,
            "f1": (
                2 * heading_precision * heading_recall / (heading_precision + heading_recall)
                if heading_precision + heading_recall
                else 0.0
            ),
        },
        "sections": {
            "gold": len(gold_sections),
            "candidate": len(candidate_sections),
            "precision": section_precision,
            "recall": section_recall,
            "f1": (
                2 * section_precision * section_recall / (section_precision + section_recall)
                if section_precision + section_recall
                else 0.0
            ),
        },
        "visuals": {
            "gold": len(gold_visuals),
            "candidate": len(candidate_visuals),
            "matched": len(visual_matches),
            "meanIoU": (
                sum(value["iou"] for value in visual_matches) / len(visual_matches)
                if visual_matches
                else 0.0
            ),
            "matches": visual_matches,
        },
    }
