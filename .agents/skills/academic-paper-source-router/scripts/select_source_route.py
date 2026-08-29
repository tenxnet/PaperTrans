#!/usr/bin/env python3
"""Select the lowest-cost reliable acquisition route from probed source metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PUBLISHER_PRIORITY = {
    "publisher_jats": 100,
    "publisher_html": 95,
    "official_arxiv_html": 85,
    "ar5iv_html": 80,
    "latex_source": 75,
    "pdf": 40,
}

ARXIV_PRIORITY = {
    "official_arxiv_html": 100,
    "ar5iv_html": 90,
    "latex_source": 85,
    "publisher_jats": 75,
    "publisher_html": 70,
    "pdf": 40,
}

SEMANTIC_ROUTES = {
    "publisher_jats",
    "publisher_html",
    "official_arxiv_html",
    "ar5iv_html",
    "latex_source",
}


def _reject_reason(candidate: dict[str, Any], allow_equivalent: bool) -> str | None:
    if not candidate.get("available", False):
        return "source is unavailable"
    if candidate.get("access") not in {"open", "authorized"}:
        return "source access is not open or authorized"
    identity = candidate.get("identityMatch")
    if identity == "mismatch":
        return "source identity or version does not match"
    if identity == "unknown":
        return "source identity or version is unverified"
    if identity == "equivalent" and not allow_equivalent:
        return "equivalent preprint was not explicitly permitted"
    validation = candidate.get("validation")
    if validation not in {"passed", "degraded"}:
        return f"source validation is {validation or 'missing'}"
    if candidate.get("kind") in SEMANTIC_ROUTES and not candidate.get("fullText", False):
        return "source does not contain full text"
    metrics = candidate.get("metrics", {})
    if metrics.get("fatalErrorCount", 0) > 0:
        return "source contains fatal conversion errors"
    return None


def _score(candidate: dict[str, Any], paper: dict[str, Any]) -> tuple[int, str]:
    kind = candidate["kind"]
    requested_arxiv = bool(paper.get("arxivId")) or paper.get("requestedArtifact") == "arxiv_revision"
    priorities = ARXIV_PRIORITY if requested_arxiv else PUBLISHER_PRIORITY
    score = priorities[kind]
    if candidate.get("identityMatch") == "exact":
        score += 10
    if candidate.get("validation") == "degraded":
        score -= 8
    unresolved_assets = candidate.get("metrics", {}).get("unresolvedAssetCount", 0)
    score -= min(int(unresolved_assets), 5)
    return score, candidate["id"]


def _pdf_route(candidate: dict[str, Any], policy: dict[str, Any]) -> str:
    metrics = candidate.get("metrics", {})
    text_ratio = float(metrics.get("pagesWithUsableTextRatio", 0.0))
    garbled_ratio = float(metrics.get("garbledCharacterRatio", 1.0))
    text_threshold = float(policy.get("pdfTextPageThreshold", 0.8))
    garbled_threshold = float(policy.get("pdfGarbledTextThreshold", 0.1))
    if text_ratio >= text_threshold and garbled_ratio <= garbled_threshold:
        return "pdf_text"
    return "pdf_ocr"


def _resources(route: str | None) -> dict[str, str]:
    base = {"llmRouting": "disabled", "translationUnit": "section"}
    if route in SEMANTIC_ROUTES:
        return {
            **base,
            "structureModel": "disabled",
            "vision": "disabled",
            "ocr": "disabled",
        }
    if route == "pdf_text":
        return {
            **base,
            "structureModel": "low_confidence_only",
            "vision": "low_confidence_only",
            "ocr": "disabled",
        }
    if route == "pdf_ocr":
        return {
            **base,
            "structureModel": "required",
            "vision": "low_confidence_only",
            "ocr": "missing_text_pages_only",
        }
    return {
        **base,
        "structureModel": "disabled",
        "vision": "disabled",
        "ocr": "disabled",
    }


def select_route(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schemaVersion") != "1.0":
        raise ValueError("schemaVersion must be 1.0")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("candidates must be an array")

    policy = manifest.get("policy") or {}
    allow_equivalent = bool(policy.get("allowEquivalentPreprint", False))
    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []

    seen_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate.get("id")
        kind = candidate.get("kind")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("every candidate requires a non-empty id")
        if candidate_id in seen_ids:
            raise ValueError(f"duplicate candidate id: {candidate_id}")
        seen_ids.add(candidate_id)
        if kind not in PUBLISHER_PRIORITY:
            raise ValueError(f"unsupported candidate kind: {kind}")
        reason = _reject_reason(candidate, allow_equivalent)
        if reason:
            rejections.append({"candidateId": candidate_id, "reason": reason})
        else:
            accepted.append(candidate)

    paper = manifest.get("paper") or {}
    accepted.sort(key=lambda candidate: _score(candidate, paper), reverse=True)
    if not accepted:
        return {
            "schemaVersion": "1.0",
            "status": "unresolved",
            "selected": None,
            "fallbackOrder": [],
            "rejections": rejections,
            "resources": _resources(None),
            "warnings": ["No verified, authorized, full-text source passed validation."],
        }

    selected = accepted[0]
    selected_route = (
        _pdf_route(selected, policy) if selected["kind"] == "pdf" else selected["kind"]
    )
    warnings: list[str] = []
    if selected.get("identityMatch") == "equivalent":
        warnings.append("Selected an explicitly permitted equivalent artifact, not the exact requested version.")
    if selected.get("validation") == "degraded":
        warnings.append("Selected source passed with degradation; repair only the affected regions.")
    if selected_route == "pdf_ocr":
        warnings.append("PDF lacks a sufficiently reliable text layer; OCR only missing-text pages.")

    fallback_order = [candidate["id"] for candidate in accepted[1:]]
    reason = (
        f"Selected {selected_route} as the highest-ranked exact/allowed, authorized, "
        "validated full-text source."
    )
    return {
        "schemaVersion": "1.0",
        "status": "selected",
        "selected": {
            "candidateId": selected["id"],
            "kind": selected["kind"],
            "route": selected_route,
            "reason": reason,
        },
        "fallbackOrder": fallback_order,
        "rejections": rejections,
        "resources": _resources(selected_route),
        "warnings": warnings,
    }


def _read_json(path: Path | None) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="candidate manifest JSON; stdin when omitted")
    parser.add_argument("--output", type=Path, help="route result JSON; stdout when omitted")
    args = parser.parse_args()

    try:
        result = select_route(_read_json(args.input))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"source route selection failed: {error}", file=sys.stderr)
        return 2

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
