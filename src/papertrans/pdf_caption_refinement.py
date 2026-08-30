"""Conservative caption recovery from a source PDF text layer.

Docling occasionally emits one printed caption line as several vertically
interleaved blocks.  Rejoining those blocks by their Docling reading order can
therefore corrupt otherwise exact caption text.  This module instead uses the
PDF's own line and span geometry, while treating the structure/evidence pair as
the authority for *which* visual owns the caption.

The public helper deliberately returns only ``objectId -> text`` overrides.  It
does not mutate evidence or structure and it fails open (no override) whenever
the source page, label, geometry, line continuation, or text coverage is
ambiguous.  A caller can apply the mapping after deterministic structure has
assigned caption blocks and before semantic rendering materializes captions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import math
import re
from statistics import median
from typing import Any, Mapping, Sequence

import pymupdf as fitz


_LABEL_RE = re.compile(
    r"^\s*(figure|fig(?:ure)?\.?|table|algorithm)\s*"
    r"([A-Za-z]?\d+(?:[.\-]\d+)*(?:[A-Za-z])?)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z0-9]+", re.UNICODE)
_TERMINAL_RE = re.compile(r"[.!?](?:[\"'\u2019\u201d)\]])?\s*$")
_SUPERSCRIPT_TRANSLATION = str.maketrans(
    {
        "0": "\u2070",
        "1": "\u00b9",
        "2": "\u00b2",
        "3": "\u00b3",
        "4": "\u2074",
        "5": "\u2075",
        "6": "\u2076",
        "7": "\u2077",
        "8": "\u2078",
        "9": "\u2079",
        "+": "\u207a",
        "-": "\u207b",
        "=": "\u207c",
        "(": "\u207d",
        ")": "\u207e",
    }
)
_SUPERSCRIPT_SOURCE = frozenset("0123456789+-=()")


BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class _PdfSpan:
    text: str
    bbox: BBox
    size: float
    origin_y: float


@dataclass(frozen=True)
class _PdfLine:
    spans: tuple[_PdfSpan, ...]
    bbox: BBox
    baseline: float
    main_size: float


def refine_pdf_caption_texts(
    source_pdf: str | Path,
    evidence: Mapping[str, Any],
    structure: Mapping[str, Any],
) -> dict[str, str]:
    """Return exact, high-confidence caption text overrides by visual object.

    ``evidence`` is expected to contain page blocks with ``blockId`` and
    top-left PDF-space ``bboxPdf`` values.  ``structure`` is expected to contain
    page visual objects with ``objectId``, ``label``, and ``captionBlockIds``.
    Malformed inputs, missing PDFs, image-only pages, coordinate mismatches, and
    ambiguous text-layer matches simply produce no override for the affected
    object.
    """

    path = Path(source_pdf)
    try:
        document = fitz.open(path)
    except Exception:
        return {}

    overrides: dict[str, str] = {}
    ambiguous_ids: set[str] = set()
    seen_ids: set[str] = set()
    try:
        if document.needs_pass or document.page_count <= 0:
            return {}
        declared_count = _finite_int(evidence.get("pageCount"))
        if declared_count is not None and declared_count != document.page_count:
            return {}

        evidence_pages = {
            number: page
            for page in _mapping_items(evidence.get("pages"))
            if (number := _page_number(page)) is not None
        }
        for structure_page in _mapping_items(structure.get("pages")):
            page_number = _page_number(structure_page)
            if page_number is None or not 1 <= page_number <= document.page_count:
                continue
            evidence_page = evidence_pages.get(page_number)
            if evidence_page is None:
                continue
            pdf_page = document[page_number - 1]
            if not _page_dimensions_agree(pdf_page, evidence_page):
                continue
            blocks = {
                str(block.get("blockId")): block
                for block in _mapping_items(evidence_page.get("blocks"))
                if block.get("blockId")
            }
            lines = _extract_pdf_lines(pdf_page)
            if not lines:
                continue
            for visual in _mapping_items(structure_page.get("visualObjects")):
                object_id = visual.get("objectId")
                if not isinstance(object_id, str) or not object_id:
                    continue
                if object_id in seen_ids:
                    overrides.pop(object_id, None)
                    ambiguous_ids.add(object_id)
                    continue
                seen_ids.add(object_id)
                text = _caption_override_for_visual(
                    visual=visual,
                    blocks=blocks,
                    lines=lines,
                    page_width=float(pdf_page.rect.width),
                    page_height=float(pdf_page.rect.height),
                )
                if text is not None:
                    overrides[object_id] = text
    except Exception:
        # Refinement is optional: a parser/library edge case must never make the
        # PDF ingestion pipeline fail.
        return {}
    finally:
        document.close()
    return overrides


def _caption_override_for_visual(
    *,
    visual: Mapping[str, Any],
    blocks: Mapping[str, Mapping[str, Any]],
    lines: Sequence[_PdfLine],
    page_width: float,
    page_height: float,
) -> str | None:
    label = visual.get("label")
    label_key = _label_key(label)
    if label_key is None:
        return None

    raw_ids = visual.get("captionBlockIds")
    if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
        return None
    caption_ids = [value for value in raw_ids if isinstance(value, str) and value]
    if not caption_ids or len(caption_ids) != len(set(caption_ids)):
        return None
    caption_blocks = [blocks.get(block_id) for block_id in caption_ids]
    if any(block is None for block in caption_blocks):
        return None
    caption_bboxes = [_bbox(block.get("bboxPdf")) for block in caption_blocks if block]
    if len(caption_bboxes) != len(caption_blocks) or any(value is None for value in caption_bboxes):
        return None
    concrete_bboxes = [value for value in caption_bboxes if value is not None]
    caption_union = _union(concrete_bboxes)
    if caption_union is None or not _bbox_on_page(caption_union, page_width, page_height):
        return None

    start_candidates = [
        line
        for line in lines
        if _label_key(_render_line(line)) == label_key
        and _line_matches_caption_box(line, caption_union)
    ]
    # Duplicate printed labels or overlapping extraction layers are ambiguous.
    if len(start_candidates) != 1:
        return None
    start = start_candidates[0]

    chain = _caption_line_chain(
        start=start,
        lines=lines,
        caption_union=caption_union,
        page_width=page_width,
        page_height=page_height,
    )
    if chain is None or not _chain_covers_blocks(chain, concrete_bboxes):
        return None
    text = _join_line_texts([_render_line(line) for line in chain])
    if not text or not _TERMINAL_RE.search(text):
        return None
    if _label_key(text) != label_key:
        return None

    evidence_text = " ".join(
        str(block.get("text", "")) for block in caption_blocks if block is not None
    )
    if _evidence_token_coverage(evidence_text, text) < 0.55:
        return None
    return text


def _extract_pdf_lines(page: fitz.Page) -> list[_PdfLine]:
    payload = page.get_text("dict", sort=False)
    result: list[_PdfLine] = []
    for block in payload.get("blocks", []):
        for raw_line in block.get("lines", []):
            direction = raw_line.get("dir", (1.0, 0.0))
            if (
                raw_line.get("wmode", 0) != 0
                or len(direction) != 2
                or abs(float(direction[0]) - 1.0) > 0.03
                or abs(float(direction[1])) > 0.03
            ):
                continue
            spans: list[_PdfSpan] = []
            for raw_span in raw_line.get("spans", []):
                text = raw_span.get("text")
                bbox = _bbox(raw_span.get("bbox"))
                size = _finite_float(raw_span.get("size"))
                origin = raw_span.get("origin")
                if (
                    not isinstance(text, str)
                    or not text
                    or bbox is None
                    or size is None
                    or size <= 0
                    or not isinstance(origin, Sequence)
                    or isinstance(origin, (str, bytes))
                    or len(origin) != 2
                ):
                    continue
                origin_y = _finite_float(origin[1])
                if origin_y is None:
                    continue
                spans.append(_PdfSpan(text, bbox, size, origin_y))
            if not spans or not any(span.text.strip() for span in spans):
                continue
            spans.sort(key=lambda span: (span.bbox[0], span.bbox[1]))
            main_size = max(span.size for span in spans)
            baseline_origins = [
                span.origin_y for span in spans if span.size >= main_size * 0.84 and span.text.strip()
            ]
            if not baseline_origins:
                continue
            line_bbox = _union([span.bbox for span in spans])
            if line_bbox is None:
                continue
            result.append(
                _PdfLine(
                    spans=tuple(spans),
                    bbox=line_bbox,
                    baseline=float(median(baseline_origins)),
                    main_size=main_size,
                )
            )
    return _merge_pdf_line_fragments(result)


def _merge_pdf_line_fragments(lines: Sequence[_PdfLine]) -> list[_PdfLine]:
    """Merge adjacent inline fragments that PyMuPDF reports as separate lines.

    TeX frequently paints a bold ``Figure N.`` label and its following prose in
    separate text operations on the same baseline.  Treating those operations
    as different logical lines would pass the label check while silently
    dropping the first caption words.  The tight baseline, font-size, and gap
    constraints below merge that case without joining ordinary table columns.
    """

    pending = sorted(lines, key=lambda line: (line.baseline, line.bbox[0]))
    merged: list[_PdfLine] = []
    while pending:
        current = pending.pop(0)
        while True:
            candidates = [
                line
                for line in pending
                if abs(line.baseline - current.baseline)
                <= max(0.7, current.main_size * 0.10)
                and 0.84 <= line.main_size / max(current.main_size, 0.01) <= 1.16
                and -current.main_size * 0.18
                <= line.bbox[0] - current.bbox[2]
                <= current.main_size * 1.45
            ]
            if len(candidates) != 1:
                break
            following = candidates[0]
            pending.remove(following)
            following_spans = list(following.spans)
            horizontal_gap = following.bbox[0] - current.bbox[2]
            if (
                following_spans
                and horizontal_gap > current.main_size * 0.18
                and not current.spans[-1].text[-1:].isspace()
                and not following_spans[0].text[:1].isspace()
            ):
                first = following_spans[0]
                following_spans[0] = _PdfSpan(
                    text=" " + first.text,
                    bbox=first.bbox,
                    size=first.size,
                    origin_y=first.origin_y,
                )
            spans = tuple(
                sorted(
                    current.spans + tuple(following_spans),
                    key=lambda span: (span.bbox[0], span.bbox[1]),
                )
            )
            bbox = _union([current.bbox, following.bbox])
            if bbox is None:
                break
            main_size = max(current.main_size, following.main_size)
            baseline_values = [
                span.origin_y
                for span in spans
                if span.text.strip() and span.size >= main_size * 0.84
            ]
            current = _PdfLine(
                spans=spans,
                bbox=bbox,
                baseline=float(median(baseline_values)),
                main_size=main_size,
            )
        merged.append(current)
    return merged


def _render_line(line: _PdfLine) -> str:
    pieces: list[str] = []
    for span in line.spans:
        text = span.text
        stripped = text.strip()
        elevated = span.origin_y <= line.baseline - max(0.7, line.main_size * 0.10)
        is_small = span.size <= line.main_size * 0.80
        if (
            stripped
            and is_small
            and elevated
            and all(character in _SUPERSCRIPT_SOURCE for character in stripped)
        ):
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            text = leading + stripped.translate(_SUPERSCRIPT_TRANSLATION) + trailing
        pieces.append(text)
    return _clean_inline_whitespace("".join(pieces))


def _caption_line_chain(
    *,
    start: _PdfLine,
    lines: Sequence[_PdfLine],
    caption_union: BBox,
    page_width: float,
    page_height: float,
) -> list[_PdfLine] | None:
    chain = [start]
    current = start
    max_extension = max(start.main_size * 2.3, page_height * 0.035)

    for _ in range(7):
        current_text = _render_line(current)
        union_is_covered = current.bbox[3] >= caption_union[3] - max(1.5, current.main_size * 0.2)
        if union_is_covered and _TERMINAL_RE.search(current_text):
            return chain

        later = [
            line
            for line in lines
            if line.baseline > current.baseline + max(1.0, current.main_size * 0.38)
            and line.baseline <= current.baseline + current.main_size * 1.75
            and line.bbox[1] <= caption_union[3] + max_extension
        ]
        if not later:
            return None
        next_baseline = min(line.baseline for line in later)
        baseline_band = max(1.0, current.main_size * 0.20)
        cluster = [line for line in later if abs(line.baseline - next_baseline) <= baseline_band]
        compatible = [
            line
            for line in cluster
            if _continuation_is_compatible(
                start=start,
                previous=current,
                candidate=line,
                caption_union=caption_union,
                page_width=page_width,
            )
        ]
        if len(compatible) != 1:
            return None
        current = compatible[0]
        chain.append(current)

    return None


def _continuation_is_compatible(
    *,
    start: _PdfLine,
    previous: _PdfLine,
    candidate: _PdfLine,
    caption_union: BBox,
    page_width: float,
) -> bool:
    size_ratio = candidate.main_size / max(previous.main_size, 0.01)
    if not 0.82 <= size_ratio <= 1.18:
        return False
    left_tolerance = max(start.main_size * 2.2, page_width * 0.055)
    if abs(candidate.bbox[0] - start.bbox[0]) > left_tolerance:
        return False
    if _label_key(_render_line(candidate)) is not None:
        return False
    horizontal_overlap = _overlap_1d(
        (candidate.bbox[0], candidate.bbox[2]),
        (caption_union[0], caption_union[2]),
    )
    if horizontal_overlap / max(1.0, min(_width(candidate.bbox), _width(caption_union))) < 0.35:
        return False
    return True


def _line_matches_caption_box(line: _PdfLine, caption_union: BBox) -> bool:
    horizontal = _overlap_1d(
        (line.bbox[0], line.bbox[2]), (caption_union[0], caption_union[2])
    )
    horizontal_ratio = horizontal / max(1.0, min(_width(line.bbox), _width(caption_union)))
    vertical = _overlap_1d(
        (line.bbox[1], line.bbox[3]), (caption_union[1], caption_union[3])
    )
    vertical_ratio = vertical / max(1.0, min(_height(line.bbox), _height(caption_union)))
    center_distance = abs(
        (line.bbox[1] + line.bbox[3] - caption_union[1] - caption_union[3]) / 2.0
    )
    return horizontal_ratio >= 0.55 and (
        vertical_ratio >= 0.20 or center_distance <= line.main_size * 0.9
    )


def _chain_covers_blocks(chain: Sequence[_PdfLine], blocks: Sequence[BBox]) -> bool:
    envelope = _union([line.bbox for line in chain])
    if envelope is None:
        return False
    tolerance = max(2.5, max(line.main_size for line in chain) * 0.32)
    expanded = (
        envelope[0] - tolerance,
        envelope[1] - tolerance,
        envelope[2] + tolerance,
        envelope[3] + tolerance,
    )
    for block in blocks:
        intersection = _intersection_area(expanded, block)
        if intersection / max(1.0, _width(block) * _height(block)) < 0.88:
            return False
    return True


def _evidence_token_coverage(evidence_text: str, recovered_text: str) -> float:
    evidence_tokens = Counter(token.lower() for token in _WORD_RE.findall(evidence_text))
    recovered_tokens = Counter(token.lower() for token in _WORD_RE.findall(recovered_text))
    if not evidence_tokens:
        return 0.0
    matched = sum((evidence_tokens & recovered_tokens).values())
    return matched / sum(evidence_tokens.values())


def _label_key(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    match = _LABEL_RE.match(value)
    if match is None:
        return None
    raw_kind = match.group(1).lower().rstrip(".")
    kind = "figure" if raw_kind.startswith("fig") else raw_kind
    return kind, match.group(2).lower()


def _join_line_texts(lines: Sequence[str]) -> str:
    result = ""
    for value in lines:
        value = value.strip()
        if not value:
            continue
        if result.endswith("-") and value[:1].isalnum():
            result += value
        elif result:
            result += " " + value
        else:
            result = value
    return _clean_inline_whitespace(result)


def _clean_inline_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _page_number(page: Mapping[str, Any]) -> int | None:
    return _finite_int(page.get("pageNumber", page.get("page")))


def _page_dimensions_agree(page: fitz.Page, evidence_page: Mapping[str, Any]) -> bool:
    width = _finite_float(evidence_page.get("widthPdf"))
    height = _finite_float(evidence_page.get("heightPdf"))
    if width is None or height is None or width <= 0 or height <= 0:
        return False
    return (
        abs(width - float(page.rect.width)) <= max(1.0, float(page.rect.width) * 0.01)
        and abs(height - float(page.rect.height)) <= max(1.0, float(page.rect.height) * 0.01)
    )


def _mapping_items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _bbox(value: Any) -> BBox | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return None
    coordinates = tuple(_finite_float(item) for item in value)
    if any(item is None for item in coordinates):
        return None
    x0, y0, x1, y1 = (float(item) for item in coordinates if item is not None)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _bbox_on_page(bbox: BBox, width: float, height: float) -> bool:
    tolerance = 1.5
    return (
        bbox[0] >= -tolerance
        and bbox[1] >= -tolerance
        and bbox[2] <= width + tolerance
        and bbox[3] <= height + tolerance
    )


def _union(boxes: Sequence[BBox]) -> BBox | None:
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _intersection_area(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def _overlap_1d(left: tuple[float, float], right: tuple[float, float]) -> float:
    return max(0.0, min(left[1], right[1]) - max(left[0], right[0]))


def _width(bbox: BBox) -> float:
    return bbox[2] - bbox[0]


def _height(bbox: BBox) -> float:
    return bbox[3] - bbox[1]


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _finite_int(value: Any) -> int | None:
    number = _finite_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)
