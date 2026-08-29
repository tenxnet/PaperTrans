from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from .semantic import TRANSLATABLE_ROLES, iter_translatable_units


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
    represented_blocks: set[str] = set(document.get("title", {}).get("sourceBlockIds", []))
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
    qa = {
        "schemaVersion": PDF_JOB_SCHEMA_VERSION,
        "status": (
            "passed"
            if not unresolved_links
            and not missing_assets
            and not invalid_geometry_pages
            and not all_text_pages_empty
            and not missing_semantic_blocks
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
        "semanticSourceBlocks": len(expected_blocks),
        "representedSemanticBlocks": len(expected_blocks & represented_blocks),
        "missingSemanticBlocks": missing_semantic_blocks,
        "unresolvedInternalLinks": len(unresolved_links),
        "unresolvedInternalLinkTargets": unresolved_links,
        "missingLocalAssets": missing_assets,
        "warnings": list(document.get("warnings", [])),
    }
    _atomic_json(publication_dir / "qa.json", qa)
    return qa
