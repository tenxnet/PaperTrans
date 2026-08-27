from __future__ import annotations

import argparse
import json
from pathlib import Path

from .extract import extract_document
from .io import load_document
from .render import create_bundle, render_document
from .structure import analyze_layout, extract_layout_evidence, render_visual_objects, write_visual_qa
from .translate import translate_document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="papertrans")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="Extract a PDF into DocumentIR")
    extract.add_argument("source", type=Path)
    extract.add_argument("--work-dir", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)

    translate = subparsers.add_parser("translate", help="Translate a DocumentIR using Codex")
    translate.add_argument("document", type=Path)
    translate.add_argument("--repo-root", type=Path, default=Path.cwd())
    translate.add_argument("--max-characters", type=int, default=14000)

    render = subparsers.add_parser("render", help="Render an HTML bundle")
    render.add_argument("document", type=Path)
    render.add_argument("--work-dir", type=Path, required=True)
    render.add_argument("--output-dir", type=Path, required=True)
    render.add_argument("--source-pdf", type=Path)
    render.add_argument("--zip", type=Path)

    pipeline = subparsers.add_parser("pipeline", help="Run extract, translate, and render")
    pipeline.add_argument("source", type=Path)
    pipeline.add_argument("--slug", required=True)
    pipeline.add_argument("--output-root", type=Path, default=Path("output"))
    pipeline.add_argument("--repo-root", type=Path, default=Path.cwd())
    pipeline.add_argument("--skip-translation", action="store_true")
    pipeline.add_argument("--max-characters", type=int, default=14000)

    layout_extract = subparsers.add_parser("layout-extract", help="Extract raw PDF geometry and page images")
    layout_extract.add_argument("source", type=Path)
    layout_extract.add_argument("--work-dir", type=Path, required=True)
    layout_extract.add_argument("--output", type=Path, required=True)

    structure = subparsers.add_parser("structure", help="Analyze PDF semantics with page vision and Codex")
    structure.add_argument("evidence", type=Path)
    structure.add_argument("--work-dir", type=Path, required=True)
    structure.add_argument("--output", type=Path, required=True)
    structure.add_argument("--repo-root", type=Path, default=Path.cwd())
    structure.add_argument("--batch-size", type=int, default=2)
    structure.add_argument("--max-pages", type=int)
    structure.add_argument("--source-pdf", type=Path)
    structure.add_argument("--assets-dir", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "extract":
        document = extract_document(args.source, args.work_dir, args.output)
        print(json.dumps({"pages": document.page_count, "items": sum(1 for _ in document.iter_items())}))
        return

    if args.command == "translate":
        document = load_document(args.document)
        translate_document(
            document,
            args.document,
            args.repo_root.resolve(),
            max_characters=args.max_characters,
        )
        print(json.dumps({"status": document.status, "translated": document.translated_count()}))
        return

    if args.command == "render":
        document = load_document(args.document)
        index = render_document(document, args.work_dir, args.output_dir, args.source_pdf)
        if args.zip:
            create_bundle(args.output_dir, args.zip)
        print(index)
        return

    if args.command == "layout-extract":
        evidence = extract_layout_evidence(args.source, args.work_dir, args.output)
        print(json.dumps({"pages": evidence["pageCount"], "output": str(args.output)}))
        return

    if args.command == "structure":
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        structured = analyze_layout(
            evidence,
            args.work_dir,
            args.output,
            args.repo_root.resolve(),
            batch_size=args.batch_size,
            max_pages=args.max_pages,
        )
        if args.source_pdf and args.assets_dir:
            objects = render_visual_objects(args.source_pdf, structured, args.assets_dir)
            (args.output.parent / "visual-objects.json").write_text(
                json.dumps(objects, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            write_visual_qa(objects, args.output.parent / "visual-qa.html")
        print(
            json.dumps(
                {
                    "pages": len(structured["pages"]),
                    "sections": len(structured["sections"]),
                    "visualObjects": sum(len(page["visualObjects"]) for page in structured["pages"]),
                }
            )
        )
        return

    if args.command == "pipeline":
        repo_root = args.repo_root.resolve()
        output_root = args.output_root.resolve()
        work_dir = output_root / args.slug / "work"
        document_path = work_dir / "document.json"
        publication_dir = output_root / args.slug / "html"
        document = extract_document(args.source, work_dir, document_path)
        if not args.skip_translation:
            document = translate_document(
                document,
                document_path,
                repo_root,
                max_characters=args.max_characters,
            )
        index = render_document(document, work_dir, publication_dir, args.source)
        bundle = create_bundle(publication_dir, output_root / args.slug / f"{args.slug}-html.zip")
        print(json.dumps({"html": str(index), "bundle": str(bundle), "status": document.status}, ensure_ascii=False))


if __name__ == "__main__":
    main()
