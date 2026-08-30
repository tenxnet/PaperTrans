import copy
import hashlib
import json
from pathlib import Path

import pymupdf as fitz
import pytest

from papertrans.pdf_translation_worker import (
    MAX_OUTPUT_BYTES,
    PdfTranslationContractError,
    inspect_pdf,
    load_and_validate_worker_result,
    promote_candidate_pdf,
    publish_candidate_run,
    validate_backend_health,
    validate_ndjson_events,
    validate_worker_request,
)


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_pdf(
    path: Path,
    *,
    pages: int = 2,
    width: float = 612,
    height: float = 792,
    rotation: int = 0,
) -> None:
    document = fitz.open()
    for number in range(pages):
        page = document.new_page(width=width, height=height)
        page.insert_text((72, 72), f"Paper page {number + 1}")
        if rotation:
            page.set_rotation(rotation)
    document.save(path)
    document.close()


def _request(source: Path, *, run_id: str = "pdf-harumi-01") -> dict:
    return {
        "schemaVersion": 1,
        "runId": run_id,
        "source": {
            "mediaType": "application/pdf",
            "sha256": _sha256(source),
            "bytes": source.stat().st_size,
        },
        "translation": {
            "sourceLanguage": "en",
            "targetLanguage": "ja",
            "profileId": "evaluation-ja-v1",
            "providerId": "deterministic-local",
            "modelId": "cardinality-stub-v1",
            "promptRevision": "papertrans-pdf-ja-v1",
            "glossarySha256": None,
        },
        "outputs": ["translated_mono_pdf"],
        "limits": {
            "maxPages": 300,
            "maxOutputBytes": MAX_OUTPUT_BYTES,
            "deadlineSeconds": 1500,
        },
    }


def _health(*, backend_id: str = "harumi-papertrans") -> dict:
    return {
        "schemaVersion": 1,
        "protocolVersion": 1,
        "backendId": backend_id,
        "adapterVersion": "0.1.0",
        "engineVersion": "1.19.0",
        "dependencies": {"harumi": "1.19.0", "harumi-ai": "0.9.0"},
        "sourceRevision": HEX_A,
        "forkRevision": None,
        "buildDigest": f"sha256:{HEX_B}",
        "imageDigest": f"sha256:{HEX_C}",
        "sbomSha256": HEX_D,
        "lockSha256": HEX_A,
        "capabilities": {"outputs": ["translated_mono_pdf"]},
        "ready": True,
        "modelDigests": {"layout": HEX_B},
        "fontDigests": {"noto-cjk-ja": HEX_C},
    }


def _stage(staging: Path, source: Path, request: dict) -> tuple[dict, str]:
    artifacts = staging / "artifacts"
    artifacts.mkdir(parents=True)
    translated = artifacts / "translated-mono.pdf"
    translated.write_bytes(source.read_bytes())
    entry = {
        "role": "translated_mono_pdf",
        "path": "artifacts/translated-mono.pdf",
        "mediaType": "application/pdf",
        "sha256": _sha256(translated),
        "bytes": translated.stat().st_size,
    }
    result = {
        "schemaVersion": 1,
        "runId": request["runId"],
        "sourceSha256": request["source"]["sha256"],
        "artifacts": [entry],
        "pageMaps": {
            "translated_mono_pdf": [
                {"sourcePage": page, "outputPages": [page]} for page in (1, 2)
            ]
        },
    }
    (staging / "worker-result.json").write_text(
        json.dumps(result), encoding="utf-8"
    )
    events = "\n".join(
        json.dumps(event)
        for event in (
            {
                "schemaVersion": 1,
                "runId": request["runId"],
                "sequence": 1,
                "time": "2026-08-30T00:00:00Z",
                "type": "started",
                "backendId": "harumi-papertrans",
            },
            {
                "schemaVersion": 1,
                "runId": request["runId"],
                "sequence": 2,
                "time": "2026-08-30T00:00:01Z",
                "type": "progress",
                "stage": "translate",
                "completed": 2,
                "total": 2,
            },
            {
                "schemaVersion": 1,
                "runId": request["runId"],
                "sequence": 3,
                "time": "2026-08-30T00:00:02Z",
                "type": "artifact",
                **entry,
            },
            {
                "schemaVersion": 1,
                "runId": request["runId"],
                "sequence": 4,
                "time": "2026-08-30T00:00:03Z",
                "type": "completed",
            },
        )
    )
    return result, events


def _assert_code(code: str, callback) -> None:
    with pytest.raises(PdfTranslationContractError) as error:
        callback()
    assert error.value.code == code


def test_strict_request_validates_source_and_rejects_unsafe_input(tmp_path: Path):
    source = tmp_path / "source.pdf"
    _make_pdf(source)
    request = _request(source)
    assert validate_worker_request(request, source_path=source)["runId"] == "pdf-harumi-01"

    unknown = copy.deepcopy(request)
    unknown["sourceUrl"] = "https://example.invalid/source.pdf"
    _assert_code("invalid_schema", lambda: validate_worker_request(unknown))

    bad_language = copy.deepcopy(request)
    bad_language["translation"]["targetLanguage"] = "../../ja"
    _assert_code("invalid_language", lambda: validate_worker_request(bad_language))

    oversized = copy.deepcopy(request)
    oversized["limits"]["maxOutputBytes"] = MAX_OUTPUT_BYTES + 1
    _assert_code("limit_exceeded", lambda: validate_worker_request(oversized))

    wrong_digest = copy.deepcopy(request)
    wrong_digest["source"]["sha256"] = HEX_A
    _assert_code(
        "source_digest_mismatch",
        lambda: validate_worker_request(wrong_digest, source_path=source),
    )


def test_health_rejects_vulnerable_babeldoc_and_unapproved_forks():
    safe = _health(backend_id="papertrans-babeldoc")
    safe["dependencies"] = {"babeldoc": "0.6.4", "pymupdf": "1.26.7"}
    safe["forkRevision"] = HEX_D
    assert (
        validate_backend_health(safe, approved_fork_revisions={HEX_D})["forkRevision"]
        == HEX_D
    )

    vulnerable = copy.deepcopy(safe)
    vulnerable["dependencies"]["babeldoc"] = "0.6.2"
    _assert_code(
        "policy_refusal",
        lambda: validate_backend_health(vulnerable, approved_fork_revisions={HEX_D}),
    )
    old_pymupdf = copy.deepcopy(safe)
    old_pymupdf["dependencies"]["pymupdf"] = "1.26.6"
    _assert_code(
        "policy_refusal",
        lambda: validate_backend_health(old_pymupdf, approved_fork_revisions={HEX_D}),
    )
    _assert_code("policy_refusal", lambda: validate_backend_health(safe))

    missing_sbom = _health()
    del missing_sbom["sbomSha256"]
    _assert_code("invalid_schema", lambda: validate_backend_health(missing_sbom))


def test_ndjson_requires_ordered_matching_terminal_sequence():
    good = "\n".join(
        json.dumps(event)
        for event in (
            {
                "schemaVersion": 1,
                "runId": "pdf-run-1",
                "sequence": 1,
                "time": "2026-08-30T00:00:00Z",
                "type": "started",
            },
            {
                "schemaVersion": 1,
                "runId": "pdf-run-1",
                "sequence": 2,
                "time": "2026-08-30T00:00:01Z",
                "type": "completed",
            },
        )
    )
    assert [event["type"] for event in validate_ndjson_events(good, run_id="pdf-run-1")] == [
        "started",
        "completed",
    ]
    rollback = good.replace('"sequence": 2', '"sequence": 1')
    _assert_code(
        "invalid_event", lambda: validate_ndjson_events(rollback, run_id="pdf-run-1")
    )
    _assert_code(
        "invalid_event",
        lambda: validate_ndjson_events(good.splitlines()[0], run_id="pdf-run-1"),
    )


@pytest.mark.parametrize("mutation,expected", [("extra", "extra_file"), ("hash", "artifact_digest_mismatch"), ("symlink", "unsafe_file")])
def test_staging_rejects_extra_hash_mismatch_and_symlink(
    tmp_path: Path, mutation: str, expected: str
):
    source = tmp_path / "source.pdf"
    _make_pdf(source)
    request = _request(source)
    staging = tmp_path / "staging"
    _stage(staging, source, request)
    if mutation == "extra":
        (staging / "unexpected.txt").write_text("no", encoding="utf-8")
    elif mutation == "hash":
        result_path = staging / "worker-result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["artifacts"][0]["sha256"] = HEX_A
        result_path.write_text(json.dumps(result), encoding="utf-8")
    else:
        translated = staging / "artifacts" / "translated-mono.pdf"
        translated.unlink()
        translated.symlink_to(source)
    _assert_code(
        expected,
        lambda: load_and_validate_worker_result(
            staging, request=request, source_pdf=source
        ),
    )


def test_pdf_validation_rejects_geometry_changes_and_active_content(tmp_path: Path):
    source = tmp_path / "source.pdf"
    _make_pdf(source)
    request = _request(source)
    staging = tmp_path / "geometry-staging"
    _stage(staging, source, request)
    translated = staging / "artifacts" / "translated-mono.pdf"
    _make_pdf(translated, width=600)
    result_path = staging / "worker-result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["artifacts"][0]["sha256"] = _sha256(translated)
    result["artifacts"][0]["bytes"] = translated.stat().st_size
    result_path.write_text(json.dumps(result), encoding="utf-8")
    _assert_code(
        "page_geometry_mismatch",
        lambda: load_and_validate_worker_result(
            staging, request=request, source_pdf=source
        ),
    )

    active = tmp_path / "active.pdf"
    _make_pdf(active)
    document = fitz.open(active)
    document.xref_set_key(
        document.pdf_catalog(), "OpenAction", "<</S/JavaScript/JS(app.alert\\(1\\))>>"
    )
    document.save(active.with_suffix(".new.pdf"))
    document.close()
    active.with_suffix(".new.pdf").replace(active)
    _assert_code("active_content", lambda: inspect_pdf(active))


def test_atomic_publication_and_explicit_promotion_preserve_main_status(tmp_path: Path):
    source = tmp_path / "source.pdf"
    _make_pdf(source)
    request = _request(source)
    staging = tmp_path / "worker-staging"
    _, events = _stage(staging, source, request)
    output = tmp_path / "output"

    run_root = publish_candidate_run(
        output_root=output,
        slug="paper-1",
        source_pdf=source,
        staging_dir=staging,
        request=request,
        health=_health(),
        events_ndjson=events,
        worker_log=f"source was {source}",
    )
    assert run_root == output / "paper-1" / "pdf-runs" / request["runId"]
    assert json.loads((run_root / "qa.json").read_text(encoding="utf-8"))["status"] == "passed"
    assert "[redacted-path]" in (run_root / "worker.log").read_text(encoding="utf-8")
    index = json.loads((run_root / "artifact-index.json").read_text(encoding="utf-8"))
    assert index["artifacts"][0]["path"] == "artifacts/translated-mono.pdf"
    _assert_code(
        "run_exists",
        lambda: publish_candidate_run(
            output_root=output,
            slug="paper-1",
            source_pdf=source,
            staging_dir=staging,
            request=request,
            health=_health(),
            events_ndjson=events,
        ),
    )

    manifest_path = output / "paper-1" / "work" / "papertrans-job.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "jobId": "paper-1",
                "sourceType": "pdf",
                "status": "needs_review",
                "source": {"sha256": request["source"]["sha256"]},
                "artifacts": {"html": "html/index.html"},
            }
        ),
        encoding="utf-8",
    )
    promoted = promote_candidate_pdf(
        output_root=output,
        slug="paper-1",
        run_id=request["runId"],
        approved_by="reviewer-1",
        promoted_at="2026-08-30T00:10:00Z",
    )
    assert promoted["status"] == "needs_review"
    assert promoted["artifacts"]["translatedPdf"] == (
        "pdf-runs/pdf-harumi-01/artifacts/translated-mono.pdf"
    )
    assert promoted["pdfTranslation"]["approvedBy"] == "reviewer-1"
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "needs_review"

