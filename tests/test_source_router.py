from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents/skills/academic-paper-source-router/scripts/select_source_route.py"
)
SPEC = importlib.util.spec_from_file_location("select_source_route", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def candidate(candidate_id: str, kind: str, **overrides):
    value = {
        "id": candidate_id,
        "kind": kind,
        "available": True,
        "access": "open",
        "identityMatch": "exact",
        "validation": "passed",
        "fullText": kind != "pdf",
    }
    value.update(overrides)
    return value


def manifest(*candidates, policy=None):
    value = {
        "schemaVersion": "1.0",
        "paper": {"arxivId": "2510.08859v2", "requestedArtifact": "arxiv_revision"},
        "candidates": list(candidates),
    }
    if policy is not None:
        value["policy"] = policy
    return value


def test_prefers_official_arxiv_html_without_structure_resources():
    result = MODULE.select_route(
        manifest(
            candidate("pdf", "pdf", metrics={"pagesWithUsableTextRatio": 1, "garbledCharacterRatio": 0}),
            candidate("source", "latex_source"),
            candidate("ar5iv", "ar5iv_html"),
            candidate("official", "official_arxiv_html"),
        )
    )

    assert result["selected"]["candidateId"] == "official"
    assert result["resources"] == {
        "llmRouting": "disabled",
        "translationUnit": "section",
        "structureModel": "disabled",
        "vision": "disabled",
        "ocr": "disabled",
    }


def test_arxiv_revision_stays_on_arxiv_route_even_if_other_semantic_source_exists():
    result = MODULE.select_route(
        manifest(
            candidate("publisher", "publisher_jats"),
            candidate("official", "official_arxiv_html"),
        )
    )

    assert result["selected"]["candidateId"] == "official"


def test_falls_back_locally_when_official_html_fails_validation():
    result = MODULE.select_route(
        manifest(
            candidate("official", "official_arxiv_html", validation="failed"),
            candidate("ar5iv", "ar5iv_html"),
            candidate("source", "latex_source"),
        )
    )

    assert result["selected"]["candidateId"] == "ar5iv"
    assert result["fallbackOrder"] == ["source"]
    assert result["rejections"] == [
        {"candidateId": "official", "reason": "source validation is failed"}
    ]


def test_uses_lightweight_pdf_route_for_reliable_text_layer():
    result = MODULE.select_route(
        manifest(
            candidate(
                "publisher-pdf",
                "pdf",
                metrics={"pagesWithUsableTextRatio": 0.97, "garbledCharacterRatio": 0.01},
            )
        )
    )

    assert result["selected"]["route"] == "pdf_text"
    assert result["resources"]["ocr"] == "disabled"
    assert result["resources"]["vision"] == "low_confidence_only"


def test_limits_ocr_to_missing_text_pages_for_scanned_pdf():
    result = MODULE.select_route(
        manifest(
            candidate(
                "scan",
                "pdf",
                metrics={"pagesWithUsableTextRatio": 0.2, "garbledCharacterRatio": 0.4},
            )
        )
    )

    assert result["selected"]["route"] == "pdf_ocr"
    assert result["resources"]["ocr"] == "missing_text_pages_only"
    assert result["resources"]["vision"] == "low_confidence_only"


def test_does_not_substitute_equivalent_preprint_without_permission():
    related = candidate("preprint", "official_arxiv_html", identityMatch="equivalent")

    rejected = MODULE.select_route(manifest(related))
    allowed = MODULE.select_route(
        manifest(related, policy={"allowEquivalentPreprint": True})
    )

    assert rejected["status"] == "unresolved"
    assert allowed["selected"]["candidateId"] == "preprint"
    assert allowed["warnings"]


def test_rejects_restricted_full_text():
    result = MODULE.select_route(
        manifest(candidate("publisher", "publisher_jats", access="restricted"))
    )

    assert result["status"] == "unresolved"
    assert result["rejections"][0]["reason"] == "source access is not open or authorized"
