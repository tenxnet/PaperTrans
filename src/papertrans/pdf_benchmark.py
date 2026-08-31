from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from .deterministic_structure import analyze_layout_deterministic
from .docling_adapter import extract_docling_semantics
from .structure import extract_layout_evidence


ParserResult = tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]


def _visuals_from_structure(structure: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**visual, "pageNumber": page["pageNumber"]}
        for page in structure.get("pages", [])
        for visual in page.get("visualObjects", [])
    ]


def summarize_parser_result(
    evidence: dict[str, Any],
    structure: dict[str, Any],
    visuals: list[dict[str, Any]],
    *,
    duration_seconds: float,
) -> dict[str, Any]:
    assignments = [
        assignment
        for page in structure.get("pages", [])
        for assignment in page.get("blockAssignments", [])
    ]
    confidences = [float(value.get("confidence", 0)) for value in assignments]
    role_counts: dict[str, int] = {}
    for assignment in assignments:
        role = str(assignment.get("role", "unknown"))
        role_counts[role] = role_counts.get(role, 0) + 1
    visual_counts: dict[str, int] = {}
    for visual in visuals:
        kind = str(visual.get("kind", "unknown"))
        visual_counts[kind] = visual_counts.get(kind, 0) + 1
    return {
        "status": "completed",
        "durationSeconds": round(duration_seconds, 3),
        "pages": int(evidence.get("pageCount", 0)),
        "blocks": sum(len(page.get("blocks", [])) for page in evidence.get("pages", [])),
        "sections": len(structure.get("sections", [])),
        "assignments": len(assignments),
        "hiddenAssignments": sum(bool(value.get("hidden")) for value in assignments),
        "lowConfidenceAssignments": sum(value < 0.7 for value in confidences),
        "meanConfidence": round(mean(confidences), 4) if confidences else 0,
        "roles": role_counts,
        "visualObjects": visual_counts,
        "warnings": list(structure.get("warnings", [])),
    }


def _safe_stem(index: int, path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-.") or "paper"
    return f"{index:02d}-{stem[:48]}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_parser(
    parser: Callable[[Path, Path], ParserResult],
    source: Path,
    work_dir: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        evidence, structure, visuals = parser(source, work_dir)
        return summarize_parser_result(
            evidence,
            structure,
            visuals,
            duration_seconds=time.perf_counter() - started,
        )
    except Exception as error:
        return {
            "status": "failed",
            "durationSeconds": round(time.perf_counter() - started, 3),
            "error": str(error)[:2000],
        }


def _pymupdf_parser(source: Path, work_dir: Path) -> ParserResult:
    evidence = extract_layout_evidence(
        source,
        work_dir,
        work_dir / "layout-evidence.json",
    )
    structure = analyze_layout_deterministic(
        evidence,
        work_dir / "structure.json",
    )
    visuals = _visuals_from_structure(structure)
    (work_dir / "visual-objects.json").write_text(
        json.dumps(visuals, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return evidence, structure, visuals


def _docling_parser(source: Path, work_dir: Path) -> ParserResult:
    return extract_docling_semantics(
        source,
        work_dir,
        work_dir / "layout-evidence.json",
        work_dir / "structure.json",
        work_dir / "visual-objects.json",
    )


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# PDF parser comparison",
        "",
        f"Generated: {report['generatedAt']}",
        "",
        "Automated counts are triage signals, not semantic accuracy scores. Review every paper using the checklist below.",
        "",
        "| Paper | Parser | Status | Seconds | Blocks | Sections | Low confidence | Figures | Tables | Equations |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for paper in report["papers"]:
        for parser in ("pymupdf", "docling"):
            value = paper["parsers"][parser]
            visuals = value.get("visualObjects", {})
            lines.append(
                "| {paper} | {parser} | {status} | {seconds} | {blocks} | {sections} | {low} | {figures} | {tables} | {equations} |".format(
                    paper=paper["fileName"].replace("|", "\\|"),
                    parser=parser,
                    status=value.get("status", "failed"),
                    seconds=value.get("durationSeconds", "-"),
                    blocks=value.get("blocks", "-"),
                    sections=value.get("sections", "-"),
                    low=value.get("lowConfidenceAssignments", "-"),
                    figures=visuals.get("figure", 0),
                    tables=visuals.get("table", 0),
                    equations=visuals.get("equation", 0),
                )
            )
    lines.extend(
        [
            "",
            "## Manual review checklist",
            "",
            "For each parser and paper, score 0–2 for reading order, section hierarchy, paragraph continuity, figure/table crops, equations, captions, references, and page furniture. Record blocking errors separately; do not infer quality from object counts alone.",
            "",
        ]
    )
    return "\n".join(lines)


def run_pdf_parser_benchmark(
    corpus_dir: Path,
    output: Path,
    work_root: Path,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if output.suffix.lower() != ".json":
        raise ValueError("benchmark --output must use a .json suffix")
    sources = sorted(path for path in corpus_dir.resolve().iterdir() if path.suffix.lower() == ".pdf")[:limit]
    if not sources:
        raise ValueError(f"no PDF files found in {corpus_dir}")
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
    run_root = work_root.resolve() / run_id
    if run_root.exists():
        raise FileExistsError(f"benchmark run already exists: {run_root}")
    run_root.mkdir(parents=True)
    papers: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        paper_root = run_root / _safe_stem(index, source)
        papers.append(
            {
                "fileName": source.name,
                "sha256": _sha256(source),
                "parsers": {
                    "pymupdf": _run_parser(_pymupdf_parser, source, paper_root / "pymupdf"),
                    "docling": _run_parser(_docling_parser, source, paper_root / "docling"),
                },
                "manualReview": {
                    key: None
                    for key in (
                        "readingOrder",
                        "sectionHierarchy",
                        "paragraphContinuity",
                        "visualCrops",
                        "equations",
                        "captions",
                        "references",
                        "pageFurniture",
                    )
                },
            }
        )
    report = {
        "schemaVersion": 1,
        "status": (
            "completed"
            if all(
                parser_result.get("status") == "completed"
                for paper in papers
                for parser_result in paper["parsers"].values()
            )
            else "failed"
        ),
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "corpus": str(corpus_dir.resolve()),
        "workRoot": str(run_root),
        "paperCount": len(papers),
        "papers": papers,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(_markdown_report(report), encoding="utf-8")
    return report
