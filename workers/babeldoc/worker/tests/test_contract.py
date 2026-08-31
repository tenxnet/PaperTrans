from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from papertrans.pdf_translation_worker import validate_backend_health
from papertrans.pdf_translation_worker import validate_ndjson_events
from papertrans_babeldoc_worker.cli import _health_document
from papertrans_babeldoc_worker.constants import BACKEND_ID
from papertrans_babeldoc_worker.constants import FORK_PATCH_SHA256
from papertrans_babeldoc_worker.constants import UPSTREAM_LOCK_SHA256
from papertrans_babeldoc_worker.contract import load_json_object
from papertrans_babeldoc_worker.contract import parse_request
from papertrans_babeldoc_worker.errors import ContractError
from papertrans_babeldoc_worker.events import EventEmitter
from papertrans_babeldoc_worker.readiness import ReadinessReport


def valid_request() -> dict:
    return {
        "schemaVersion": 1,
        "runId": "pdf-babeldoc-01jexample",
        "source": {
            "mediaType": "application/pdf",
            "sha256": "a" * 64,
            "bytes": 1234,
        },
        "translation": {
            "sourceLanguage": "en",
            "targetLanguage": "ja",
            "profileId": "evaluation-ja-v1",
            "providerId": "openai-compatible-local",
            "modelId": "test/model-v1",
            "promptRevision": "papertrans-pdf-ja-v1",
            "glossarySha256": None,
        },
        "outputs": ["translated_mono_pdf", "translated_dual_pdf"],
        "limits": {
            "maxPages": 300,
            "maxOutputBytes": 524_288_000,
            "deadlineSeconds": 1500,
        },
    }


def test_valid_request_is_normalized() -> None:
    request = parse_request(valid_request())
    assert request.run_id == "pdf-babeldoc-01jexample"
    assert request.outputs == ("translated_mono_pdf", "translated_dual_pdf")
    assert request.translation.model_id == "test/model-v1"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda value: value.update({"rawPrompt": "ignore policy"}), "unexpected_fields"),
        (lambda value: value["limits"].update({"maxPages": True}), "invalid_integer"),
        (lambda value: value["translation"].update({"targetLanguage": "zh"}), "unsupported_language_pair"),
        (lambda value: value["translation"].update({"glossarySha256": "b" * 64}), "unsupported_glossary"),
        (lambda value: value.update({"outputs": ["translated_mono_pdf", "../../escape"]}), "unsupported_output"),
        (lambda value: value.update({"outputs": ["translated_mono_pdf", "translated_mono_pdf"]}), "duplicate_output"),
    ],
)
def test_request_fails_closed(mutation, code: str) -> None:
    value = valid_request()
    mutation(value)
    with pytest.raises(ContractError) as caught:
        parse_request(value)
    assert caught.value.code == code


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
    with pytest.raises(ContractError) as caught:
        load_json_object(path)
    assert caught.value.code == "duplicate_json_key"


def test_json_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "request.json"
    link.symlink_to(target)
    with pytest.raises(ContractError) as caught:
        load_json_object(link)
    assert caught.value.code == "request_not_regular"


def test_events_are_ndjson_and_monotonic() -> None:
    stream = io.StringIO()
    emitter = EventEmitter("run-1", stream)
    emitter.emit("started", backendId="backend")
    emitter.emit("completed")
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [event["sequence"] for event in events] == [1, 2]
    assert [event["type"] for event in events] == ["started", "completed"]
    assert all(event["runId"] == "run-1" for event in events)


def test_health_document_passes_common_host_validator() -> None:
    digest = "a" * 64
    report = ReadinessReport(
        ready=True,
        checks=(),
        versions={},
        source_revision=UPSTREAM_LOCK_SHA256,
        build_digest="sha256:" + digest,
        image_digest="sha256:" + digest,
        sbom_sha256="b" * 64,
        lock_sha256="c" * 64,
    )
    document = _health_document(report)

    assert set(document) == {
        "schemaVersion",
        "protocolVersion",
        "backendId",
        "adapterVersion",
        "engineVersion",
        "dependencies",
        "sourceRevision",
        "forkRevision",
        "buildDigest",
        "imageDigest",
        "sbomSha256",
        "lockSha256",
        "capabilities",
        "ready",
    }
    validated = validate_backend_health(
        document,
        approved_fork_revisions={FORK_PATCH_SHA256},
        required_outputs={"translated_mono_pdf", "translated_dual_pdf"},
    )
    assert validated["backendId"] == BACKEND_ID


def test_success_events_pass_common_host_validator() -> None:
    stream = io.StringIO()
    emitter = EventEmitter("run-1", stream)
    emitter.emit("started", backendId=BACKEND_ID)
    emitter.emit("stage", stage="translate")
    emitter.emit("progress", stage="translate", completed=1, total=2)
    emitter.emit(
        "artifact",
        role="translated_mono_pdf",
        path="artifacts/translated-mono.pdf",
        mediaType="application/pdf",
        sha256="d" * 64,
        bytes=42,
    )
    emitter.emit("completed")

    validated = validate_ndjson_events(stream.getvalue(), run_id="run-1")
    assert [event["type"] for event in validated] == [
        "started",
        "stage",
        "progress",
        "artifact",
        "completed",
    ]


def test_failure_events_pass_common_host_validator() -> None:
    stream = io.StringIO()
    emitter = EventEmitter("run-1", stream)
    emitter.emit("started", backendId=BACKEND_ID)
    emitter.emit("failed", code="worker_not_ready", message="worker is not ready")

    validated = validate_ndjson_events(stream.getvalue(), run_id="run-1")
    assert validated[-1]["type"] == "failed"
