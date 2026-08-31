from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Collection, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

import pymupdf as fitz

from .pdf_artifacts import paper_manifest_lock


SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_PAGES = 300
MAX_OUTPUT_BYTES = 500 * 1024 * 1024
MAX_DEADLINE_SECONDS = 25 * 60
MAX_OUTPUT_PAGES = MAX_PAGES * 2
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 64 * 1024
MAX_PAGE_DIMENSION_POINTS = 14_400.0
MAX_RENDER_PIXELS = 4_000_000
PDF_GEOMETRY_TOLERANCE = 0.1
_MUPDF_INSPECTION_LOCK = threading.Lock()

PDF_OUTPUT_ROLES = frozenset({"translated_mono_pdf", "translated_dual_pdf"})
ARTIFACT_ROLES = PDF_OUTPUT_ROLES | {"backend_report"}
ROLE_PATHS = {
    "translated_mono_pdf": "artifacts/translated-mono.pdf",
    "translated_dual_pdf": "artifacts/translated-dual.pdf",
    "backend_report": "artifacts/backend-report.json",
}
ROLE_MEDIA_TYPES = {
    "translated_mono_pdf": "application/pdf",
    "translated_dual_pdf": "application/pdf",
    "backend_report": "application/json",
}
_FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "prompt",
        "sourcetext",
        "translatedtext",
    }
)

_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_LANGUAGE_TAG = re.compile(
    r"^(?:[a-z]{2,3})(?:-(?:[A-Z][a-z]{3}|[A-Z]{2}|[0-9]{3}|[A-Za-z0-9]{5,8}))*$"
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACTIVE_NAME = re.compile(
    r"/(?:JavaScript|JS|Launch|RichMedia|RichMediaContent|EmbeddedFile|"
    r"EmbeddedFiles|OpenAction|AA|SubmitForm|ImportData|GoToR|GoToE|"
    r"Rendition|Movie|Sound|Screen|3D|XFA|AcroForm)(?![A-Za-z0-9])"
)
_STANDARD_ACTION_SUBTYPES = frozenset(
    {
        "GoTo",
        "GoToR",
        "GoToE",
        "Launch",
        "Thread",
        "URI",
        "Sound",
        "Movie",
        "Hide",
        "Named",
        "SubmitForm",
        "ResetForm",
        "ImportData",
        "JavaScript",
        "SetOCGState",
        "Rendition",
        "Trans",
        "GoTo3DView",
    }
)
_ACTION_SUBTYPE = re.compile(r"/S\s*/([A-Za-z][A-Za-z0-9]*)")
_ACTION_DICTIONARY = re.compile(r"/A\s*<<(.*?)(?:>>|$)", re.DOTALL)
_ACTION_REFERENCE = re.compile(r"/A\s+(\d+)\s+\d+\s+R")
_NEXT_ACTION_DICTIONARY = re.compile(r"/Next\s*<<(.*?)(?:>>|$)", re.DOTALL)
_NEXT_ACTION_REFERENCE = re.compile(r"/Next\s+(\d+)\s+\d+\s+R")
_ACTION_TYPE = re.compile(r"/Type\s*/Action(?![A-Za-z0-9])")
_LITERAL_URI = re.compile(r"/URI\s*\(((?:\\.|[^\\)])*)\)", re.DOTALL)
_HEX_URI = re.compile(r"/URI\s*<([0-9A-Fa-f\s]+)>")


class PdfTranslationContractError(ValueError):
    """A fail-closed contract, policy, artifact, or publication error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PdfPageGeometry:
    media_box: tuple[float, float, float, float]
    crop_box: tuple[float, float, float, float]
    rotation: int


@dataclass(frozen=True)
class PdfInspection:
    page_count: int
    pages: tuple[PdfPageGeometry, ...]
    rendered_pages: int
    active_findings: tuple[str, ...]
    external_uris: frozenset[str]


@dataclass(frozen=True)
class ValidatedArtifact:
    role: str
    path: str
    media_type: str
    sha256: str
    bytes: int

    def to_index_entry(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.path,
            "mediaType": self.media_type,
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class ValidatedWorkerResult:
    artifacts: tuple[ValidatedArtifact, ...]
    page_maps: dict[str, list[dict[str, Any]]]
    source_pdf: PdfInspection
    output_pdfs: dict[str, PdfInspection]
    artifact_index: dict[str, Any]
    qa: dict[str, Any]


def _fail(code: str, message: str) -> None:
    raise PdfTranslationContractError(code, message)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid_schema", f"{field} must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        _fail("invalid_schema", f"{field} contains a non-string key")
    return value


def _strict_keys(
    value: Mapping[str, Any],
    field: str,
    *,
    required: Collection[str],
    optional: Collection[str] = (),
) -> None:
    keys = set(value)
    missing = set(required) - keys
    unknown = keys - set(required) - set(optional)
    if missing:
        _fail("invalid_schema", f"{field} is missing fields: {sorted(missing)}")
    if unknown:
        _fail("invalid_schema", f"{field} contains unknown fields: {sorted(unknown)}")


def _positive_int(value: Any, field: str, maximum: int | None = None) -> int:
    if not _is_int(value) or value <= 0:
        _fail("invalid_schema", f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        _fail("limit_exceeded", f"{field} exceeds the hard maximum of {maximum}")
    return value


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _fail("invalid_schema", f"{field} is not a safe recorded identifier")
    return value


def _validate_backend_report_safety(value: Any) -> None:
    """Bound untrusted supporting evidence and reject content-bearing fields."""

    retained_characters = 0

    def visit(item: Any, *, depth: int) -> None:
        nonlocal retained_characters
        if depth > 10:
            _fail("invalid_artifact", "backend report nesting exceeds policy")
        if item is None or isinstance(item, bool):
            return
        if _is_int(item):
            if abs(item) > 2**63 - 1:
                _fail("invalid_artifact", "backend report integer exceeds policy")
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                _fail("invalid_artifact", "backend report contains a non-finite number")
            return
        if isinstance(item, str):
            if len(item) > 1000 or "\x00" in item:
                _fail("invalid_artifact", "backend report contains unbounded text")
            retained_characters += len(item)
            if retained_characters > 64 * 1024:
                _fail("invalid_artifact", "backend report retains too much text")
            return
        if isinstance(item, list):
            if len(item) > MAX_OUTPUT_PAGES:
                _fail("invalid_artifact", "backend report array exceeds page policy")
            for child in item:
                visit(child, depth=depth + 1)
            return
        if isinstance(item, dict):
            if len(item) > 64 or not all(isinstance(key, str) for key in item):
                _fail("invalid_artifact", "backend report object exceeds policy")
            for key, child in item.items():
                if key.casefold().replace("_", "") in _FORBIDDEN_REPORT_KEYS:
                    _fail(
                        "invalid_artifact",
                        "backend report contains a forbidden content or credential field",
                    )
                visit(child, depth=depth + 1)
            return
        _fail("invalid_artifact", "backend report contains an unsupported JSON value")

    visit(value, depth=0)


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail("invalid_digest", f"{field} must be 64 lowercase hexadecimal characters")
    return value


def _image_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _fail("invalid_digest", f"{field} must be an immutable sha256:<hex> digest")
    return value


def _run_id(value: Any, field: str = "runId") -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        _fail("invalid_run_id", f"{field} must match {_RUN_ID.pattern}")
    return value


def _language(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _LANGUAGE_TAG.fullmatch(value):
        _fail("invalid_language", f"{field} must be a canonical, bounded BCP-47 language tag")
    return value


def _strict_json_loads(raw: str, field: str) -> Any:
    def reject_constant(value: str) -> None:
        _fail("invalid_json", f"{field} contains non-finite value {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("invalid_json", f"{field} contains duplicate field {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except PdfTranslationContractError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        _fail("invalid_json", f"{field} is not strict JSON: {exc}")


def _json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("invalid_json", f"value cannot be encoded as strict JSON: {exc}")


@contextmanager
def _open_regular(path: Path, *, field: str) -> Iterator[BinaryIO]:
    try:
        before = path.lstat()
    except FileNotFoundError:
        _fail("missing_file", f"{field} does not exist")
    if stat.S_ISLNK(before.st_mode):
        _fail("unsafe_file", f"{field} must not be a symlink")
    if not stat.S_ISREG(before.st_mode):
        _fail("unsafe_file", f"{field} must be a regular file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        _fail("unsafe_file", f"could not safely open {field}: {exc}")
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode):
            _fail("unsafe_file", f"{field} changed type while opening")
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            _fail("unsafe_file", f"{field} changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            yield handle
    finally:
        os.close(descriptor)


def _hash_and_size(path: Path, *, field: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with _open_regular(path, field=field) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _read_regular_bytes(path: Path, *, field: str, maximum: int) -> bytes:
    with _open_regular(path, field=field) as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        _fail("limit_exceeded", f"{field} exceeds {maximum} bytes")
    return data


def validate_worker_request(
    raw: Mapping[str, Any],
    *,
    source_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and return a detached strict process-contract request."""

    request = _object(raw, "request")
    _strict_keys(
        request,
        "request",
        required={"schemaVersion", "runId", "source", "translation", "outputs", "limits"},
    )
    if request["schemaVersion"] != SCHEMA_VERSION or not _is_int(request["schemaVersion"]):
        _fail("unsupported_schema", "request.schemaVersion must be 1")
    run_id = _run_id(request["runId"])

    source = _object(request["source"], "request.source")
    _strict_keys(source, "request.source", required={"mediaType", "sha256", "bytes"})
    if source["mediaType"] != "application/pdf":
        _fail("unsupported_media_type", "request.source.mediaType must be application/pdf")
    source_digest = _sha256(source["sha256"], "request.source.sha256")
    source_bytes = _positive_int(source["bytes"], "request.source.bytes", MAX_INPUT_BYTES)

    translation = _object(request["translation"], "request.translation")
    _strict_keys(
        translation,
        "request.translation",
        required={
            "sourceLanguage",
            "targetLanguage",
            "profileId",
            "providerId",
            "modelId",
            "promptRevision",
            "glossarySha256",
        },
    )
    source_language = _language(
        translation["sourceLanguage"], "request.translation.sourceLanguage"
    )
    target_language = _language(
        translation["targetLanguage"], "request.translation.targetLanguage"
    )
    if source_language.lower() == target_language.lower():
        _fail("invalid_language", "source and target languages must differ")
    for key in ("profileId", "providerId", "modelId", "promptRevision"):
        _identifier(translation[key], f"request.translation.{key}")
    glossary_digest = translation["glossarySha256"]
    if glossary_digest is not None:
        _sha256(glossary_digest, "request.translation.glossarySha256")

    outputs = request["outputs"]
    if not isinstance(outputs, list) or not outputs:
        _fail("invalid_outputs", "request.outputs must be a non-empty array")
    if not all(isinstance(role, str) for role in outputs):
        _fail("invalid_outputs", "request.outputs may contain only string roles")
    if len(outputs) != len(set(outputs)):
        _fail("invalid_outputs", "request.outputs contains a duplicate role")
    unsupported_outputs = set(outputs) - PDF_OUTPUT_ROLES
    if unsupported_outputs:
        _fail("invalid_outputs", f"request.outputs contains unsupported roles: {sorted(unsupported_outputs)}")

    limits = _object(request["limits"], "request.limits")
    _strict_keys(
        limits,
        "request.limits",
        required={"maxPages", "maxOutputBytes", "deadlineSeconds"},
    )
    max_pages = _positive_int(limits["maxPages"], "request.limits.maxPages", MAX_PAGES)
    _positive_int(
        limits["maxOutputBytes"], "request.limits.maxOutputBytes", MAX_OUTPUT_BYTES
    )
    _positive_int(
        limits["deadlineSeconds"],
        "request.limits.deadlineSeconds",
        MAX_DEADLINE_SECONDS,
    )

    if source_path is not None:
        source_path = Path(source_path)
        actual_digest, actual_bytes = _hash_and_size(source_path, field="source PDF")
        if actual_bytes != source_bytes:
            _fail(
                "source_size_mismatch",
                f"source PDF has {actual_bytes} bytes, request declares {source_bytes}",
            )
        if actual_digest != source_digest:
            _fail("source_digest_mismatch", "source PDF digest does not match request")
        inspect_pdf(source_path, max_pages=max_pages, reject_active_content=True)

    normalized = copy.deepcopy(dict(request))
    normalized["runId"] = run_id
    return normalized


def load_worker_request(path: Path, *, source_path: Path | None = None) -> dict[str, Any]:
    raw = _read_regular_bytes(Path(path), field="request.json", maximum=MAX_JSON_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("invalid_json", f"request.json is not UTF-8: {exc}")
    return validate_worker_request(
        _object(_strict_json_loads(text, "request.json"), "request.json"),
        source_path=source_path,
    )


def _version_tuple(value: Any, field: str) -> tuple[int, int, int, int]:
    if not isinstance(value, str):
        _fail("policy_refusal", f"{field} is missing an exact version")
    match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+)|\.post(\d+))?",
        value,
    )
    if not match:
        _fail("policy_refusal", f"{field} must be an exact three-part release version")
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    suffix = match.group(4)
    # Stable and post releases sort after prereleases of the same numeric release.
    release_rank = -1 if suffix else 0
    return major, minor, patch, release_rank


def validate_backend_health(
    raw: Mapping[str, Any],
    *,
    approved_fork_revisions: Collection[str] = (),
    required_outputs: Collection[str] = (),
) -> dict[str, Any]:
    """Apply common immutable-build policy and BabelDOC-specific hard stops."""

    health = _object(raw, "health")
    _strict_keys(
        health,
        "health",
        required={
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
        },
        optional={"modelDigests", "fontDigests"},
    )
    if health["schemaVersion"] != SCHEMA_VERSION or not _is_int(health["schemaVersion"]):
        _fail("policy_refusal", "health.schemaVersion must be 1")
    if health["protocolVersion"] != SCHEMA_VERSION or not _is_int(
        health["protocolVersion"]
    ):
        _fail("policy_refusal", "health.protocolVersion must be 1")
    backend_id = _identifier(health["backendId"], "health.backendId")
    _identifier(health["adapterVersion"], "health.adapterVersion")
    _identifier(health["engineVersion"], "health.engineVersion")
    if health["ready"] is not True:
        _fail("policy_refusal", "backend health is not ready")

    _image_digest(health["buildDigest"], "health.buildDigest")
    _image_digest(health["imageDigest"], "health.imageDigest")
    _sha256(health["sbomSha256"], "health.sbomSha256")
    _sha256(health["lockSha256"], "health.lockSha256")
    source_revision = health["sourceRevision"]
    if not isinstance(source_revision, str) or not _SHA256.fullmatch(source_revision):
        _fail("policy_refusal", "health.sourceRevision must be an immutable commit digest")

    dependencies = _object(health["dependencies"], "health.dependencies")
    if not dependencies or not all(
        isinstance(name, str) and isinstance(version, str) and name and version
        for name, version in dependencies.items()
    ):
        _fail("policy_refusal", "health.dependencies must contain exact dependency versions")

    capabilities = _object(health["capabilities"], "health.capabilities")
    _strict_keys(capabilities, "health.capabilities", required={"outputs"})
    supported_outputs = capabilities["outputs"]
    if (
        not isinstance(supported_outputs, list)
        or len(supported_outputs) != len(set(supported_outputs))
        or not all(role in PDF_OUTPUT_ROLES for role in supported_outputs)
    ):
        _fail("policy_refusal", "health.capabilities.outputs is invalid")
    unsupported_required = set(required_outputs) - set(supported_outputs)
    if unsupported_required:
        _fail(
            "unsupported_output",
            f"backend does not declare requested outputs: {sorted(unsupported_required)}",
        )

    for digest_field in ("modelDigests", "fontDigests"):
        values = health.get(digest_field, {})
        if not isinstance(values, dict) or not all(
            isinstance(name, str)
            and bool(name)
            and isinstance(digest, str)
            and bool(_SHA256.fullmatch(digest))
            for name, digest in values.items()
        ):
            _fail("policy_refusal", f"health.{digest_field} contains an invalid digest")

    normalized_dependency_names = {name.lower(): value for name, value in dependencies.items()}
    is_babeldoc = (
        "babeldoc" in backend_id.lower()
        or "pdf2zh" in backend_id.lower()
        or "babeldoc" in normalized_dependency_names
    )
    if is_babeldoc:
        babeldoc_version = _version_tuple(
            normalized_dependency_names.get("babeldoc"), "BabelDOC"
        )
        pymupdf_version = _version_tuple(
            normalized_dependency_names.get("pymupdf"), "PyMuPDF"
        )
        if babeldoc_version < (0, 6, 3, 0):
            _fail("policy_refusal", "BabelDOC <= 0.6.2 or a prerelease is blocked")
        if pymupdf_version < (1, 26, 7, 0):
            _fail("policy_refusal", "PyMuPDF < 1.26.7 or a prerelease is blocked")
        fork_revision = health["forkRevision"]
        if (
            not isinstance(fork_revision, str)
            or not _SHA256.fullmatch(fork_revision)
            or fork_revision not in set(approved_fork_revisions)
        ):
            _fail("policy_refusal", "BabelDOC backend fork revision is not approved")
    elif health["forkRevision"] is not None:
        if not isinstance(health["forkRevision"], str) or not _SHA256.fullmatch(
            health["forkRevision"]
        ):
            _fail("policy_refusal", "health.forkRevision must be null or a commit digest")

    return copy.deepcopy(dict(health))


def _event_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        _fail("invalid_event", f"{field} must be an RFC 3339 timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        _fail("invalid_event", f"{field} must be an RFC 3339 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("invalid_event", f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


_EVENT_REQUIRED: dict[str, set[str]] = {
    "started": set(),
    "stage": {"stage"},
    "progress": {"stage", "completed", "total"},
    "warning": {"code", "message"},
    "artifact": {"role", "path", "mediaType", "sha256", "bytes"},
    "completed": set(),
    "failed": {"code", "message"},
}
_EVENT_OPTIONAL: dict[str, set[str]] = {
    "started": {"backendId"},
    "stage": set(),
    "progress": set(),
    "warning": set(),
    "artifact": set(),
    "completed": set(),
    "failed": set(),
}


def validate_ndjson_events(
    value: str | bytes | Iterable[str],
    *,
    run_id: str,
    require_terminal: bool = True,
) -> list[dict[str, Any]]:
    """Validate the complete worker stdout protocol without accepting free-form text."""

    expected_run_id = _run_id(run_id)
    if isinstance(value, bytes):
        if len(value) > MAX_JSON_BYTES:
            _fail("invalid_event", "event stream exceeds the retained protocol limit")
        try:
            lines = value.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            _fail("invalid_event", f"event stream is not UTF-8: {exc}")
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_JSON_BYTES:
            _fail("invalid_event", "event stream exceeds the retained protocol limit")
        lines = value.splitlines()
    else:
        lines = list(value)

    events: list[dict[str, Any]] = []
    previous_sequence = 0
    previous_time: datetime | None = None
    terminal_seen = False
    retained_bytes = 0
    for line_number, line in enumerate(lines, start=1):
        if not isinstance(line, str):
            _fail("invalid_event", f"event line {line_number} is not text")
        if not line.strip():
            _fail("invalid_event", f"event line {line_number} is blank")
        line_bytes = len(line.encode("utf-8"))
        retained_bytes += line_bytes + 1
        if retained_bytes > MAX_JSON_BYTES:
            _fail("invalid_event", "event stream exceeds the retained protocol limit")
        if line_bytes > MAX_EVENT_LINE_BYTES:
            _fail("invalid_event", f"event line {line_number} is too large")
        event = _object(
            _strict_json_loads(line, f"event line {line_number}"),
            f"event line {line_number}",
        )
        event_type = event.get("type")
        if event_type not in _EVENT_REQUIRED:
            _fail("invalid_event", f"event line {line_number} has unknown type {event_type!r}")
        common = {"schemaVersion", "runId", "sequence", "time", "type"}
        _strict_keys(
            event,
            f"event line {line_number}",
            required=common | _EVENT_REQUIRED[event_type],
            optional=_EVENT_OPTIONAL[event_type],
        )
        if event["schemaVersion"] != SCHEMA_VERSION or not _is_int(event["schemaVersion"]):
            _fail("invalid_event", f"event line {line_number} has unsupported schemaVersion")
        if event["runId"] != expected_run_id:
            _fail("invalid_event", f"event line {line_number} has a different runId")
        sequence = _positive_int(event["sequence"], f"event line {line_number}.sequence")
        if sequence <= previous_sequence:
            _fail("invalid_event", f"event line {line_number} sequence is not increasing")
        parsed_time = _event_time(event["time"], f"event line {line_number}.time")
        if previous_time is not None and parsed_time < previous_time:
            _fail("invalid_event", f"event line {line_number} time moved backwards")
        if terminal_seen:
            _fail("invalid_event", "an event appears after the terminal event")
        if not events and event_type != "started":
            _fail("invalid_event", "the first event must be started")
        if events and event_type == "started":
            _fail("invalid_event", "started may appear only once")

        if event_type in {"stage", "progress"}:
            _identifier(event["stage"], f"event line {line_number}.stage")
        if event_type == "progress":
            completed = event["completed"]
            total = event["total"]
            if not _is_int(completed) or completed < 0:
                _fail("invalid_event", "progress.completed must be a non-negative integer")
            _positive_int(total, "progress.total")
            if completed > total:
                _fail("invalid_event", "progress.completed must not exceed progress.total")
        if event_type in {"warning", "failed"}:
            _identifier(event["code"], f"event line {line_number}.code")
            if (
                not isinstance(event["message"], str)
                or not event["message"].strip()
                or len(event["message"]) > 1000
                or "\n" in event["message"]
            ):
                _fail("invalid_event", f"event line {line_number}.message is invalid")
        if event_type == "artifact":
            role = event["role"]
            if role not in ARTIFACT_ROLES:
                _fail("invalid_event", f"event line {line_number} has an invalid artifact role")
            if event["path"] != ROLE_PATHS[role]:
                _fail("invalid_event", f"event line {line_number} has an invalid artifact path")
            if event["mediaType"] != ROLE_MEDIA_TYPES[role]:
                _fail("invalid_event", f"event line {line_number} has an invalid media type")
            _sha256(event["sha256"], f"event line {line_number}.sha256")
            _positive_int(event["bytes"], f"event line {line_number}.bytes")
        if event_type == "started" and "backendId" in event:
            _identifier(event["backendId"], f"event line {line_number}.backendId")
        if event_type in {"completed", "failed"}:
            terminal_seen = True

        events.append(copy.deepcopy(event))
        previous_sequence = sequence
        previous_time = parsed_time

    if not events:
        _fail("invalid_event", "event stream is empty")
    if require_terminal and events[-1]["type"] not in {"completed", "failed"}:
        _fail("invalid_event", "event stream has no terminal event")
    return events


def _rect_tuple(rect: Any) -> tuple[float, float, float, float]:
    return tuple(float(value) for value in (rect.x0, rect.y0, rect.x1, rect.y1))


def _safe_pdf_link_uris(document: fitz.Document) -> frozenset[str]:
    values: set[str] = set()
    for page in document:
        try:
            links = page.get_links()
        except (RuntimeError, ValueError) as exc:
            _fail("malformed_pdf", f"could not enumerate PDF links: {exc}")
        for link in links:
            kind = link.get("kind")
            if kind == fitz.LINK_URI:
                uri = link.get("uri")
                if (
                    not isinstance(uri, str)
                    or not uri
                    or len(uri) > 4096
                    or any(character in uri for character in ("\x00", "\r", "\n"))
                ):
                    _fail("active_content", "PDF contains a malformed external URI action")
                scheme = urlsplit(uri).scheme.lower()
                if scheme not in {"http", "https", "mailto"}:
                    _fail(
                        "active_content",
                        "PDF contains a blocked external URI action scheme",
                    )
                values.add(uri)
            elif kind in {fitz.LINK_LAUNCH, fitz.LINK_GOTOR}:
                _fail("active_content", "PDF contains a launch or remote-go-to action")
    return frozenset(values)


def _uri_from_action_dictionary(raw: str) -> str | None:
    literal = _LITERAL_URI.search(raw)
    if literal is not None:
        value = re.sub(
            r"\\([()\\])",
            lambda match: match.group(1),
            literal.group(1),
        )
        if "\\" in value:
            return None
        return value
    hexadecimal = _HEX_URI.search(raw)
    if hexadecimal is None:
        return None
    compact = re.sub(r"\s+", "", hexadecimal.group(1))
    if len(compact) % 2:
        compact += "0"
    try:
        return bytes.fromhex(compact).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _action_dictionary_findings(
    raw: str,
    *,
    external_uris: frozenset[str],
) -> set[str]:
    findings: set[str] = set()
    subtype_match = _ACTION_SUBTYPE.search(raw)
    if subtype_match is None:
        findings.add("action_missing_or_unknown_subtype")
        return findings
    subtype = subtype_match.group(1)
    if subtype not in _STANDARD_ACTION_SUBTYPES:
        findings.add("action_unknown_subtype")
        return findings
    if subtype not in {"GoTo", "URI"}:
        findings.add(f"action_{subtype.lower()}")
        return findings
    if subtype == "GoTo":
        if re.search(r"/D(?![A-Za-z0-9])", raw) is None:
            findings.add("action_goto_without_destination")
        return findings

    uri = _uri_from_action_dictionary(raw)
    if uri is None or uri not in external_uris:
        findings.add("action_uri_not_verified")
        return findings
    scheme = urlsplit(uri).scheme.lower()
    if scheme not in {"http", "https", "mailto"}:
        findings.add("action_uri_blocked_scheme")
    return findings


def _action_graph_findings(
    *,
    raw_objects: Mapping[int, str],
    external_uris: frozenset[str],
) -> set[str]:
    """Default-deny action dictionaries reachable from /A or /Next."""

    findings: set[str] = set()
    pending: list[int] = []
    for raw in raw_objects.values():
        for direct in _ACTION_DICTIONARY.finditer(raw):
            findings.update(
                _action_dictionary_findings(
                    direct.group(1), external_uris=external_uris
                )
            )
        pending.extend(int(match.group(1)) for match in _ACTION_REFERENCE.finditer(raw))
        if _ACTION_TYPE.search(raw):
            findings.update(
                _action_dictionary_findings(raw, external_uris=external_uris)
            )
        for subtype_match in _ACTION_SUBTYPE.finditer(raw):
            subtype = subtype_match.group(1)
            if subtype in _STANDARD_ACTION_SUBTYPES - {"GoTo", "URI"}:
                findings.add(f"action_{subtype.lower()}")

    visited: set[int] = set()
    while pending:
        xref = pending.pop()
        if xref in visited:
            continue
        visited.add(xref)
        raw = raw_objects.get(xref)
        if raw is None:
            findings.add("action_reference_invalid")
            continue
        findings.update(
            _action_dictionary_findings(raw, external_uris=external_uris)
        )
        pending.extend(
            int(match.group(1)) for match in _NEXT_ACTION_REFERENCE.finditer(raw)
        )
        for direct in _NEXT_ACTION_DICTIONARY.finditer(raw):
            findings.update(
                _action_dictionary_findings(
                    direct.group(1), external_uris=external_uris
                )
            )
    return findings


def _active_pdf_findings(
    document: fitz.Document,
    *,
    external_uris: frozenset[str],
) -> tuple[str, ...]:
    findings: set[str] = set()
    try:
        trailer = document.pdf_trailer()
    except (RuntimeError, ValueError):
        trailer = ""
    if re.search(r"/Encrypt(?![A-Za-z0-9])", trailer or ""):
        findings.add("encrypted")
    try:
        if document.embfile_names():
            findings.add("embedded_file")
    except (RuntimeError, ValueError):
        findings.add("embedded_file_scan_failed")

    raw_objects: dict[int, str] = {}
    for xref in range(1, document.xref_length()):
        try:
            raw_object = document.xref_object(xref, compressed=False)
        except (RuntimeError, ValueError):
            findings.add("xref_scan_failed")
            continue
        raw_objects[xref] = raw_object
        for match in _ACTIVE_NAME.finditer(raw_object):
            findings.add(match.group(0)[1:].lower())
    findings.update(
        _action_graph_findings(
            raw_objects=raw_objects,
            external_uris=external_uris,
        )
    )
    return tuple(sorted(findings))


def _inspect_pdf_with_pymupdf(
    path: Path,
    *,
    max_pages: int = MAX_PAGES,
    reject_active_content: bool = True,
) -> PdfInspection:
    """Run the PyMuPDF portion of the fail-closed PDF inspection."""

    path = Path(path)
    # A bilingual dual-layout artifact may legitimately contain two output
    # pages for every source page. Source requests remain capped at MAX_PAGES
    # by validate_worker_request().
    _positive_int(max_pages, "max_pages", MAX_OUTPUT_PAGES)
    with _open_regular(path, field="PDF") as handle:
        magic = handle.read(5)
    if magic != b"%PDF-":
        _fail("malformed_pdf", "PDF does not begin with %PDF-")

    try:
        document = fitz.open(str(path))
    except (RuntimeError, ValueError, OSError) as exc:
        _fail("malformed_pdf", f"PyMuPDF could not open PDF: {exc}")
    try:
        if document.needs_pass or document.is_encrypted:
            _fail("encrypted_pdf", "encrypted or password-protected PDFs are unsupported")
        page_count = document.page_count
        if page_count <= 0:
            _fail("malformed_pdf", "PDF has no pages")
        if page_count > max_pages:
            _fail("page_limit", f"PDF has {page_count} pages, limit is {max_pages}")

        external_uris = _safe_pdf_link_uris(document)
        active_findings = _active_pdf_findings(
            document,
            external_uris=external_uris,
        )
        if "encrypted" in active_findings:
            _fail("encrypted_pdf", "PDF trailer declares encryption")
        if reject_active_content and active_findings:
            _fail(
                "active_content",
                "PDF contains blocked active content: " + ", ".join(active_findings),
            )
        pages: list[PdfPageGeometry] = []
        rendered_pages = 0
        for page_number in range(page_count):
            try:
                page = document.load_page(page_number)
                media_box = _rect_tuple(page.mediabox)
                crop_box = _rect_tuple(page.cropbox)
                rotation = int(page.rotation)
            except (RuntimeError, ValueError, TypeError) as exc:
                _fail("malformed_pdf", f"could not read page {page_number + 1}: {exc}")
            for box_name, box in (("MediaBox", media_box), ("CropBox", crop_box)):
                if not all(math.isfinite(value) for value in box):
                    _fail("invalid_page_geometry", f"page {page_number + 1} {box_name} is non-finite")
                width = abs(box[2] - box[0])
                height = abs(box[3] - box[1])
                if width <= 0 or height <= 0:
                    _fail("invalid_page_geometry", f"page {page_number + 1} {box_name} is empty")
                if width > MAX_PAGE_DIMENSION_POINTS or height > MAX_PAGE_DIMENSION_POINTS:
                    _fail(
                        "invalid_page_geometry",
                        f"page {page_number + 1} {box_name} exceeds the page-size budget",
                    )
            if rotation not in {0, 90, 180, 270}:
                _fail("invalid_page_geometry", f"page {page_number + 1} has invalid rotation")

            visible_width = max(float(page.rect.width), 1.0)
            visible_height = max(float(page.rect.height), 1.0)
            scale = min(
                0.5,
                math.sqrt(MAX_RENDER_PIXELS / (visible_width * visible_height)),
            )
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            except (RuntimeError, ValueError, MemoryError) as exc:
                _fail("render_smoke_failed", f"page {page_number + 1} did not render: {exc}")
            if pixmap.width <= 0 or pixmap.height <= 0 or pixmap.stride <= 0:
                _fail("render_smoke_failed", f"page {page_number + 1} rendered an empty pixmap")
            rendered_pages += 1
            pages.append(PdfPageGeometry(media_box, crop_box, rotation))

        return PdfInspection(
            page_count=page_count,
            pages=tuple(pages),
            rendered_pages=rendered_pages,
            active_findings=active_findings,
            external_uris=external_uris,
        )
    finally:
        document.close()


def inspect_pdf(
    path: Path,
    *,
    max_pages: int = MAX_PAGES,
    reject_active_content: bool = True,
) -> PdfInspection:
    """Open, bound, render-smoke, and reject parser/render warnings."""

    # MuPDF's warning buffer and display switches are process-global. Serialize
    # the inspection so one concurrent PDF cannot hide or inherit another's
    # diagnostics.
    with _MUPDF_INSPECTION_LOCK:
        previous_errors = bool(fitz.TOOLS.mupdf_display_errors())
        previous_warnings = bool(fitz.TOOLS.mupdf_display_warnings())
        fitz.TOOLS.mupdf_display_errors(False)
        fitz.TOOLS.mupdf_display_warnings(False)
        fitz.TOOLS.mupdf_warnings(reset=True)
        diagnostics = ""
        try:
            inspection = _inspect_pdf_with_pymupdf(
                path,
                max_pages=max_pages,
                reject_active_content=reject_active_content,
            )
            diagnostics = fitz.TOOLS.mupdf_warnings(reset=True)
        finally:
            fitz.TOOLS.mupdf_warnings(reset=True)
            fitz.TOOLS.mupdf_display_errors(previous_errors)
            fitz.TOOLS.mupdf_display_warnings(previous_warnings)
    if diagnostics.strip():
        count = len(diagnostics.splitlines())
        _fail(
            "render_smoke_failed",
            f"MuPDF reported {count} parser or render warning(s)",
        )
    return inspection


def _independent_pdf_check(path: Path) -> dict[str, Any]:
    """Run qpdf as an independent structural parser when it is installed."""

    executable = shutil.which("qpdf")
    if executable is None:
        return {"tool": "qpdf", "status": "unavailable"}
    try:
        result = subprocess.run(
            [executable, "--check", str(Path(path))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"tool": "qpdf", "status": "failed"}
    return {
        "tool": "qpdf",
        "status": "passed" if result.returncode == 0 else "failed",
    }


def _validate_result_document(
    raw: Mapping[str, Any], request: Mapping[str, Any]
) -> tuple[list[ValidatedArtifact], dict[str, Any]]:
    result = _object(raw, "worker-result.json")
    _strict_keys(
        result,
        "worker-result.json",
        required={"schemaVersion", "runId", "sourceSha256", "artifacts", "pageMaps"},
    )
    if result["schemaVersion"] != SCHEMA_VERSION or not _is_int(result["schemaVersion"]):
        _fail("invalid_worker_result", "worker-result schemaVersion must be 1")
    if result["runId"] != request["runId"]:
        _fail("invalid_worker_result", "worker-result runId does not match request")
    if result["sourceSha256"] != request["source"]["sha256"]:
        _fail("invalid_worker_result", "worker-result source digest does not match request")
    artifacts_raw = result["artifacts"]
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        _fail("invalid_worker_result", "worker-result artifacts must be non-empty")
    artifacts: list[ValidatedArtifact] = []
    seen_roles: set[str] = set()
    for index, item_raw in enumerate(artifacts_raw):
        item = _object(item_raw, f"worker-result.artifacts[{index}]")
        _strict_keys(
            item,
            f"worker-result.artifacts[{index}]",
            required={"role", "path", "mediaType", "sha256", "bytes"},
        )
        role = item["role"]
        if role not in ARTIFACT_ROLES:
            _fail("invalid_artifact", f"unsupported artifact role {role!r}")
        if role in seen_roles:
            _fail("invalid_artifact", f"duplicate artifact role {role!r}")
        seen_roles.add(role)
        if item["path"] != ROLE_PATHS[role]:
            _fail("invalid_artifact", f"artifact {role} has a non-canonical path")
        if item["mediaType"] != ROLE_MEDIA_TYPES[role]:
            _fail("invalid_artifact", f"artifact {role} has an invalid media type")
        digest = _sha256(item["sha256"], f"artifact {role}.sha256")
        size = _positive_int(item["bytes"], f"artifact {role}.bytes")
        artifacts.append(
            ValidatedArtifact(
                role=role,
                path=item["path"],
                media_type=item["mediaType"],
                sha256=digest,
                bytes=size,
            )
        )
    emitted_pdf_roles = seen_roles & PDF_OUTPUT_ROLES
    requested_roles = set(request["outputs"])
    if emitted_pdf_roles != requested_roles:
        _fail(
            "invalid_artifact",
            "worker PDF roles do not exactly match the validated request outputs",
        )
    if not emitted_pdf_roles:
        _fail("invalid_artifact", "worker-result contains no translated PDF")

    page_maps = _object(result["pageMaps"], "worker-result.pageMaps")
    if set(page_maps) != emitted_pdf_roles:
        _fail("invalid_page_map", "pageMaps must contain exactly the emitted PDF roles")
    return artifacts, copy.deepcopy(page_maps)


def _staged_regular_files(staging_dir: Path) -> set[str]:
    try:
        root_stat = staging_dir.lstat()
    except FileNotFoundError:
        _fail("missing_file", "worker staging directory does not exist")
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        _fail("unsafe_file", "worker staging root must be a real directory")
    files: set[str] = set()
    allowed_directory = "artifacts"
    for current_root, directories, names in os.walk(staging_dir, followlinks=False):
        current = Path(current_root)
        for name in list(directories):
            directory = current / name
            mode = directory.lstat().st_mode
            relative = directory.relative_to(staging_dir).as_posix()
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                _fail("unsafe_file", f"staging entry {relative} is not a real directory")
            if relative != allowed_directory:
                _fail("extra_file", f"unexpected staging directory {relative}")
        for name in names:
            path = current / name
            mode = path.lstat().st_mode
            relative = path.relative_to(staging_dir).as_posix()
            if stat.S_ISLNK(mode):
                _fail("unsafe_file", f"staging entry {relative} is a symlink")
            if not stat.S_ISREG(mode):
                _fail("unsafe_file", f"staging entry {relative} is not a regular file")
            files.add(relative)
    return files


def _box_matches(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return all(abs(a - b) <= PDF_GEOMETRY_TOLERANCE for a, b in zip(left, right))


def _validate_page_map(
    raw: Any,
    *,
    role: str,
    source: PdfInspection,
    output: PdfInspection,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) != source.page_count:
        _fail("invalid_page_map", f"{role} page map must contain every source page exactly once")
    normalized: list[dict[str, Any]] = []
    source_pages: set[int] = set()
    output_pages: set[int] = set()
    for index, entry_raw in enumerate(raw):
        entry = _object(entry_raw, f"pageMaps.{role}[{index}]")
        _strict_keys(
            entry,
            f"pageMaps.{role}[{index}]",
            required={"sourcePage", "outputPages"},
        )
        source_page = entry["sourcePage"]
        mapped_outputs = entry["outputPages"]
        if not _is_int(source_page) or not 1 <= source_page <= source.page_count:
            _fail("invalid_page_map", f"{role} contains an out-of-range source page")
        if source_page in source_pages:
            _fail("invalid_page_map", f"{role} maps source page {source_page} more than once")
        if (
            not isinstance(mapped_outputs, list)
            or not mapped_outputs
            or not all(_is_int(page) for page in mapped_outputs)
            or len(mapped_outputs) != len(set(mapped_outputs))
        ):
            _fail("invalid_page_map", f"{role} outputPages must be a non-empty unique integer list")
        for output_page in mapped_outputs:
            if not 1 <= output_page <= output.page_count:
                _fail("invalid_page_map", f"{role} contains an out-of-range output page")
            if output_page in output_pages:
                _fail("invalid_page_map", f"{role} output page {output_page} is mapped twice")
            source_geometry = source.pages[source_page - 1]
            output_geometry = output.pages[output_page - 1]
            if (
                not _box_matches(source_geometry.media_box, output_geometry.media_box)
                or not _box_matches(source_geometry.crop_box, output_geometry.crop_box)
                or source_geometry.rotation != output_geometry.rotation
            ):
                _fail(
                    "page_geometry_mismatch",
                    f"{role} output page {output_page} does not preserve source page {source_page} geometry",
                )
            output_pages.add(output_page)
        source_pages.add(source_page)
        normalized.append({"sourcePage": source_page, "outputPages": list(mapped_outputs)})
    if source_pages != set(range(1, source.page_count + 1)):
        _fail("invalid_page_map", f"{role} does not map every source page")
    if output_pages != set(range(1, output.page_count + 1)):
        _fail("invalid_page_map", f"{role} does not map every output page")
    if role == "translated_mono_pdf":
        expected = [
            {"sourcePage": page, "outputPages": [page]}
            for page in range(1, source.page_count + 1)
        ]
        if output.page_count != source.page_count or normalized != expected:
            _fail("invalid_page_map", "monolingual PDF must preserve one-to-one page identity")
    elif role == "translated_dual_pdf":
        expected = [
            {"sourcePage": page, "outputPages": [page * 2 - 1, page * 2]}
            for page in range(1, source.page_count + 1)
        ]
        if output.page_count != source.page_count * 2 or normalized != expected:
            _fail(
                "invalid_page_map",
                "dual PDF must preserve adjacent original/translated page pairs",
            )
    return normalized


def load_and_validate_worker_result(
    staging_dir: Path,
    *,
    request: Mapping[str, Any],
    source_pdf: Path,
) -> ValidatedWorkerResult:
    """Validate all untrusted staging files and independently inspect output PDFs."""

    staging_dir = Path(staging_dir)
    validated_request = validate_worker_request(request, source_path=Path(source_pdf))
    result_bytes = _read_regular_bytes(
        staging_dir / "worker-result.json",
        field="worker-result.json",
        maximum=MAX_JSON_BYTES,
    )
    try:
        result_text = result_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("invalid_worker_result", f"worker-result.json is not UTF-8: {exc}")
    result_raw = _object(
        _strict_json_loads(result_text, "worker-result.json"),
        "worker-result.json",
    )
    artifacts, page_maps = _validate_result_document(result_raw, validated_request)

    expected_files = {"worker-result.json"} | {artifact.path for artifact in artifacts}
    actual_files = _staged_regular_files(staging_dir)
    extras = actual_files - expected_files
    missing = expected_files - actual_files
    if extras:
        _fail("extra_file", f"worker staging contains undeclared files: {sorted(extras)}")
    if missing:
        _fail("missing_file", f"worker staging is missing declared files: {sorted(missing)}")

    total_output_bytes = 0
    for artifact in artifacts:
        artifact_path = staging_dir / Path(artifact.path)
        actual_digest, actual_bytes = _hash_and_size(
            artifact_path, field=f"artifact {artifact.role}"
        )
        if actual_digest != artifact.sha256:
            _fail("artifact_digest_mismatch", f"artifact {artifact.role} digest does not match")
        if actual_bytes != artifact.bytes:
            _fail("artifact_size_mismatch", f"artifact {artifact.role} size does not match")
        total_output_bytes += actual_bytes
        if artifact.role == "backend_report":
            report_raw = _read_regular_bytes(
                artifact_path,
                field="backend report",
                maximum=min(MAX_JSON_BYTES, validated_request["limits"]["maxOutputBytes"]),
            )
            try:
                report_text = report_raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                _fail("invalid_artifact", f"backend report is not UTF-8: {exc}")
            report = _object(
                _strict_json_loads(report_text, "backend report"),
                "backend report",
            )
            _validate_backend_report_safety(report)
    if total_output_bytes > validated_request["limits"]["maxOutputBytes"]:
        _fail("output_limit", "aggregate worker artifacts exceed request.maxOutputBytes")

    source_inspection = inspect_pdf(
        Path(source_pdf),
        max_pages=validated_request["limits"]["maxPages"],
        reject_active_content=True,
    )
    source_independent_check = _independent_pdf_check(Path(source_pdf))
    if source_independent_check["status"] == "failed":
        _fail("independent_pdf_check_failed", "qpdf rejected the source PDF")
    output_inspections: dict[str, PdfInspection] = {}
    output_independent_checks: dict[str, dict[str, Any]] = {}
    normalized_page_maps: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        if artifact.role not in PDF_OUTPUT_ROLES:
            continue
        pdf_size_limit = min(
            validated_request["limits"]["maxOutputBytes"],
            validated_request["source"]["bytes"] * 5,
        )
        if artifact.bytes > pdf_size_limit:
            _fail(
                "output_limit",
                f"artifact {artifact.role} exceeds five times source size or configured output limit",
            )
        output_page_limit = validated_request["limits"]["maxPages"]
        if artifact.role == "translated_dual_pdf":
            output_page_limit *= 2
        output_inspection = inspect_pdf(
            staging_dir / artifact.path,
            max_pages=output_page_limit,
            reject_active_content=True,
        )
        independent_check = _independent_pdf_check(staging_dir / artifact.path)
        if independent_check["status"] == "failed":
            _fail(
                "independent_pdf_check_failed",
                f"qpdf rejected artifact {artifact.role}",
            )
        unexpected_uris = output_inspection.external_uris - source_inspection.external_uris
        if unexpected_uris:
            _fail(
                "active_content",
                f"artifact {artifact.role} introduces an unexpected external URI action",
            )
        normalized_page_maps[artifact.role] = _validate_page_map(
            page_maps[artifact.role],
            role=artifact.role,
            source=source_inspection,
            output=output_inspection,
        )
        output_inspections[artifact.role] = output_inspection
        output_independent_checks[artifact.role] = independent_check

    index_entries = [artifact.to_index_entry() for artifact in artifacts]
    artifact_index = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": validated_request["runId"],
        "source": {
            "sha256": validated_request["source"]["sha256"],
            "bytes": validated_request["source"]["bytes"],
        },
        "artifacts": index_entries,
    }
    qa_artifacts = []
    for artifact in artifacts:
        item: dict[str, Any] = {
            "role": artifact.role,
            "sha256Verified": True,
            "sizeVerified": True,
            "bytes": artifact.bytes,
        }
        if artifact.role in PDF_OUTPUT_ROLES:
            inspection = output_inspections[artifact.role]
            item.update(
                {
                    "pdfMagic": True,
                    "opens": True,
                    "encrypted": False,
                    "pageCount": inspection.page_count,
                    "pageMapValid": True,
                    "pageGeometryValid": True,
                    "renderedPages": inspection.rendered_pages,
                    "activeContentFindings": [],
                    "actionPolicy": "default-deny-v1",
                    "unexpectedExternalActions": 0,
                    "independentPdfCheck": output_independent_checks[artifact.role],
                }
            )
        qa_artifacts.append(item)
    qa = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": validated_request["runId"],
        # Machine checks are not a semantic/layout review. A human must still
        # approve the immutable candidate explicitly before it can enter the
        # shared manifest.
        "status": "needs_review",
        "automaticChecksStatus": (
            "passed"
            if source_independent_check["status"] == "passed"
            and all(
                check["status"] == "passed"
                for check in output_independent_checks.values()
            )
            else "partial"
        ),
        "source": {
            "sha256Verified": True,
            "bytesVerified": True,
            "encrypted": False,
            "pageCount": source_inspection.page_count,
            "renderedPages": source_inspection.rendered_pages,
            "activeContentFindings": [],
            "actionPolicy": "default-deny-v1",
            "independentPdfCheck": source_independent_check,
        },
        "output": {
            "aggregateBytes": total_output_bytes,
            "maxOutputBytes": validated_request["limits"]["maxOutputBytes"],
        },
        "artifacts": qa_artifacts,
        "pageMaps": normalized_page_maps,
        "segmentCounts": {
            "source": None,
            "translated": None,
            "skipped": None,
            "failed": None,
        },
        "protectedTokenRecall": {
            "doi": None,
            "url": None,
            "citation": None,
            "equationLabel": None,
            "glossaryTerm": None,
        },
        "layoutFindings": {
            "overflow": None,
            "truncation": None,
            "fontShrink": None,
            "collision": None,
            "imageOverlap": None,
            "boundingBoxDrift": None,
        },
        "glyphFindings": {"missingGlyphs": None, "tofu": None},
        "backendNativeQuality": {
            "trust": "untrusted-supporting-evidence",
            "artifact": ROLE_PATHS["backend_report"]
            if any(item.role == "backend_report" for item in artifacts)
            else None,
        },
        "manualReview": {"status": "pending", "notes": []},
    }
    return ValidatedWorkerResult(
        artifacts=tuple(artifacts),
        page_maps=normalized_page_maps,
        source_pdf=source_inspection,
        output_pdfs=output_inspections,
        artifact_index=artifact_index,
        qa=qa,
    )


def _atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def _copy_verified_artifact(
    source: Path,
    destination: Path,
    *,
    expected_digest: str,
    expected_bytes: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with _open_regular(source, field="validated staging artifact") as reader:
        with destination.open("xb") as writer:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    if digest.hexdigest() != expected_digest or size != expected_bytes:
        destination.unlink(missing_ok=True)
        _fail("artifact_changed", "staging artifact changed after validation")


def _sanitize_log(value: str, sensitive_paths: Sequence[Path]) -> str:
    if not isinstance(value, str):
        _fail("invalid_log", "worker_log must be text")
    result = value
    for path in sensitive_paths:
        path_text = str(path)
        if path_text:
            result = result.replace(path_text, "[redacted-path]")
    result = "\n".join(line[:2000] for line in result.splitlines())
    encoded = result.encode("utf-8")[: 256 * 1024]
    return encoded.decode("utf-8", errors="ignore")


def _fsync_tree(root: Path) -> None:
    for current_root, directories, names in os.walk(root):
        current = Path(current_root)
        for name in names:
            descriptor = os.open(current / name, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for name in directories:
            descriptor = os.open(current / name, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normalized_events_text(events: Iterable[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(dict(event), ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n"
        for event in events
    )


def publish_candidate_run(
    *,
    output_root: Path,
    slug: str,
    source_pdf: Path,
    staging_dir: Path,
    request: Mapping[str, Any],
    health: Mapping[str, Any],
    events_ndjson: str | bytes | Iterable[str],
    approved_fork_revisions: Collection[str] = (),
    worker_log: str = "",
    run_purpose: str = "unclassified",
    promotion_eligible: bool = False,
) -> Path:
    """Validate and atomically publish one immutable candidate run.

    The destination is never passed to the backend. Existing run IDs are never
    replaced, and no staging content becomes visible before the final rename.
    """

    output_root = Path(output_root)
    source_pdf = Path(source_pdf)
    staging_dir = Path(staging_dir)
    validated_request = validate_worker_request(request, source_path=source_pdf)
    validated_health = validate_backend_health(
        health,
        approved_fork_revisions=approved_fork_revisions,
        required_outputs=validated_request["outputs"],
    )
    purpose = _identifier(run_purpose, "run_purpose")
    if not isinstance(promotion_eligible, bool):
        _fail("invalid_schema", "promotion_eligible must be a boolean")
    if promotion_eligible and purpose != "semantic_translation":
        _fail("policy_refusal", "only semantic translation runs may be promotable")
    events = validate_ndjson_events(
        events_ndjson,
        run_id=validated_request["runId"],
        require_terminal=True,
    )
    if events[-1]["type"] != "completed":
        _fail("worker_failed", "a failed event stream cannot be published")
    if "backendId" in events[0] and events[0]["backendId"] != validated_health["backendId"]:
        _fail("invalid_event", "started event backendId does not match health")

    validated_result = load_and_validate_worker_result(
        staging_dir,
        request=validated_request,
        source_pdf=source_pdf,
    )
    event_artifacts = {
        event["role"]: event for event in events if event["type"] == "artifact"
    }
    if len(event_artifacts) != sum(event["type"] == "artifact" for event in events):
        _fail("invalid_event", "event stream declares a duplicate artifact role")
    for artifact in validated_result.artifacts:
        event = event_artifacts.get(artifact.role)
        if event is None:
            _fail("invalid_event", f"event stream omitted artifact {artifact.role}")
        if (
            event["path"] != artifact.path
            or event["mediaType"] != artifact.media_type
            or event["sha256"] != artifact.sha256
            or event["bytes"] != artifact.bytes
        ):
            _fail("invalid_event", f"artifact event for {artifact.role} disagrees with result")
    if set(event_artifacts) != {artifact.role for artifact in validated_result.artifacts}:
        _fail("invalid_event", "event stream declares an artifact absent from worker-result")

    slug_value = _run_id(slug, "slug")
    try:
        root_stat = output_root.lstat()
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            _fail("unsafe_output_root", "output_root must be a real directory")
    except FileNotFoundError:
        output_root.mkdir(parents=True, exist_ok=False)
    slug_root = output_root / slug_value
    runs_root = slug_root / "pdf-runs"
    for directory in (slug_root, runs_root):
        if directory.exists():
            mode = directory.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                _fail("unsafe_output_root", f"{directory.name} must be a real directory")
        else:
            directory.mkdir()

    run_id = validated_request["runId"]
    destination = runs_root / run_id
    if os.path.lexists(destination):
        _fail("run_exists", f"candidate run {run_id} already exists and is immutable")
    lock_path = runs_root / f".{run_id}.publish.lock"
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        _fail("run_exists", f"candidate run {run_id} is already being published")
    os.close(lock_descriptor)
    host_staging = Path(tempfile.mkdtemp(prefix=f".{run_id}.staging-", dir=runs_root))
    renamed = False
    try:
        if os.path.lexists(destination):
            _fail("run_exists", f"candidate run {run_id} already exists and is immutable")
        for artifact in validated_result.artifacts:
            _copy_verified_artifact(
                staging_dir / artifact.path,
                host_staging / artifact.path,
                expected_digest=artifact.sha256,
                expected_bytes=artifact.bytes,
            )

        request_bytes = _json_bytes(validated_request)
        artifact_index_bytes = _json_bytes(validated_result.artifact_index)
        qa_bytes = _json_bytes(validated_result.qa)
        events_text = _normalized_events_text(events)
        artifact_index_digest = hashlib.sha256(artifact_index_bytes).hexdigest()
        qa_digest = hashlib.sha256(qa_bytes).hexdigest()
        progress_events = [event for event in events if event["type"] == "progress"]
        last_progress = (
            {
                "stage": progress_events[-1]["stage"],
                "completed": progress_events[-1]["completed"],
                "total": progress_events[-1]["total"],
            }
            if progress_events
            else None
        )
        run_document = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": run_id,
            "slug": slug_value,
            "sourceType": "pdf",
            "source": copy.deepcopy(validated_request["source"]),
            "targetLanguage": validated_request["translation"]["targetLanguage"],
            "backendId": validated_health["backendId"],
            "adapterVersion": validated_health["adapterVersion"],
            "engineVersion": validated_health["engineVersion"],
            "dependencies": copy.deepcopy(validated_health["dependencies"]),
            "sourceRevision": validated_health["sourceRevision"],
            "forkRevision": validated_health["forkRevision"],
            "buildDigest": validated_health["buildDigest"],
            "imageDigest": validated_health["imageDigest"],
            "sbomSha256": validated_health["sbomSha256"],
            "lockSha256": validated_health["lockSha256"],
            "modelDigests": copy.deepcopy(validated_health.get("modelDigests", {})),
            "fontDigests": copy.deepcopy(validated_health.get("fontDigests", {})),
            "modelId": validated_request["translation"]["modelId"],
            "profileId": validated_request["translation"]["profileId"],
            "promptRevision": validated_request["translation"]["promptRevision"],
            "glossarySha256": validated_request["translation"]["glossarySha256"],
            "state": (
                "succeeded"
                if validated_result.qa["status"] == "passed"
                else "needs_review"
            ),
            "purpose": purpose,
            "promotionEligible": promotion_eligible,
            "progress": last_progress,
            "timestamps": {
                "startedAt": events[0]["time"],
                "completedAt": events[-1]["time"],
            },
            "resourceMetrics": {},
            "artifactIndexSha256": artifact_index_digest,
            "qaSha256": qa_digest,
        }
        files = {
            "request.json": request_bytes,
            "run.json": _json_bytes(run_document),
            "events.ndjson": events_text.encode("utf-8"),
            "worker.log": _sanitize_log(
                worker_log,
                (source_pdf, staging_dir, output_root),
            ).encode("utf-8"),
            "artifact-index.json": artifact_index_bytes,
            "qa.json": qa_bytes,
        }
        for relative, data in files.items():
            target = host_staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        _fsync_tree(host_staging)
        os.rename(host_staging, destination)
        renamed = True
        parent_descriptor = os.open(runs_root, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        return destination
    finally:
        lock_path.unlink(missing_ok=True)
        if not renamed and host_staging.exists():
            shutil.rmtree(host_staging)


def _promote_into_manifest(
    *,
    manifest_path: Path,
    index_source: Mapping[str, Any],
    run: Mapping[str, Any],
    run_id: str,
    role: str,
    artifact: Mapping[str, Any],
    digest: str,
    size: int,
    promoted_at: str,
    reviewer: str,
) -> dict[str, Any]:
    with paper_manifest_lock(manifest_path):
        manifest = _object(
            _strict_json_loads(
                _read_regular_bytes(
                    manifest_path,
                    field="papertrans-job.json",
                    maximum=MAX_JSON_BYTES,
                ).decode("utf-8"),
                "papertrans-job.json",
            ),
            "papertrans-job.json",
        )
        if manifest.get("sourceType") != "pdf":
            _fail("invalid_manifest", "only a PDF job can receive a translated PDF")
        manifest_source = _object(manifest.get("source"), "papertrans-job.source")
        if manifest_source.get("sha256") != index_source["sha256"]:
            _fail("source_digest_mismatch", "candidate source does not match the PDF job")
        if not isinstance(manifest.get("artifacts"), dict):
            _fail("invalid_manifest", "papertrans-job.artifacts must be an object")
        prior_status = copy.deepcopy(manifest.get("status"))
        relative_artifact = f"pdf-runs/{run_id}/{artifact['path']}"
        manifest["artifacts"]["translatedPdf"] = relative_artifact
        manifest["pdfTranslation"] = {
            "schemaVersion": SCHEMA_VERSION,
            "runId": run_id,
            "backendId": run.get("backendId"),
            "role": role,
            "artifact": relative_artifact,
            "sha256": digest,
            "bytes": size,
            "artifactIndex": f"pdf-runs/{run_id}/artifact-index.json",
            "qa": f"pdf-runs/{run_id}/qa.json",
            "promotedAt": promoted_at,
            "approvedBy": reviewer,
        }
        # The main pipeline state belongs to the existing import/translation pipeline.
        manifest["status"] = prior_status
        mode = stat.S_IMODE(manifest_path.stat().st_mode)
        _atomic_write_bytes(manifest_path, _json_bytes(manifest), mode=mode)
        return copy.deepcopy(manifest)


def promote_candidate_pdf(
    *,
    output_root: Path,
    slug: str,
    run_id: str,
    approved_by: str,
    role: str = "translated_mono_pdf",
    promoted_at: str | None = None,
) -> dict[str, Any]:
    """Explicitly promote a validated candidate without changing the job status."""

    output_root = Path(output_root)
    slug_value = _run_id(slug, "slug")
    run_id_value = _run_id(run_id)
    if role not in PDF_OUTPUT_ROLES:
        _fail("invalid_artifact", "only a translated PDF role can be promoted")
    reviewer = _identifier(approved_by, "approved_by")
    if promoted_at is None:
        promoted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _event_time(promoted_at, "promoted_at")

    slug_root = output_root / slug_value
    run_root = slug_root / "pdf-runs" / run_id_value
    if run_root.is_symlink() or not run_root.is_dir():
        _fail("missing_run", "candidate run does not exist as a real directory")
    index_bytes = _read_regular_bytes(
        run_root / "artifact-index.json",
        field="artifact-index.json",
        maximum=MAX_JSON_BYTES,
    )
    index_raw = _strict_json_loads(
        index_bytes.decode("utf-8"),
        "artifact-index.json",
    )
    index = _object(index_raw, "artifact-index.json")
    _strict_keys(
        index,
        "artifact-index.json",
        required={"schemaVersion", "runId", "source", "artifacts"},
    )
    if index["schemaVersion"] != SCHEMA_VERSION or index["runId"] != run_id_value:
        _fail("invalid_run", "artifact index identity is invalid")
    index_source = _object(index["source"], "artifact-index.source")
    _strict_keys(
        index_source,
        "artifact-index.source",
        required={"sha256", "bytes"},
    )
    _sha256(index_source["sha256"], "artifact-index.source.sha256")
    _positive_int(index_source["bytes"], "artifact-index.source.bytes")
    if not isinstance(index["artifacts"], list):
        _fail("invalid_artifact", "artifact-index.artifacts must be an array")
    qa_bytes = _read_regular_bytes(
        run_root / "qa.json", field="qa.json", maximum=MAX_JSON_BYTES
    )
    qa = _object(
        _strict_json_loads(qa_bytes.decode("utf-8"), "qa.json"),
        "qa.json",
    )
    if qa.get("schemaVersion") != SCHEMA_VERSION or qa.get("runId") != run_id_value:
        _fail("invalid_run", "candidate QA identity is invalid")
    if qa.get("status") not in {"passed", "needs_review"}:
        _fail("qa_not_passed", "candidate QA is not reviewable")
    pdf_qa_items = [
        item
        for item in qa.get("artifacts", [])
        if isinstance(item, dict) and item.get("role") in PDF_OUTPUT_ROLES
    ]
    independent_qa_passed = qa.get("automaticChecksStatus") == "passed" and bool(
        pdf_qa_items
    ) and not any(
        item.get("independentPdfCheck")
        != {"tool": "qpdf", "status": "passed"}
        for item in pdf_qa_items
    )
    run_bytes = _read_regular_bytes(
        run_root / "run.json", field="run.json", maximum=MAX_JSON_BYTES
    )
    run = _object(
        _strict_json_loads(run_bytes.decode("utf-8"), "run.json"),
        "run.json",
    )
    if run.get("schemaVersion") != SCHEMA_VERSION or run.get("runId") != run_id_value:
        _fail("invalid_run", "candidate run identity is invalid")
    if run.get("state") not in {"succeeded", "needs_review"}:
        _fail("invalid_run", "candidate run is not publishable")
    if run.get("purpose") != "semantic_translation" or run.get(
        "promotionEligible"
    ) is not True:
        _fail(
            "promotion_refused",
            "candidate is not a host-approved semantic translation run",
        )
    if not independent_qa_passed:
        _fail(
            "qa_not_passed",
            "candidate requires a successful independent qpdf check before promotion",
        )
    run_source = _object(run.get("source"), "run.source")
    if (
        run_source.get("sha256") != index_source["sha256"]
        or run_source.get("bytes") != index_source["bytes"]
    ):
        _fail("source_digest_mismatch", "run source disagrees with artifact index")
    if run.get("artifactIndexSha256") != hashlib.sha256(index_bytes).hexdigest():
        _fail("artifact_changed", "artifact index changed after run publication")
    if run.get("qaSha256") != hashlib.sha256(qa_bytes).hexdigest():
        _fail("artifact_changed", "candidate QA changed after run publication")

    matching = [item for item in index["artifacts"] if item.get("role") == role]
    if len(matching) != 1:
        _fail("invalid_artifact", f"candidate has no unique {role} artifact")
    artifact = _object(matching[0], f"artifact {role}")
    _strict_keys(
        artifact,
        f"artifact {role}",
        required={"role", "path", "mediaType", "sha256", "bytes"},
    )
    if artifact["path"] != ROLE_PATHS[role] or artifact["mediaType"] != "application/pdf":
        _fail("invalid_artifact", "candidate artifact metadata is not canonical")
    digest = _sha256(artifact["sha256"], f"artifact {role}.sha256")
    size = _positive_int(artifact["bytes"], f"artifact {role}.bytes")
    artifact_path = run_root / artifact["path"]
    actual_digest, actual_size = _hash_and_size(artifact_path, field=f"artifact {role}")
    if actual_digest != digest or actual_size != size:
        _fail("artifact_changed", "candidate artifact changed after publication")

    return _promote_into_manifest(
        manifest_path=slug_root / "work" / "papertrans-job.json",
        index_source=index_source,
        run=run,
        run_id=run_id_value,
        role=role,
        artifact=artifact,
        digest=digest,
        size=size,
        promoted_at=promoted_at,
        reviewer=reviewer,
    )


__all__ = [
    "ARTIFACT_ROLES",
    "MAX_DEADLINE_SECONDS",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_OUTPUT_PAGES",
    "MAX_PAGES",
    "PDF_OUTPUT_ROLES",
    "PdfInspection",
    "PdfPageGeometry",
    "PdfTranslationContractError",
    "ValidatedArtifact",
    "ValidatedWorkerResult",
    "inspect_pdf",
    "load_and_validate_worker_result",
    "load_worker_request",
    "promote_candidate_pdf",
    "publish_candidate_run",
    "validate_backend_health",
    "validate_ndjson_events",
    "validate_worker_request",
]
