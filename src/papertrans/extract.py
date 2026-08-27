from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pymupdf as fitz

from .io import save_document
from .models import BBox, DocumentIR, DocumentItem, PageIR


CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table|Algorithm)\s+[A-Z0-9]+[.:]", re.IGNORECASE)
REFERENCE_HEADING_RE = re.compile(r"^(References|Bibliography)$", re.IGNORECASE)
EQUATION_NUMBER_RE = re.compile(r"\([0-9]{1,3}\)\s*$")
MATH_MARKERS_RE = re.compile(r"[=∑∏√∞≤≥≈∈∉⊂⊆→←↔α-ωΑ-Ω]|\b(arg|min|max|log|exp|softmax)\b")


@dataclass
class Region:
    kind: str
    rect: fitz.Rect
    caption: str = ""


def _clean_text(value: str) -> str:
    value = value.replace("\u00ad", "")
    value = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", value)
    value = re.sub(r"\s*\n\s*", " ", value)
    return re.sub(r"[ \t]+", " ", value).strip()


def _block_text(block: dict) -> str:
    lines: list[str] = []
    for line in block.get("lines", []):
        lines.append("".join(span.get("text", "") for span in line.get("spans", [])))
    return _clean_text("\n".join(lines))


def _block_font(block: dict) -> tuple[float, bool]:
    sizes: list[float] = []
    bold = False
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            sizes.append(float(span.get("size", 0)))
            font = str(span.get("font", "")).lower()
            flags = int(span.get("flags", 0))
            bold = bold or "bold" in font or bool(flags & 16)
    return (max(sizes, default=0.0), bold)


def _overlap_ratio(a: fitz.Rect, b: fitz.Rect) -> float:
    intersection = a & b
    if intersection.is_empty:
        return 0.0
    area = max(1.0, a.get_area())
    return intersection.get_area() / area


def _clip_rect(rect: fitz.Rect, page_rect: fitz.Rect, pad: float = 5.0) -> fitz.Rect:
    expanded = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)
    return expanded & page_rect


def _caption_blocks(blocks: list[dict]) -> list[tuple[fitz.Rect, str]]:
    captions: list[tuple[fitz.Rect, str]] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        text = _block_text(block)
        if CAPTION_RE.match(text):
            captions.append((fitz.Rect(block["bbox"]), text))
    return captions


def _nearest_caption(
    rect: fitz.Rect, captions: list[tuple[fitz.Rect, str]], max_distance: float = 95.0
) -> tuple[fitz.Rect, str] | None:
    candidates: list[tuple[float, fitz.Rect, str]] = []
    for caption_rect, text in captions:
        horizontal_overlap = min(rect.x1, caption_rect.x1) - max(rect.x0, caption_rect.x0)
        if horizontal_overlap < min(rect.width, caption_rect.width) * 0.25:
            continue
        if caption_rect.y0 >= rect.y1:
            distance = caption_rect.y0 - rect.y1
        elif rect.y0 >= caption_rect.y1:
            distance = rect.y0 - caption_rect.y1
        else:
            distance = 0.0
        if distance <= max_distance:
            candidates.append((distance, caption_rect, text))
    if not candidates:
        return None
    _, caption_rect, text = min(candidates, key=lambda item: item[0])
    return caption_rect, text


def _merge_regions(regions: Iterable[Region]) -> list[Region]:
    merged: list[Region] = []
    for region in sorted(regions, key=lambda item: (item.rect.y0, item.rect.x0)):
        duplicate = None
        for existing in merged:
            if _overlap_ratio(region.rect, existing.rect) > 0.75 or _overlap_ratio(existing.rect, region.rect) > 0.75:
                duplicate = existing
                break
        if duplicate:
            duplicate.rect |= region.rect
            duplicate.caption = duplicate.caption or region.caption
            if duplicate.kind != "table" and region.kind == "table":
                duplicate.kind = "table"
        else:
            merged.append(region)
    return merged


def _detect_regions(page: fitz.Page, blocks: list[dict]) -> list[Region]:
    page_rect = page.rect
    captions = _caption_blocks(blocks)
    regions: list[Region] = []

    try:
        tables = page.find_tables()
        for table in tables.tables:
            rect = fitz.Rect(table.bbox)
            caption = _nearest_caption(rect, captions, 70.0)
            if caption:
                rect |= caption[0]
            regions.append(Region("table", _clip_rect(rect, page_rect, 7), caption[1] if caption else ""))
    except Exception:
        pass

    graphic_candidates: list[fitz.Rect] = []
    for block in blocks:
        if block.get("type") == 1:
            graphic_candidates.append(fitz.Rect(block["bbox"]))
    try:
        drawings = page.get_drawings()
        if drawings:
            graphic_candidates.extend(fitz.cluster_drawings(drawings, x_tolerance=4, y_tolerance=4))
    except Exception:
        pass

    page_area = page_rect.get_area()
    for rect in graphic_candidates:
        if rect.get_area() < page_area * 0.008 or rect.width < 45 or rect.height < 30:
            continue
        caption = _nearest_caption(rect, captions)
        if not caption and rect.get_area() < page_area * 0.07:
            continue
        combined = rect | caption[0] if caption else rect
        regions.append(Region("figure", _clip_rect(combined, page_rect, 7), caption[1] if caption else ""))

    for block in blocks:
        if block.get("type") != 0:
            continue
        text = _block_text(block)
        rect = fitz.Rect(block["bbox"])
        compact = len(text) < 420 and rect.height < 125
        centered = rect.x0 > page_rect.width * 0.16 and rect.x1 < page_rect.width * 0.84
        looks_numbered = bool(EQUATION_NUMBER_RE.search(text) and "=" in text)
        looks_mathematical = bool(centered and compact and MATH_MARKERS_RE.search(text) and len(text) < 220)
        if looks_numbered or looks_mathematical:
            regions.append(Region("equation", _clip_rect(rect, page_rect, 8), text))

    # Some PDFs flatten plots into many tiny vector paths. In that case the
    # graphics detector has no useful bounding box, but the caption still gives
    # us a reliable anchor. Preserve the area immediately above every unmatched
    # caption as an original-language raster fallback.
    for caption_rect, caption_text in captions:
        if any(region.caption == caption_text for region in regions):
            continue
        crosses_center = caption_rect.x0 < page_rect.width * 0.45 and caption_rect.x1 > page_rect.width * 0.55
        if crosses_center or caption_rect.width > page_rect.width * 0.58:
            x0, x1 = 34.0, page_rect.width - 34.0
        elif caption_rect.x0 < page_rect.width / 2:
            x0, x1 = 34.0, page_rect.width / 2 - 10.0
        else:
            x0, x1 = page_rect.width / 2 + 10.0, page_rect.width - 34.0

        previous_bottoms: list[float] = []
        column_rect = fitz.Rect(x0, 0, x1, caption_rect.y0)
        for block in blocks:
            if block.get("type") != 0:
                continue
            rect = fitz.Rect(block["bbox"])
            if rect.y1 > caption_rect.y0 or CAPTION_RE.match(_block_text(block)):
                continue
            if (rect & column_rect).width >= min(rect.width, column_rect.width) * 0.35:
                previous_bottoms.append(rect.y1)
        top = max(previous_bottoms, default=caption_rect.y0 - 190.0) + 3.0
        top = max(36.0, caption_rect.y0 - 270.0, top)
        if caption_rect.y1 - top < 72.0:
            top = max(36.0, caption_rect.y0 - 150.0)
        kind = "table" if caption_text.lower().startswith(("table", "algorithm")) else "figure"
        regions.append(
            Region(
                kind,
                _clip_rect(fitz.Rect(x0, top, x1, caption_rect.y1), page_rect, 5),
                caption_text,
            )
        )

    return _merge_regions(regions)


def _render_region(page: fitz.Page, region: Region, asset_path: Path) -> None:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2.4, 2.4), clip=region.rect, alpha=False)
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(asset_path)


def _render_source_page(page: fitz.Page, asset_path: Path) -> None:
    """Render a compact page facsimile used when structural detection misses an object."""
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(asset_path, jpg_quality=84)


def _reading_key(rect: fitz.Rect, page_width: float) -> tuple[int, float, float]:
    crosses_center = rect.x0 < page_width * 0.46 and rect.x1 > page_width * 0.54
    if crosses_center or rect.width > page_width * 0.62:
        return (0, rect.y0, rect.x0)
    column = 1 if rect.x0 < page_width / 2 else 2
    return (column, rect.y0, rect.x0)


def _classify_text(text: str, font_size: float, bold: bool, median_size: float, in_references: bool) -> tuple[str, int | None]:
    if in_references:
        return "reference", None
    if REFERENCE_HEADING_RE.match(text):
        return "heading", 1
    if len(text) < 150 and (bold or font_size >= median_size * 1.16):
        numbered = re.match(r"^(\d+(?:\.\d+)*)\s+", text)
        if numbered:
            return "heading", min(4, numbered.group(1).count(".") + 1)
        return "heading", 2
    return "paragraph", None


def extract_document(source: Path, work_dir: Path, output_json: Path) -> DocumentIR:
    source = source.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = work_dir / "assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    pdf = fitz.open(source)

    pages: list[PageIR] = []
    title = source.stem
    authors = ""
    in_references = False

    for page_index, page in enumerate(pdf):
        page_number = page_index + 1
        source_page_name = f"page-{page_number:03d}-original.jpg"
        _render_source_page(page, assets_dir / source_page_name)
        page_dict = page.get_text("dict", sort=False)
        blocks = page_dict.get("blocks", [])
        text_blocks = [block for block in blocks if block.get("type") == 0 and _block_text(block)]
        font_sizes = sorted(size for block in text_blocks if (size := _block_font(block)[0]) > 0)
        median_size = font_sizes[len(font_sizes) // 2] if font_sizes else 10.0
        regions = _detect_regions(page, blocks)

        ordered: list[tuple[fitz.Rect, DocumentItem]] = []
        region_counter = 0
        for region in regions:
            region_counter += 1
            asset_name = f"page-{page_number:03d}-{region.kind}-{region_counter:02d}.png"
            asset_path = assets_dir / asset_name
            _render_region(page, region, asset_path)
            ordered.append(
                (
                    region.rect,
                    DocumentItem(
                        id=f"p{page_number}-{region.kind}-{region_counter}",
                        kind=region.kind,  # type: ignore[arg-type]
                        page=page_number,
                        order=0,
                        original=region.caption,
                        asset=f"assets/{asset_name}",
                        caption=region.caption or None,
                        bbox=BBox.from_value(region.rect),
                    ),
                )
            )

        text_counter = 0
        for block in text_blocks:
            text = _block_text(block)
            rect = fitz.Rect(block["bbox"])
            if rect.y0 < 34 or rect.y1 > page.rect.height - 28:
                continue
            if any(_overlap_ratio(rect, region.rect) > 0.62 for region in regions):
                continue
            if CAPTION_RE.match(text):
                continue
            font_size, bold = _block_font(block)
            kind, level = _classify_text(text, font_size, bold, median_size, in_references)
            if REFERENCE_HEADING_RE.match(text):
                in_references = True
            text_counter += 1
            ordered.append(
                (
                    rect,
                    DocumentItem(
                        id=f"p{page_number}-text-{text_counter}",
                        kind=kind,  # type: ignore[arg-type]
                        page=page_number,
                        order=0,
                        original=text,
                        level=level,
                        bbox=BBox.from_value(rect),
                    ),
                )
            )

        ordered.sort(key=lambda pair: _reading_key(pair[0], page.rect.width))
        for order, (_, item) in enumerate(ordered, start=1):
            item.order = order

        page_ir = PageIR(
            number=page_number,
            width=round(page.rect.width, 2),
            height=round(page.rect.height, 2),
            source_image=f"assets/{source_page_name}",
            items=[item for _, item in ordered],
        )
        pages.append(page_ir)

        if page_index == 0:
            candidates = [item for item in page_ir.items if item.kind in {"heading", "paragraph"}]
            if candidates:
                title = max(candidates[:8], key=lambda item: len(item.original) if len(item.original) < 220 else 0).original
                for candidate in candidates:
                    if candidate.original != title and 2 <= len(candidate.original.split()) <= 30:
                        authors = candidate.original
                        break

    document = DocumentIR(
        version=1,
        source_file=source.name,
        source_sha256=digest,
        title=title,
        authors=authors,
        page_count=len(pdf),
        pages=pages,
    )
    save_document(document, output_json)
    pdf.close()
    return document
