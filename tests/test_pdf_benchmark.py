import sys
from pathlib import Path

import pytest

import papertrans.cli as cli
import papertrans.pdf_benchmark as benchmark
from papertrans.pdf_benchmark import run_pdf_parser_benchmark, summarize_parser_result


def test_summarizes_structure_without_treating_counts_as_quality_score():
    evidence = {"pageCount": 1, "pages": [{"blocks": [{}, {}]}]}
    structure = {
        "sections": [{"sectionId": "s1"}],
        "warnings": ["review page 1"],
        "pages": [
            {
                "blockAssignments": [
                    {"role": "heading", "hidden": False, "confidence": 0.9},
                    {"role": "paragraph", "hidden": False, "confidence": 0.6},
                ]
            }
        ],
    }
    result = summarize_parser_result(
        evidence,
        structure,
        [{"kind": "figure"}, {"kind": "table"}],
        duration_seconds=1.23456,
    )
    assert result["durationSeconds"] == 1.235
    assert result["blocks"] == 2
    assert result["sections"] == 1
    assert result["lowConfidenceAssignments"] == 1
    assert result["roles"] == {"heading": 1, "paragraph": 1}
    assert result["visualObjects"] == {"figure": 1, "table": 1}
    assert "score" not in result


def test_benchmark_requires_distinct_json_and_markdown_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\.json suffix"):
        run_pdf_parser_benchmark(
            tmp_path,
            tmp_path / "comparison.md",
            tmp_path / "work",
        )


def test_benchmark_report_is_failed_when_any_parser_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "paper.pdf").write_bytes(b"%PDF-fixture")
    results = iter(
        [
            {"status": "completed", "durationSeconds": 1.0},
            {"status": "failed", "durationSeconds": 2.0, "error": "worker failed"},
        ]
    )
    monkeypatch.setattr(benchmark, "_run_parser", lambda *_args: next(results))

    output = tmp_path / "comparison.json"
    report = run_pdf_parser_benchmark(corpus, output, tmp_path / "work")

    assert report["status"] == "failed"
    assert output.exists()
    assert output.with_suffix(".md").exists()


def test_benchmark_cli_exits_nonzero_after_preserving_failed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "comparison.json"
    monkeypatch.setattr(
        cli,
        "run_pdf_parser_benchmark",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "paperCount": 1,
            "workRoot": str(tmp_path / "work"),
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["papertrans", "pdf-benchmark", str(tmp_path), "--output", str(output)],
    )

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 1
