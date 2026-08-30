from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .markdown_render import semantic_v3_to_markdown_blocks, serialize_markdown


CITATION_GROUP_RE = re.compile(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]")
EXTERNAL_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
MARKDOWN_ANCHOR_RE = re.compile(r'<a\s+id="([^"]+)"\s*></a>')
MARKDOWN_INTERNAL_LINK_RE = re.compile(r"\]\(#([^)]+)\)")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _validate_semantic_markdown(
    markdown: str,
    output_dir: Path,
    *,
    block_count: int,
) -> dict[str, Any]:
    """Validate the portable Markdown sibling emitted for semantic PDFs."""

    anchors = MARKDOWN_ANCHOR_RE.findall(markdown)
    anchor_set = set(anchors)
    duplicate_anchors = sorted(
        anchor for anchor in anchor_set if anchors.count(anchor) > 1
    )
    unresolved_links = sorted(
        {
            unquote(target)
            for target in MARKDOWN_INTERNAL_LINK_RE.findall(markdown)
            if unquote(target) not in anchor_set
        }
    )
    missing_assets: list[str] = []
    for destination in MARKDOWN_IMAGE_RE.findall(markdown):
        parsed = urlsplit(destination)
        if parsed.scheme or parsed.netloc:
            continue
        relative = unquote(parsed.path).lstrip("/")
        candidate = (output_dir / relative).resolve()
        root = output_dir.resolve()
        if (
            not relative
            or candidate == root
            or root not in candidate.parents
            or not candidate.is_file()
        ):
            missing_assets.append(destination)
    qa = {
        "schemaVersion": 1,
        "status": "passed",
        "blocks": block_count,
        "anchors": len(anchors),
        "duplicateAnchors": duplicate_anchors,
        "unresolvedInternalLinks": unresolved_links,
        "missingLocalAssets": sorted(set(missing_assets)),
    }
    if (
        block_count == 0
        or duplicate_anchors
        or unresolved_links
        or missing_assets
    ):
        qa["status"] = "failed"
    return qa


def _link_filter(document: dict[str, Any]):
    reference_ids: set[str] = set()
    object_labels: dict[str, str] = {}
    for section in document["sections"]:
        for item in section["content"]:
            value = item["value"]
            if item["type"] == "unit" and value["kind"] == "reference" and value.get("referenceLabel"):
                reference_ids.add(str(value["referenceLabel"]))
            if item["type"] == "visual" and value.get("label"):
                object_labels[str(value["label"])] = f"visual-{value['objectId']}"

    def link_text(text: str) -> Markup:
        external_anchors: list[str] = []

        def external_url(match: re.Match[str]) -> str:
            value = match.group(0)
            trailing = ""
            while value and value[-1] in ".,;:":
                trailing = value[-1] + trailing
                value = value[:-1]
            while value.endswith(")") and value.count("(") < value.count(")"):
                trailing = ")" + trailing
                value = value[:-1]
            token = f"\x00papertrans-external-{len(external_anchors)}\x00"
            escaped_value = html.escape(value, quote=True)
            external_anchors.append(
                f'<a class="external" href="{escaped_value}" '
                f'rel="noopener noreferrer">{escaped_value}</a>'
            )
            return token + trailing

        tokenized = EXTERNAL_URL_RE.sub(external_url, text or "")
        rendered = html.escape(tokenized)
        for label in sorted(object_labels, key=len, reverse=True):
            target = object_labels[label]
            escaped_label = html.escape(label)
            rendered = re.sub(
                rf"(?<![\w-]){re.escape(escaped_label)}(?![\w-])",
                f'<a class="xref" href="#{html.escape(target)}">{escaped_label}</a>',
                rendered,
            )

        def citation(match: re.Match[str]) -> str:
            content = match.group(1)
            parts = re.split(r"(\d+)", content)
            linked = ""
            for part in parts:
                if part.isdigit() and part in reference_ids:
                    linked += f'<a href="#ref-{part}">{part}</a>'
                else:
                    linked += part
            return f'<span class="citation">[{linked}]</span>'

        rendered = CITATION_GROUP_RE.sub(citation, rendered)
        for index, anchor in enumerate(external_anchors):
            rendered = rendered.replace(
                f"\x00papertrans-external-{index}\x00", anchor
            )
        return Markup(rendered)

    return link_text


def render_semantic_document(
    document: dict[str, Any],
    work_dir: Path,
    output_dir: Path,
    source_pdf: Path | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_assets = output_dir / "assets"
    if output_assets.exists():
        shutil.rmtree(output_assets)
    source_assets = work_dir / "assets"
    if source_assets.exists():
        shutil.copytree(source_assets, output_assets)
    if source_pdf:
        shutil.copy2(source_pdf, output_dir / "source.pdf")

    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["academic_links"] = _link_filter(document)
    template = environment.get_template("semantic-paper.html.j2")
    text = template.render(document=document)
    index_path = output_dir / "index.html"
    index_path.write_text(text, encoding="utf-8")
    markdown_blocks = semantic_v3_to_markdown_blocks(document, asset_base=work_dir)
    markdown = serialize_markdown(markdown_blocks)
    (output_dir / "index.md").write_text(markdown, encoding="utf-8")
    markdown_qa = _validate_semantic_markdown(
        markdown,
        output_dir,
        block_count=len(markdown_blocks),
    )
    (output_dir / "markdown-qa.json").write_text(
        json.dumps(markdown_qa, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if markdown_qa["status"] != "passed":
        raise RuntimeError(f"rendered Markdown QA failed: {markdown_qa}")
    (output_dir / "semantic-document.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return index_path
