from __future__ import annotations

import hashlib
import html
import json
import shutil
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .markdown_render import document_ir_to_markdown
from .models import DocumentIR


ARXIV_HTML_TEMPLATE = "arxiv-paper.html.j2"
# Bump this when renderer behavior outside the template changes the offline artifact.
ARXIV_HTML_RENDERER_VERSION = "2"


def arxiv_html_artifact_version() -> str:
    """Return the version of the self-contained arXiv HTML artifact."""

    template = Path(__file__).parent / "templates" / ARXIV_HTML_TEMPLATE
    template_digest = hashlib.sha256(template.read_bytes()).hexdigest()[:12]
    return f"{ARXIV_HTML_RENDERER_VERSION}-{template_digest}"


def _template_environment() -> Environment:
    template_dir = Path(__file__).parent / "templates"
    return Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_document(
    document: DocumentIR,
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

    template = _template_environment().get_template("paper.html.j2")
    html_text = template.render(document=document)
    index_path = output_dir / "index.html"
    index_path.write_text(html_text, encoding="utf-8")
    (output_dir / "index.md").write_text(
        document_ir_to_markdown(document, asset_base=work_dir),
        encoding="utf-8",
    )
    (output_dir / "document.json").write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return index_path


def create_bundle(output_dir: Path, zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = zip_path.with_suffix(f"{zip_path.suffix}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output_dir.rglob("*")):
                if path.is_file() and path.resolve() not in {
                    temporary.resolve(),
                    zip_path.resolve(),
                }:
                    archive.write(path, path.relative_to(output_dir))
        temporary.replace(zip_path)
    finally:
        temporary.unlink(missing_ok=True)
    return zip_path
