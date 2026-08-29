from __future__ import annotations

import argparse
import json
from pathlib import Path

from .arxiv_html import run_arxiv_html_pipeline
from .chatgpt_worker import MCPTranslationStore
from .deterministic_structure import analyze_layout_deterministic, evaluate_structure
from .docling_adapter import extract_docling_semantics
from .extract import extract_document
from .hybrid_structure import refine_structure_with_llm
from .io import load_document
from .metrics import record_stage, utc_now
from .pdf_artifacts import write_pdf_job_manifest, write_semantic_pdf_qa
from .pdf_benchmark import run_pdf_parser_benchmark
from .render import create_bundle, render_document
from .semantic import (
    build_semantic_document,
    load_semantic_document,
    merge_semantic_translations,
    save_semantic_document,
)
from .semantic_render import render_semantic_document
from .semantic_translate import translate_semantic_document
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
    layout_extract.add_argument("--metrics", type=Path)

    structure = subparsers.add_parser("structure", help="Analyze PDF semantics with page vision and Codex")
    structure.add_argument("evidence", type=Path)
    structure.add_argument("--work-dir", type=Path, required=True)
    structure.add_argument("--output", type=Path, required=True)
    structure.add_argument("--repo-root", type=Path, default=Path.cwd())
    structure.add_argument("--batch-size", type=int, default=2)
    structure.add_argument("--max-pages", type=int)
    structure.add_argument("--source-pdf", type=Path)
    structure.add_argument("--assets-dir", type=Path)
    structure.add_argument("--model", default="gpt-5.6-sol")
    structure.add_argument("--reasoning-effort", default="high")
    structure.add_argument("--metrics", type=Path)

    deterministic_structure = subparsers.add_parser(
        "deterministic-structure",
        help="Build a fast structure proposal from PDF geometry without an LLM",
    )
    deterministic_structure.add_argument("evidence", type=Path)
    deterministic_structure.add_argument("--output", type=Path, required=True)
    deterministic_structure.add_argument("--max-pages", type=int)
    deterministic_structure.add_argument("--source-pdf", type=Path)
    deterministic_structure.add_argument("--assets-dir", type=Path)
    deterministic_structure.add_argument("--metrics", type=Path)

    structure_evaluate = subparsers.add_parser(
        "structure-evaluate", help="Compare a structure proposal with a reviewed structure file"
    )
    structure_evaluate.add_argument("gold", type=Path)
    structure_evaluate.add_argument("candidate", type=Path)
    structure_evaluate.add_argument("--output", type=Path)

    hybrid_structure = subparsers.add_parser(
        "hybrid-structure",
        help="Review only low-confidence deterministic pages with Codex",
    )
    hybrid_structure.add_argument("evidence", type=Path)
    hybrid_structure.add_argument("baseline", type=Path)
    hybrid_structure.add_argument("--work-dir", type=Path, required=True)
    hybrid_structure.add_argument("--output", type=Path, required=True)
    hybrid_structure.add_argument("--repo-root", type=Path, default=Path.cwd())
    hybrid_structure.add_argument("--confidence-threshold", type=float, default=0.7)
    hybrid_structure.add_argument("--review-page", type=int, action="append", dest="review_pages")
    hybrid_structure.add_argument("--max-review-pages", type=int)
    hybrid_structure.add_argument("--workers", type=int, default=3)
    hybrid_structure.add_argument("--model", default="gpt-5.6-sol")
    hybrid_structure.add_argument("--reasoning-effort", default="high")
    hybrid_structure.add_argument("--metrics", type=Path)

    semantic_build = subparsers.add_parser("semantic-build", help="Build chapter and paragraph document semantics")
    semantic_build.add_argument("evidence", type=Path)
    semantic_build.add_argument("structure", type=Path)
    semantic_build.add_argument("visual_objects", type=Path)
    semantic_build.add_argument("--output", type=Path, required=True)
    semantic_build.add_argument("--previous", type=Path)
    semantic_build.add_argument("--metrics", type=Path)

    semantic_translate = subparsers.add_parser("semantic-translate", help="Translate reconstructed semantic paragraphs")
    semantic_translate.add_argument("document", type=Path)
    semantic_translate.add_argument("--repo-root", type=Path, default=Path.cwd())
    semantic_translate.add_argument("--cache-dir", type=Path, required=True)
    semantic_translate.add_argument("--max-characters", type=int, default=11000)
    semantic_translate.add_argument("--workers", type=int, default=3)
    semantic_translate.add_argument("--model", default="gpt-5.6-sol")
    semantic_translate.add_argument("--reasoning-effort", default="high")
    semantic_translate.add_argument("--metrics", type=Path)

    semantic_render = subparsers.add_parser("semantic-render", help="Render chapter-structured HTML")
    semantic_render.add_argument("document", type=Path)
    semantic_render.add_argument("--work-dir", type=Path, required=True)
    semantic_render.add_argument("--output-dir", type=Path, required=True)
    semantic_render.add_argument("--source-pdf", type=Path)
    semantic_render.add_argument("--zip", type=Path)
    semantic_render.add_argument("--metrics", type=Path)

    semantic_pipeline = subparsers.add_parser(
        "semantic-pipeline", help="Run the measured semantic PDF-to-HTML pipeline"
    )
    semantic_pipeline.add_argument("source", type=Path)
    semantic_pipeline.add_argument("--slug", required=True)
    semantic_pipeline.add_argument("--output-root", type=Path, default=Path("output"))
    semantic_pipeline.add_argument("--repo-root", type=Path, default=Path.cwd())
    semantic_pipeline.add_argument(
        "--layout-parser",
        choices=("pymupdf", "docling"),
        default="pymupdf",
        help="Use the legacy PyMuPDF geometry path or Docling's semantic document parser",
    )
    semantic_pipeline.add_argument(
        "--structure-mode", choices=("hybrid", "llm"), default="hybrid"
    )
    semantic_pipeline.add_argument("--structure-confidence-threshold", type=float, default=0.7)
    semantic_pipeline.add_argument("--max-structure-review-pages", type=int)
    semantic_pipeline.add_argument("--structure-review-workers", type=int, default=3)
    semantic_pipeline.add_argument("--structure-batch-size", type=int, default=2)
    semantic_pipeline.add_argument("--structure-model", default="gpt-5.6-sol")
    semantic_pipeline.add_argument("--structure-reasoning-effort", default="high")
    semantic_pipeline.add_argument("--translation-model", default="gpt-5.6-sol")
    semantic_pipeline.add_argument("--translation-reasoning-effort", default="high")
    semantic_pipeline.add_argument("--translation-workers", type=int, default=3)
    semantic_pipeline.add_argument("--max-characters", type=int, default=9000)
    semantic_pipeline.add_argument("--skip-translation", action="store_true")

    pdf_benchmark = subparsers.add_parser(
        "pdf-benchmark",
        help="Compare Docling with the deterministic PyMuPDF parser on a local PDF corpus",
    )
    pdf_benchmark.add_argument("corpus_dir", type=Path)
    pdf_benchmark.add_argument("--output", type=Path, required=True)
    pdf_benchmark.add_argument(
        "--work-root",
        type=Path,
        default=Path("output/pdf-parser-benchmark"),
    )
    pdf_benchmark.add_argument("--limit", type=int, default=10)

    arxiv_html_pipeline = subparsers.add_parser(
        "arxiv-html-pipeline",
        help="Acquire official arXiv HTML, translate semantic sections, and preserve MathML/links",
    )
    arxiv_html_pipeline.add_argument("arxiv_id")
    arxiv_html_pipeline.add_argument("--slug", required=True)
    arxiv_html_pipeline.add_argument("--output-root", type=Path, default=Path("output"))
    arxiv_html_pipeline.add_argument("--repo-root", type=Path, default=Path.cwd())
    arxiv_html_pipeline.add_argument("--translation-model", default="gpt-5.6-luna")
    arxiv_html_pipeline.add_argument("--translation-reasoning-effort", default="high")
    arxiv_html_pipeline.add_argument("--translation-workers", type=int, default=4)
    arxiv_html_pipeline.add_argument("--max-characters", type=int, default=14000)
    arxiv_html_pipeline.add_argument("--skip-translation", action="store_true")

    prepare_mcp_job = subparsers.add_parser(
        "prepare-mcp-job",
        help="Acquire official arXiv HTML and prepare a job for an MCP translation worker",
    )
    prepare_mcp_job.add_argument("arxiv_id")
    prepare_mcp_job.add_argument("--job-id")
    prepare_mcp_job.add_argument("--output-root", type=Path, default=Path("output"))
    prepare_mcp_job.add_argument("--repo-root", type=Path, default=Path.cwd())
    prepare_mcp_job.add_argument("--max-characters", type=int, default=9000)
    prepare_mcp_job.add_argument("--target-language", choices=("ja",), default="ja")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "pdf-benchmark":
        report = run_pdf_parser_benchmark(
            args.corpus_dir,
            args.output,
            args.work_root,
            limit=args.limit,
        )
        print(
            json.dumps(
                {
                    "papers": report["paperCount"],
                    "report": str(args.output),
                    "markdown": str(args.output.with_suffix(".md")),
                    "workRoot": report["workRoot"],
                },
                ensure_ascii=False,
            )
        )
        if report["status"] != "completed":
            raise SystemExit(1)
        return
    if args.command == "prepare-mcp-job":
        store = MCPTranslationStore(
            args.repo_root.resolve(),
            args.output_root.resolve(),
        )
        result = store.prepare(
            args.arxiv_id,
            job_id=args.job_id,
            max_characters=args.max_characters,
            target_language=args.target_language,
        )
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "arxiv-html-pipeline":
        result = run_arxiv_html_pipeline(
            args.arxiv_id,
            args.slug,
            args.output_root,
            args.repo_root.resolve(),
            max_characters=args.max_characters,
            translation_workers=args.translation_workers,
            translation_model=args.translation_model,
            translation_reasoning_effort=args.translation_reasoning_effort,
            skip_translation=args.skip_translation,
        )
        print(json.dumps(result, ensure_ascii=False))
        return
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
        started = utc_now()
        evidence = extract_layout_evidence(args.source, args.work_dir, args.output)
        record_stage(
            args.metrics,
            "layout_extraction",
            started,
            utc_now(),
            {"pages": evidence["pageCount"], "blocks": sum(len(page["blocks"]) for page in evidence["pages"])},
        )
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
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            metrics_path=args.metrics,
        )
        if args.source_pdf and args.assets_dir:
            visual_started = utc_now()
            objects = render_visual_objects(args.source_pdf, structured, args.assets_dir)
            (args.output.parent / "visual-objects.json").write_text(
                json.dumps(objects, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            write_visual_qa(objects, args.output.parent / "visual-qa.html")
            record_stage(
                args.metrics,
                "visual_extraction",
                visual_started,
                utc_now(),
                {"visualObjects": len(objects)},
            )
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

    if args.command == "deterministic-structure":
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        structured = analyze_layout_deterministic(
            evidence,
            args.output,
            max_pages=args.max_pages,
            metrics_path=args.metrics,
        )
        if args.source_pdf and args.assets_dir:
            visual_started = utc_now()
            objects = render_visual_objects(args.source_pdf, structured, args.assets_dir)
            visual_path = args.output.parent / "visual-objects-deterministic.json"
            visual_path.write_text(json.dumps(objects, ensure_ascii=False, indent=2), encoding="utf-8")
            write_visual_qa(objects, args.output.parent / "visual-qa-deterministic.html")
            record_stage(
                args.metrics,
                "deterministic_visual_extraction",
                visual_started,
                utc_now(),
                {"visualObjects": len(objects)},
            )
        print(
            json.dumps(
                {
                    "pages": len(structured["pages"]),
                    "sections": len(structured["sections"]),
                    "visualObjects": sum(len(page["visualObjects"]) for page in structured["pages"]),
                    "uncertainPages": structured["analysis"]["uncertainPages"],
                }
            )
        )
        return

    if args.command == "structure-evaluate":
        result = evaluate_structure(
            json.loads(args.gold.read_text(encoding="utf-8")),
            json.loads(args.candidate.read_text(encoding="utf-8")),
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return

    if args.command == "hybrid-structure":
        combined = refine_structure_with_llm(
            json.loads(args.evidence.read_text(encoding="utf-8")),
            json.loads(args.baseline.read_text(encoding="utf-8")),
            args.work_dir,
            args.output,
            args.repo_root.resolve(),
            confidence_threshold=args.confidence_threshold,
            explicit_pages=args.review_pages,
            max_review_pages=args.max_review_pages,
            max_workers=args.workers,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            metrics_path=args.metrics,
        )
        print(
            json.dumps(
                {
                    "reviewedPages": combined["analysis"].get("reviewedPages", []),
                    "unresolvedPages": combined["analysis"].get("unresolvedPages", []),
                    "output": str(args.output),
                }
            )
        )
        return

    if args.command == "semantic-build":
        started = utc_now()
        previous = load_semantic_document(args.previous) if args.previous and args.previous.exists() else None
        document = build_semantic_document(
            json.loads(args.evidence.read_text(encoding="utf-8")),
            json.loads(args.structure.read_text(encoding="utf-8")),
            json.loads(args.visual_objects.read_text(encoding="utf-8")),
        )
        if previous:
            merge_semantic_translations(document, previous)
        save_semantic_document(document, args.output)
        unit_count = sum(
            1 for section in document["sections"] for item in section["content"] if item["type"] == "unit"
        )
        record_stage(
            args.metrics,
            "semantic_build",
            started,
            utc_now(),
            {"sections": len(document["sections"]), "units": unit_count},
        )
        print(json.dumps({"sections": len(document["sections"]), "units": unit_count}))
        return

    if args.command == "semantic-translate":
        document = load_semantic_document(args.document)
        translate_semantic_document(
            document,
            args.document,
            args.repo_root.resolve(),
            args.cache_dir,
            max_characters=args.max_characters,
            max_workers=args.workers,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            metrics_path=args.metrics,
        )
        print(json.dumps({"status": document["status"], "model": document["model"]["translation"]}))
        return

    if args.command == "semantic-render":
        started = utc_now()
        document = load_semantic_document(args.document)
        index = render_semantic_document(document, args.work_dir, args.output_dir, args.source_pdf)
        if args.zip:
            create_bundle(args.output_dir, args.zip)
        record_stage(
            args.metrics,
            "html_render",
            started,
            utc_now(),
            {"html": str(index), "zip": str(args.zip) if args.zip else None},
        )
        print(index)
        return

    if args.command == "semantic-pipeline":
        repo_root = args.repo_root.resolve()
        output_root = args.output_root.resolve()
        paper_root = output_root / args.slug
        work_dir = paper_root / "work"
        evidence_path = work_dir / "layout-evidence.json"
        structure_path = work_dir / "structure.json"
        visuals_path = work_dir / "visual-objects.json"
        semantic_path = work_dir / "semantic-document.json"
        translation_cache = work_dir / "semantic-translations"
        publication_dir = paper_root / "html"
        bundle_path = paper_root / f"{args.slug}-html.zip"
        metrics_path = paper_root / "run-metrics.json"
        manifest_path = work_dir / "papertrans-job.json"
        pipeline_started = utc_now()
        manifest_structure_mode = "docling" if args.layout_parser == "docling" else args.structure_mode
        document = None
        active_manifest = write_pdf_job_manifest(
            manifest_path,
            slug=args.slug,
            source=args.source,
            status="translating",
            pdf_parser=args.layout_parser,
            structure_mode=manifest_structure_mode,
            started_at=pipeline_started.isoformat(),
            skip_translation=args.skip_translation,
        )
        source_sha256 = str(active_manifest["source"]["sha256"])
        try:
            started = utc_now()
            if args.layout_parser == "docling":
                evidence, structured, objects = extract_docling_semantics(
                    args.source,
                    work_dir,
                    evidence_path,
                    structure_path,
                    visuals_path,
                )
            else:
                evidence = extract_layout_evidence(args.source, work_dir, evidence_path)
                if args.structure_mode == "hybrid":
                    baseline_path = work_dir / "structure-baseline.json"
                    baseline = analyze_layout_deterministic(
                        evidence,
                        baseline_path,
                        metrics_path=metrics_path,
                    )
                    structured = refine_structure_with_llm(
                        evidence,
                        baseline,
                        work_dir,
                        structure_path,
                        repo_root,
                        confidence_threshold=args.structure_confidence_threshold,
                        max_review_pages=args.max_structure_review_pages,
                        max_workers=args.structure_review_workers,
                        model=args.structure_model,
                        reasoning_effort=args.structure_reasoning_effort,
                        metrics_path=metrics_path,
                    )
                else:
                    structured = analyze_layout(
                        evidence,
                        work_dir,
                        structure_path,
                        repo_root,
                        batch_size=args.structure_batch_size,
                        model=args.structure_model,
                        reasoning_effort=args.structure_reasoning_effort,
                        metrics_path=metrics_path,
                    )
                objects = render_visual_objects(args.source, structured, work_dir / "assets")
                visuals_path.write_text(
                    json.dumps(objects, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            record_stage(
                metrics_path,
                "layout_extraction",
                started,
                utc_now(),
                {
                    "parser": args.layout_parser,
                    "pages": evidence["pageCount"],
                    "blocks": sum(len(page["blocks"]) for page in evidence["pages"]),
                },
            )
            write_visual_qa(objects, work_dir / "visual-qa.html")

            build_started = utc_now()
            previous = load_semantic_document(semantic_path) if semantic_path.exists() else None
            document = build_semantic_document(evidence, structured, objects)
            if previous:
                merge_semantic_translations(document, previous)
            save_semantic_document(document, semantic_path)
            write_pdf_job_manifest(
                manifest_path,
                slug=args.slug,
                source=args.source,
                status="translating",
                pdf_parser=args.layout_parser,
                structure_mode=manifest_structure_mode,
                document=document,
                started_at=pipeline_started.isoformat(),
                skip_translation=args.skip_translation,
                source_sha256=source_sha256,
            )
            unit_count = sum(
                1
                for section in document["sections"]
                for item in section["content"]
                if item["type"] == "unit"
            )
            record_stage(
                metrics_path,
                "semantic_build",
                build_started,
                utc_now(),
                {"sections": len(document["sections"]), "units": unit_count},
            )
            if not args.skip_translation:
                document = translate_semantic_document(
                    document,
                    semantic_path,
                    repo_root,
                    translation_cache,
                    max_characters=args.max_characters,
                    max_workers=args.translation_workers,
                    model=args.translation_model,
                    reasoning_effort=args.translation_reasoning_effort,
                    metrics_path=metrics_path,
                    progress_callback=lambda current_document: write_pdf_job_manifest(
                        manifest_path,
                        slug=args.slug,
                        source=args.source,
                        status="translating",
                        pdf_parser=args.layout_parser,
                        structure_mode=manifest_structure_mode,
                        document=current_document,
                        started_at=pipeline_started.isoformat(),
                        skip_translation=args.skip_translation,
                        source_sha256=source_sha256,
                    ),
                )
            render_started = utc_now()
            index = render_semantic_document(document, work_dir, publication_dir, args.source)
            qa = write_semantic_pdf_qa(
                document,
                publication_dir,
                pdf_parser=args.layout_parser,
                evidence=evidence,
                structure=structured,
            )
            create_bundle(publication_dir, bundle_path)
            record_stage(
                metrics_path,
                "html_render",
                render_started,
                utc_now(),
                {"html": str(index), "zip": str(bundle_path), "qa": qa["status"]},
            )
            status = (
                "failed"
                if qa["status"] != "passed"
                else "needs_review"
                if args.skip_translation
                or document.get("status") == "needs_review"
                or bool(qa.get("emptyTextPages"))
                else "completed"
            )
            write_pdf_job_manifest(
                manifest_path,
                slug=args.slug,
                source=args.source,
                status=status,
                pdf_parser=args.layout_parser,
                structure_mode=manifest_structure_mode,
                document=document,
                started_at=pipeline_started.isoformat(),
                skip_translation=args.skip_translation,
                error="HTML artifact QA failed" if status == "failed" else None,
                source_sha256=source_sha256,
            )
            if status == "failed":
                raise RuntimeError("HTML artifact QA failed")
            print(
                json.dumps(
                    {
                        "html": str(index),
                        "bundle": str(bundle_path),
                        "manifest": str(manifest_path),
                        "qa": str(publication_dir / "qa.json"),
                        "metrics": str(metrics_path),
                        "status": status,
                    },
                    ensure_ascii=False,
                )
            )
        except BaseException as error:
            write_pdf_job_manifest(
                manifest_path,
                slug=args.slug,
                source=args.source,
                status="failed",
                pdf_parser=args.layout_parser,
                structure_mode=manifest_structure_mode,
                document=document,
                started_at=pipeline_started.isoformat(),
                skip_translation=args.skip_translation,
                error=str(error),
                source_sha256=source_sha256,
            )
            raise
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
