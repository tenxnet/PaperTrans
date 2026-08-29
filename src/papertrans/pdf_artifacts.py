from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pymupdf as fitz
from bs4 import BeautifulSoup

from .semantic import TRANSLATABLE_ROLES, iter_translatable_units
from .structure import is_near_certain_blank_pixmap


PDF_JOB_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _source_sha256(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_text(unit: dict[str, Any] | None, fallback: str) -> str:
    if not unit:
        return fallback
    return str(unit.get("japanese") or unit.get("original") or fallback).strip()


def _authors(document: dict[str, Any] | None) -> list[str]:
    if not document:
        return []
    values: list[str] = []
    for author in document.get("frontMatter", {}).get("authors", []):
        text = str(author.get("original", "")).strip()
        if text and text not in values:
            values.append(text)
    return values


def _translation_chunks(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not document:
        return []
    chunks: list[dict[str, Any]] = []
    title = document.get("title")
    if title:
        chunks.append(
            {
                "chunkId": "front-matter",
                "status": "completed" if str(title.get("japanese", "")).strip() else "pending",
                "unitIds": [str(title.get("id", "paper-title"))],
            }
        )
    for index, section in enumerate(document.get("sections", []), start=1):
        units = [section.get("title")]
        units.extend(
            item.get("value")
            for item in section.get("content", [])
            if item.get("type") == "unit"
            and item.get("value", {}).get("kind") in TRANSLATABLE_ROLES
        )
        units = [unit for unit in units if unit]
        if not units:
            continue
        chunks.append(
            {
                "chunkId": f"section-{index:03d}",
                "status": (
                    "completed"
                    if all(str(unit.get("japanese", "")).strip() for unit in units)
                    else "pending"
                ),
                "unitIds": [str(unit.get("id", "")) for unit in units],
            }
        )
    return chunks


def write_pdf_job_manifest(
    path: Path,
    *,
    slug: str,
    source: Path,
    status: str,
    pdf_parser: str,
    structure_mode: str,
    document: dict[str, Any] | None = None,
    started_at: str | None = None,
    skip_translation: bool = False,
    error: str | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Write the source-neutral manifest consumed by the local Web library."""

    now = utc_now_iso()
    created_at = started_at or now
    title = _display_text(document.get("title") if document else None, source.stem)
    chunks = _translation_chunks(document)
    manifest: dict[str, Any] = {
        "schemaVersion": PDF_JOB_SCHEMA_VERSION,
        "jobId": slug,
        "sourceType": "pdf",
        "status": status,
        "provider": "none" if skip_translation else "codex-cli",
        "paper": {
            "requestedArxivId": "",
            "resolvedArxivId": "",
            "title": title,
            "sourceUrl": "",
            "authors": _authors(document),
            "publishedAt": None,
        },
        "source": {
            "type": "pdf",
            "fileName": source.name,
            "sha256": source_sha256 or _source_sha256(source),
        },
        "settings": {
            "targetLanguage": "ja",
            "pdfParser": pdf_parser,
            "structureMode": structure_mode,
        },
        "chunks": chunks,
        "createdAt": created_at,
        "updatedAt": now,
        "finalizedAt": now if status in {"completed", "needs_review", "failed"} else None,
        "artifacts": {
            "html": "html/index.html",
            "qa": "html/qa.json",
            "bundle": f"{slug}-html.zip",
            "sourcePdf": "html/source.pdf",
        },
    }
    if error:
        manifest["error"] = {"message": error[:2000]}
    _atomic_json(path, manifest)
    return manifest


def _local_asset_path(publication_dir: Path, raw_url: str) -> Path | None:
    parsed = urlsplit(raw_url)
    if parsed.scheme or parsed.netloc or raw_url.startswith(("#", "data:")):
        return None
    relative = unquote(parsed.path).lstrip("/")
    if not relative:
        return None
    candidate = (publication_dir / relative).resolve()
    root = publication_dir.resolve()
    if candidate == root or root not in candidate.parents:
        return publication_dir / "__unsafe_asset_path__"
    return candidate


def _emitted_blank_visual_assets(
    document: dict[str, Any], publication_dir: Path
) -> list[dict[str, Any]]:
    blank_assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for visual in document.get("visualObjects", []):
        raw_asset = str(visual.get("asset", "")).strip()
        if not raw_asset or raw_asset in seen:
            continue
        seen.add(raw_asset)
        asset_path = _local_asset_path(publication_dir, raw_asset)
        if asset_path is None or not asset_path.is_file():
            continue
        try:
            pixmap = fitz.Pixmap(str(asset_path))
        except (RuntimeError, ValueError):
            # Missing/corrupt assets are already handled by the normal artifact QA.
            continue
        if is_near_certain_blank_pixmap(pixmap):
            blank_assets.append(
                {
                    "objectId": str(visual.get("objectId", "")),
                    "pageNumber": int(visual.get("pageNumber", 0)),
                    "asset": raw_asset,
                }
            )
    return blank_assets


def write_semantic_pdf_qa(
    document: dict[str, Any],
    publication_dir: Path,
    *,
    pdf_parser: str,
    evidence: dict[str, Any] | None = None,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the portable HTML artifact and emit the common QA shape."""

    index = publication_dir / "index.html"
    unresolved_links: list[str] = []
    missing_assets: list[str] = []
    if index.exists():
        soup = BeautifulSoup(index.read_text(encoding="utf-8"), "html.parser")
        known_ids = {str(tag.get("id")) for tag in soup.find_all(id=True)}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", ""))
            if href.startswith("#") and len(href) > 1 and unquote(href[1:]) not in known_ids:
                unresolved_links.append(href)
        for tag, attribute in (("img", "src"), ("object", "data"), ("source", "src")):
            for element in soup.find_all(tag):
                raw_url = str(element.get(attribute, ""))
                asset = _local_asset_path(publication_dir, raw_url)
                if asset is not None and not asset.is_file() and raw_url not in missing_assets:
                    missing_assets.append(raw_url)
    else:
        missing_assets.append("index.html")

    visuals = document.get("visualObjects", [])
    emitted_blank_visual_assets = _emitted_blank_visual_assets(document, publication_dir)
    filtered_blank_visuals = list(
        (structure or {}).get("renderDiagnostics", {}).get("filteredBlankVisuals", [])
    )
    references = 0
    for section in document.get("sections", []):
        references += sum(
            item.get("type") == "unit" and item.get("value", {}).get("kind") == "reference"
            for item in section.get("content", [])
        )
    translatable = list(iter_translatable_units(document))
    translated = sum(bool(str(unit.get("japanese", "")).strip()) for unit in translatable)
    semantic_body_units = [
        item.get("value", {})
        for section in document.get("sections", [])
        for item in section.get("content", [])
        if item.get("type") == "unit"
        and item.get("value", {}).get("kind") in TRANSLATABLE_ROLES | {"reference"}
        and str(item.get("value", {}).get("original", "")).strip()
    ]
    evidence_pages = evidence.get("pages", []) if evidence else []
    invalid_geometry_pages = [
        int(page.get("pageNumber", 0))
        for page in evidence_pages
        if float(page.get("widthPdf", 0)) <= 1 or float(page.get("heightPdf", 0)) <= 1
    ]
    empty_text_pages = [
        int(page.get("pageNumber", 0))
        for page in evidence_pages
        if not page.get("blocks")
    ]
    all_text_pages_empty = bool(evidence_pages) and len(empty_text_pages) == len(evidence_pages)
    title_source_block_ids = {
        block_id.strip()
        for block_id in document.get("title", {}).get("sourceBlockIds", [])
        if isinstance(block_id, str) and block_id.strip()
    }
    missing_title_source = pdf_parser == "docling" and not title_source_block_ids
    represented_blocks: set[str] = set(title_source_block_ids)
    for collection in ("authors", "affiliations", "metadata"):
        for value in document.get("frontMatter", {}).get(collection, []):
            represented_blocks.update(str(item) for item in value.get("sourceBlockIds", []))
            if value.get("blockId"):
                represented_blocks.add(str(value["blockId"]))
    for section in document.get("sections", []):
        represented_blocks.update(
            str(item) for item in section.get("title", {}).get("sourceBlockIds", [])
        )
        for item in section.get("content", []):
            represented_blocks.update(
                str(block_id) for block_id in item.get("value", {}).get("sourceBlockIds", [])
            )
    for visual in document.get("visualObjects", []):
        represented_blocks.update(str(item) for item in visual.get("captionBlockIds", []))
    semantic_roles = {
        "title",
        "author",
        "affiliation",
        "metadata",
        "heading",
        "abstract",
        "paragraph",
        "list_item",
        "footnote",
        "reference",
        "caption",
    }
    expected_blocks = {
        str(assignment.get("blockId"))
        for page in (structure or {}).get("pages", [])
        for assignment in page.get("blockAssignments", [])
        if not assignment.get("hidden") and assignment.get("role") in semantic_roles
    }
    missing_semantic_blocks = sorted(expected_blocks - represented_blocks)
    embedded_visual_text_blocks = {
        str(block.get("blockId"))
        for page in evidence_pages
        for block in page.get("blocks", [])
        if block.get("suppressedVisualText") and block.get("blockId")
    }
    structure_visual_text_blocks = {
        str(assignment.get("blockId"))
        for page in (structure or {}).get("pages", [])
        for assignment in page.get("blockAssignments", [])
        if assignment.get("suppressedVisualText") and assignment.get("blockId")
    }
    structurally_visible_visual_text = {
        str(assignment.get("blockId"))
        for page in (structure or {}).get("pages", [])
        for assignment in page.get("blockAssignments", [])
        if assignment.get("suppressedVisualText")
        and not assignment.get("hidden")
        and assignment.get("blockId")
    }
    embedded_visual_text_blocks.update(structure_visual_text_blocks)
    leaked_visual_text_blocks = sorted(
        (embedded_visual_text_blocks & represented_blocks)
        | structurally_visible_visual_text
    )
    caption_block_usage: dict[str, list[str]] = {}
    visuals_by_page: dict[int, list[dict[str, Any]]] = {}
    for page in (structure or {}).get("pages", []):
        page_number = int(page.get("pageNumber", 0))
        page_visuals = list(page.get("visualObjects", []))
        visuals_by_page[page_number] = page_visuals
        for visual in page_visuals:
            object_id = str(visual.get("objectId") or "")
            for block_id in visual.get("captionBlockIds", []):
                caption_block_usage.setdefault(str(block_id), []).append(object_id)
    duplicate_visual_caption_blocks = sorted(
        block_id
        for block_id, object_ids in caption_block_usage.items()
        if len(set(object_ids)) > 1
    )
    unattached_visual_caption_blocks = sorted(
        str(assignment.get("blockId"))
        for page in (structure or {}).get("pages", [])
        for assignment in page.get("blockAssignments", [])
        if assignment.get("visualCaptionCandidate")
        and not assignment.get("associatedVisualCaption")
        and assignment.get("blockId")
    )
    evidence_blocks_by_page = {
        int(page.get("pageNumber", 0)): {
            str(block.get("blockId")): block
            for block in page.get("blocks", [])
            if block.get("blockId")
        }
        for page in evidence_pages
    }
    visible_visual_overlap_blocks: set[str] = set()
    overlap_roles = {"abstract", "list_item", "paragraph", "reference"}
    for page in (structure or {}).get("pages", []):
        page_number = int(page.get("pageNumber", 0))
        page_blocks = evidence_blocks_by_page.get(page_number, {})
        visual_bboxes = [
            visual.get("bboxNormalized", [])
            for visual in visuals_by_page.get(page_number, [])
        ]
        for assignment in page.get("blockAssignments", []):
            block_id = str(assignment.get("blockId") or "")
            block = page_blocks.get(block_id)
            if (
                not block_id
                or block is None
                or assignment.get("hidden")
                or assignment.get("role") not in overlap_roles
                or assignment.get("associatedVisualCaption")
            ):
                continue
            bbox = block.get("bboxNormalized", [])
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            area = max(0.0, float(bbox[2]) - float(bbox[0])) * max(
                0.0, float(bbox[3]) - float(bbox[1])
            )
            if area <= 0:
                continue
            for visual_bbox in visual_bboxes:
                if not isinstance(visual_bbox, list) or len(visual_bbox) != 4:
                    continue
                intersection_width = max(
                    0.0,
                    min(float(bbox[2]), float(visual_bbox[2]))
                    - max(float(bbox[0]), float(visual_bbox[0])),
                )
                intersection_height = max(
                    0.0,
                    min(float(bbox[3]), float(visual_bbox[3]))
                    - max(float(bbox[1]), float(visual_bbox[1])),
                )
                if intersection_width * intersection_height / area >= 0.4:
                    visible_visual_overlap_blocks.add(block_id)
                    break
    visible_visual_overlap_block_ids = sorted(visible_visual_overlap_blocks)
    docling_diagnostics = (structure or {}).get("doclingDiagnostics", {})
    suppressed_internal_caption_block_ids = sorted(
        str(value)
        for value in docling_diagnostics.get(
            "suppressedInternalCaptionBlockIds", []
        )
        if value
    )
    overlarge_visual_object_ids = sorted(
        str(value)
        for value in docling_diagnostics.get("overlargeVisualObjectIds", [])
        if value
    )
    missing_caption_text_override_object_ids = sorted(
        str(value)
        for value in docling_diagnostics.get(
            "missingCaptionTextOverrideObjectIds", []
        )
        if value
    )
    unmerged_multi_panel_visual_object_ids = sorted(
        str(value)
        for value in docling_diagnostics.get(
            "unmergedMultiPanelVisualObjectIds", []
        )
        if value
    )
    dangling_parent_section_ids = sorted(
        str(value)
        for value in docling_diagnostics.get("danglingParentSectionIds", [])
        if value
    )
    blank_visible_heading_block_ids = sorted(
        str(value)
        for value in docling_diagnostics.get("blankVisibleHeadingBlockIds", [])
        if value
    )
    suppressed_blank_heading_block_ids = sorted(
        str(value)
        for value in docling_diagnostics.get(
            "suppressedBlankHeadingBlockIds", []
        )
        if value
    )
    unabsorbed_panel_heading_block_ids = sorted(
        str(value)
        for value in docling_diagnostics.get(
            "unabsorbedPanelHeadingBlockIds", []
        )
        if value
    )
    qa = {
        "schemaVersion": PDF_JOB_SCHEMA_VERSION,
        "status": (
            "passed"
            if not unresolved_links
            and not missing_assets
            and not invalid_geometry_pages
            and not all_text_pages_empty
            and not missing_title_source
            and not missing_semantic_blocks
            and not leaked_visual_text_blocks
            and not duplicate_visual_caption_blocks
            and not unattached_visual_caption_blocks
            and not visible_visual_overlap_block_ids
            and not suppressed_internal_caption_block_ids
            and not overlarge_visual_object_ids
            and not missing_caption_text_override_object_ids
            and not unmerged_multi_panel_visual_object_ids
            and not dangling_parent_section_ids
            and not blank_visible_heading_block_ids
            and not unabsorbed_panel_heading_block_ids
            and not emitted_blank_visual_assets
            and bool(semantic_body_units)
            else "failed"
        ),
        "sourceType": "pdf",
        "parser": pdf_parser,
        "output": {
            "figures": sum(visual.get("kind") == "figure" for visual in visuals),
            "tables": sum(visual.get("kind") == "table" for visual in visuals),
            "visibleMath": sum(visual.get("kind") == "equation" for visual in visuals),
            "bibliographyEntries": references,
            "sections": len(document.get("sections", [])),
            "translationUnits": len(translatable),
            "translatedUnits": translated,
            "semanticBodyUnits": len(semantic_body_units),
            "pages": len(evidence_pages) if evidence else int(document.get("sourcePageCount", 0)),
        },
        "invalidPageGeometry": invalid_geometry_pages,
        "emptyTextPages": empty_text_pages,
        "allTextPagesEmpty": all_text_pages_empty,
        "missingTitleSource": missing_title_source,
        "semanticSourceBlocks": len(expected_blocks),
        "representedSemanticBlocks": len(expected_blocks & represented_blocks),
        "missingSemanticBlocks": missing_semantic_blocks,
        "visualTextDetected": len(embedded_visual_text_blocks),
        "visualTextSuppressed": len(embedded_visual_text_blocks)
        - len(leaked_visual_text_blocks),
        "leakedVisualTextBlockIds": leaked_visual_text_blocks,
        "duplicateVisualCaptionBlockIds": duplicate_visual_caption_blocks,
        "unattachedVisualCaptionBlockIds": unattached_visual_caption_blocks,
        "visibleVisualOverlapBlockIds": visible_visual_overlap_block_ids,
        "suppressedInternalCaptionBlockIds": suppressed_internal_caption_block_ids,
        "overlargeVisualObjectIds": overlarge_visual_object_ids,
        "missingCaptionTextOverrideObjectIds": (
            missing_caption_text_override_object_ids
        ),
        "unmergedMultiPanelVisualObjectIds": (
            unmerged_multi_panel_visual_object_ids
        ),
        "danglingParentSectionIds": dangling_parent_section_ids,
        "blankVisibleHeadingBlockIds": blank_visible_heading_block_ids,
        "suppressedBlankHeadingBlockIds": suppressed_blank_heading_block_ids,
        "unabsorbedPanelHeadingBlockIds": unabsorbed_panel_heading_block_ids,
        "emittedBlankVisualAssets": emitted_blank_visual_assets,
        "filteredBlankVisuals": filtered_blank_visuals,
        "unresolvedInternalLinks": len(unresolved_links),
        "unresolvedInternalLinkTargets": unresolved_links,
        "missingLocalAssets": missing_assets,
        "warnings": list(document.get("warnings", [])),
    }
    _atomic_json(publication_dir / "qa.json", qa)
    return qa
