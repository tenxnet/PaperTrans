from __future__ import annotations

import copy
import importlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from .deterministic_structure import visual_caption_score
from .structure import render_visual_objects, validate_structure_batch


class DoclingAdapterError(ValueError):
    """Raised when a Docling document cannot be mapped to PaperTrans IR."""


class DoclingUnavailableError(RuntimeError):
    """Raised when PDF conversion is requested without the optional dependency."""


class DoclingWorkerError(RuntimeError):
    """Raised when the isolated Docling conversion worker fails."""


class DoclingWorkerTimeoutError(DoclingWorkerError):
    """Raised when the isolated Docling conversion worker times out."""


class DoclingPartialConversionError(DoclingAdapterError):
    """Raised when Docling returns only a partial PDF conversion."""


_DOCLING_WORKER_LOG_LIMIT_BYTES = 256 * 1024
_DOCLING_PARTIAL_EXIT_CODE = 75
_DOCLING_DOCUMENT_TIMEOUT_SECONDS = 10 * 60.0
_DOCLING_WORKER_TIMEOUT_SECONDS = _DOCLING_DOCUMENT_TIMEOUT_SECONDS + 30.0
_CONTENT_COLLECTIONS = (
    "texts",
    "pictures",
    "tables",
    "key_value_items",
    "form_items",
    "field_regions",
    "field_items",
)
_VISUAL_LABELS = {
    "chart": "figure",
    "diagram": "figure",
    "document_index": "table",
    "formula": "equation",
    "graph": "figure",
    "picture": "figure",
    "table": "table",
    "code": "algorithm",
}
_ROLE_BY_LABEL = {
    "abstract": "abstract",
    "affiliation": "affiliation",
    "author": "author",
    "caption": "caption",
    "code": "algorithm",
    "formula": "equation",
    "footnote": "footnote",
    "list_item": "list_item",
    "metadata": "metadata",
    "page_footer": "footer",
    "page_header": "header",
    "page_number": "page_number",
    "reference": "reference",
    "section_header": "heading",
    "title": "title",
}
_FURNITURE_ROLES = {"footer", "header", "page_number"}
_BODY_ROLES = {"abstract", "footnote", "list_item", "paragraph", "reference"}
_NUMBERED_HEADING = re.compile(
    r"^\s*((?:[1-9]\d*)(?:\.\d+){0,5})[.)]?\s+\S+"
)
_APPENDIX_HEADING = re.compile(
    r"^\s*Appendix\s+([A-Z](?:\.\d+){0,5})[.)]?\s+\S+", re.IGNORECASE
)
_REFERENCE_LABEL = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s*")
_NUMERIC_CITATION = re.compile(
    r"\[(?:\d+[a-z]?(?:\s*[-,;]\s*\d+[a-z]?)*|\d+\s*(?:–|—)\s*\d+)\]"
)
_AUTHOR_YEAR_CITATION = re.compile(
    r"\((?:[A-Z][A-Za-z'’\-]+(?:\s+et\s+al\.)?"
    r"(?:\s+and\s+[A-Z][A-Za-z'’\-]+)?\s*,?\s*(?:19|20)\d{2}[a-z]?"
    r"(?:\s*;\s*)?)+\)"
)
_OBJECT_REFERENCE = re.compile(
    r"\b(?:Figure|Fig\.?|Table|Algorithm|Equation|Eq\.?)\s*"
    r"\(?((?:[A-Z]\.)?\d+(?:[.\-][A-Za-z0-9]+)*)\)?",
    re.IGNORECASE,
)
_OBJECT_LABEL = re.compile(
    r"^\s*(Figure|Fig\.?|Table|Algorithm|Equation|Eq\.?)\s*"
    r"\(?((?:[A-Z]\.)?\d+(?:[.\-][A-Za-z0-9]+)*)\)?",
    re.IGNORECASE,
)


def docling_document_to_dict(document: Any) -> dict[str, Any]:
    """Return Docling's native JSON representation without importing Docling.

    Mapping and JSON-string inputs make the adapter independently testable. A real
    ``DoclingDocument`` is handled through its public ``export_to_dict`` method.
    """

    value: Any
    if isinstance(document, Mapping):
        value = copy.deepcopy(dict(document))
    elif isinstance(document, (str, bytes, bytearray)):
        try:
            value = json.loads(document)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DoclingAdapterError("Docling JSON input is invalid") from error
    elif callable(getattr(document, "export_to_dict", None)):
        value = document.export_to_dict()
    elif callable(getattr(document, "model_dump", None)):
        try:
            value = document.model_dump(mode="json")
        except TypeError:
            value = document.model_dump()
    else:
        raise DoclingAdapterError(
            "Expected a DoclingDocument, mapping, or Docling JSON string"
        )
    if not isinstance(value, Mapping):
        raise DoclingAdapterError("Docling document export must be a JSON object")
    return copy.deepcopy(dict(value))


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sanitized_conversion_summary(errors: Any) -> str:
    messages: list[str] = []
    for error in _as_sequence(errors)[:5]:
        if isinstance(error, Mapping):
            raw_message = error.get("error_message")
        else:
            raw_message = getattr(error, "error_message", None)
        if raw_message is None:
            continue
        message = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw_message))
        message = re.sub(r"\s+", " ", message).strip()
        if message:
            messages.append(message[:240])
    return "; ".join(messages)[:1000] or "no sanitized diagnostics were provided"


def _runtime_document_issues(document: Mapping[str, Any]) -> list[str]:
    pages = _collection_values(document.get("pages", {}))
    if not pages:
        return ["no page metadata"]
    page_numbers: set[int] = set()
    issues_by_page: dict[int, list[str]] = {}
    for key, page in pages:
        try:
            page_number = int(page.get("page_no", page.get("page_number", key)))
        except (TypeError, ValueError):
            continue
        if page_number <= 0:
            continue
        page_numbers.add(page_number)
        size = page.get("size") if isinstance(page.get("size"), Mapping) else page
        width = _number(size.get("width") if isinstance(size, Mapping) else None)
        height = _number(size.get("height") if isinstance(size, Mapping) else None)
        if width <= 0 or height <= 0:
            issues_by_page.setdefault(page_number, []).append("zero or missing page size")
    has_body_text = any(
        _content_layer(item) != "furniture"
        and bool(_text(item))
        and _ROLE_BY_LABEL.get(_label(item), "paragraph") in _BODY_ROLES
        for _key, item in _collection_values(document.get("texts", []))
    )
    issues = [
        f"page {page_number} ({', '.join(issues_by_page[page_number])})"
        for page_number in sorted(issues_by_page)
    ]
    if not has_body_text:
        issues.append("document (no textual body content)")
    return issues


def convert_pdf_with_docling(source: Path) -> dict[str, Any]:
    """Convert a digital PDF with Docling OCR explicitly disabled.

    Docling's default PDF pipeline may initialize or download OCR models even for
    text-native PDFs. The PoC deliberately selects the PDF pipeline and disables
    OCR; scanned-document support can be exposed as a separate opt-in later.
    """

    try:
        converter_module = importlib.import_module("docling.document_converter")
        base_models_module = importlib.import_module("docling.datamodel.base_models")
        accelerator_options_module = importlib.import_module(
            "docling.datamodel.accelerator_options"
        )
        object_detection_options_module = importlib.import_module(
            "docling.datamodel.object_detection_engine_options"
        )
        pipeline_options_module = importlib.import_module(
            "docling.datamodel.pipeline_options"
        )
    except (ImportError, ModuleNotFoundError) as error:
        raise DoclingUnavailableError(
            "Docling is not installed; install the optional dependency with "
            "`uv sync --extra docling`."
        ) from error
    try:
        importlib.import_module("onnxruntime")
    except (ImportError, ModuleNotFoundError) as error:
        raise DoclingUnavailableError(
            "Docling's ONNX layout backend is unavailable; install the "
            "PaperTrans `docling` extra with `uv sync --extra docling`."
        ) from error
    converter_class = getattr(converter_module, "DocumentConverter", None)
    pdf_format_option_class = getattr(converter_module, "PdfFormatOption", None)
    input_format = getattr(base_models_module, "InputFormat", None)
    conversion_status = getattr(base_models_module, "ConversionStatus", None)
    accelerator_device = getattr(accelerator_options_module, "AcceleratorDevice", None)
    accelerator_options_class = getattr(
        accelerator_options_module, "AcceleratorOptions", None
    )
    onnx_engine_options_class = getattr(
        object_detection_options_module,
        "OnnxRuntimeObjectDetectionEngineOptions",
        None,
    )
    pdf_pipeline_options_class = getattr(
        pipeline_options_module, "PdfPipelineOptions", None
    )
    layout_options_class = getattr(
        pipeline_options_module, "LayoutObjectDetectionOptions", None
    )
    heading_hierarchy_options_class = getattr(
        pipeline_options_module, "HeadingHierarchyOptions", None
    )
    if (
        converter_class is None
        or pdf_format_option_class is None
        or input_format is None
        or conversion_status is None
        or accelerator_device is None
        or accelerator_options_class is None
        or onnx_engine_options_class is None
        or pdf_pipeline_options_class is None
        or layout_options_class is None
        or heading_hierarchy_options_class is None
        or not hasattr(input_format, "PDF")
        or not hasattr(conversion_status, "SUCCESS")
        or not hasattr(accelerator_device, "CPU")
    ):
        raise DoclingUnavailableError(
            "The installed Docling package does not expose the required ONNX PDF pipeline API"
        )
    raw_document_timeout = os.environ.get("PAPERTRANS_DOCLING_DOCUMENT_TIMEOUT")
    try:
        document_timeout = (
            float(raw_document_timeout)
            if raw_document_timeout is not None
            else _DOCLING_DOCUMENT_TIMEOUT_SECONDS
        )
    except ValueError as error:
        raise DoclingAdapterError(
            "PAPERTRANS_DOCLING_DOCUMENT_TIMEOUT must be a positive number"
        ) from error
    if not math.isfinite(document_timeout) or document_timeout <= 0:
        raise DoclingAdapterError(
            "PAPERTRANS_DOCLING_DOCUMENT_TIMEOUT must be a positive number"
        )
    raw_parser_threads = os.environ.get("PAPERTRANS_DOCLING_PARSER_THREADS")
    parser_threads: int | None = None
    if raw_parser_threads is not None:
        try:
            parser_threads = int(raw_parser_threads)
        except ValueError as error:
            raise DoclingAdapterError(
                "PAPERTRANS_DOCLING_PARSER_THREADS must be a positive integer"
            ) from error
        if parser_threads <= 0:
            raise DoclingAdapterError(
                "PAPERTRANS_DOCLING_PARSER_THREADS must be a positive integer"
            )
    pipeline_kwargs: dict[str, Any] = {
        "do_ocr": False,
        "document_timeout": document_timeout,
        "accelerator_options": accelerator_options_class(
            device=accelerator_device.CPU
        ),
        "layout_options": layout_options_class(
            engine_options=onnx_engine_options_class(
                providers=["CPUExecutionProvider"]
            )
        ),
        "heading_hierarchy_options": heading_hierarchy_options_class(enabled=True),
        "generate_parsed_pages": True,
    }
    artifacts_path = os.environ.get("PAPERTRANS_DOCLING_ARTIFACTS_PATH")
    if artifacts_path:
        pipeline_kwargs["artifacts_path"] = Path(artifacts_path)
    pdf_pipeline_options = pdf_pipeline_options_class(**pipeline_kwargs)
    format_kwargs: dict[str, Any] = {"pipeline_options": pdf_pipeline_options}
    if parser_threads is not None:
        try:
            backend_options_module = importlib.import_module(
                "docling.datamodel.backend_options"
            )
        except (ImportError, ModuleNotFoundError) as error:
            raise DoclingUnavailableError(
                "The installed Docling package does not expose parser-thread controls"
            ) from error
        backend_options_class = getattr(
            backend_options_module, "ThreadedDoclingParseBackendOptions", None
        )
        if backend_options_class is None:
            raise DoclingUnavailableError(
                "The installed Docling package does not expose parser-thread controls"
            )
        format_kwargs["backend_options"] = backend_options_class(
            parser_threads=parser_threads
        )
    converter = converter_class(
        format_options={
            input_format.PDF: pdf_format_option_class(**format_kwargs)
        }
    )
    result = converter.convert(Path(source), raises_on_error=False)
    status = getattr(result, "status", None)
    if status != conversion_status.SUCCESS:
        raw_status = getattr(status, "value", status)
        status_label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(raw_status))[:64]
        summary = _sanitized_conversion_summary(getattr(result, "errors", []))
        error_class = (
            DoclingPartialConversionError
            if status_label == "partial_success"
            else DoclingAdapterError
        )
        raise error_class(
            f"Docling conversion status {status_label or 'unknown'}: {summary}"
        )
    document = getattr(result, "document", None)
    if document is None:
        raise DoclingAdapterError("Docling conversion did not return a document")
    exported = docling_document_to_dict(document)
    runtime_issues = _runtime_document_issues(exported)
    if runtime_issues:
        raise DoclingAdapterError(
            "Docling conversion produced unusable pages: " + "; ".join(runtime_issues)
        )
    return exported


def _capped_worker_log(retained: bytes, total_size: int, suffix: bytes = b"") -> bytes:
    suffix_value = b""
    if suffix:
        suffix_value = b"\n" + suffix.rstrip() + b"\n"
    prefix = b""
    for _ in range(8):
        budget = max(
            0,
            _DOCLING_WORKER_LOG_LIMIT_BYTES - len(prefix) - len(suffix_value),
        )
        omitted = max(0, total_size - min(len(retained), budget))
        next_prefix = (
            f"[PaperTrans truncated {omitted} stderr bytes]\n".encode()
            if omitted
            else b""
        )
        if next_prefix == prefix:
            break
        prefix = next_prefix
    budget = max(
        0,
        _DOCLING_WORKER_LOG_LIMIT_BYTES - len(prefix) - len(suffix_value),
    )
    tail = retained[-budget:] if budget else b""
    return prefix + tail + suffix_value


def _drain_worker_stderr(read_fd: int, retained: bytearray, totals: list[int]) -> None:
    """Continuously drain a worker pipe while retaining only a bounded tail."""

    with os.fdopen(read_fd, "rb", buffering=0) as handle:
        while chunk := handle.read(64 * 1024):
            totals[0] += len(chunk)
            retained.extend(chunk)
            overflow = len(retained) - _DOCLING_WORKER_LOG_LIMIT_BYTES
            if overflow > 0:
                del retained[:overflow]


def _cleanup_worker_output(output_path: Path) -> None:
    output_path.unlink(missing_ok=True)
    for temporary_path in output_path.parent.glob(f".{output_path.name}.*.tmp"):
        temporary_path.unlink(missing_ok=True)


def _run_docling_worker(
    source: Path,
    work_dir: Path,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run only native Docling conversion in a crash-isolated child process."""

    source = Path(source).resolve()
    work_dir = Path(work_dir).resolve()
    if timeout_seconds is None:
        raw_timeout = os.environ.get("PAPERTRANS_DOCLING_WORKER_TIMEOUT")
        try:
            timeout_seconds = (
                float(raw_timeout)
                if raw_timeout is not None
                else _DOCLING_WORKER_TIMEOUT_SECONDS
            )
        except ValueError as error:
            raise DoclingAdapterError(
                "PAPERTRANS_DOCLING_WORKER_TIMEOUT must be a positive number"
            ) from error
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise DoclingAdapterError(
            "PAPERTRANS_DOCLING_WORKER_TIMEOUT must be a positive number"
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = work_dir / f".docling-document-{uuid4().hex}.json"
    canonical_output_path = work_dir / "docling-document.json"
    log_path = work_dir / "docling-worker.log"
    command = [
        sys.executable,
        "-m",
        "papertrans.docling_worker",
        str(source),
        str(output_path),
    ]
    retained_stderr = bytearray()
    stderr_totals = [0]

    def retain_log_chunk(chunk: bytes) -> None:
        stderr_totals[0] += len(chunk)
        retained_stderr.extend(chunk)
        overflow = len(retained_stderr) - _DOCLING_WORKER_LOG_LIMIT_BYTES
        if overflow > 0:
            del retained_stderr[:overflow]

    def run_attempt(
        child_env: Mapping[str, str] | None = None,
        attempt_work_dir: Path | None = None,
    ) -> tuple[Any | None, subprocess.TimeoutExpired | None, BaseException | None]:
        read_fd, write_fd = os.pipe()
        stderr_reader = threading.Thread(
            target=_drain_worker_stderr,
            args=(read_fd, retained_stderr, stderr_totals),
            name="papertrans-docling-stderr",
            daemon=True,
        )
        stderr_reader.start()
        attempt_process: Any | None = None
        attempt_timeout: subprocess.TimeoutExpired | None = None
        attempt_error: BaseException | None = None
        try:
            with os.fdopen(write_fd, "wb", buffering=0) as stderr_writer:
                run_kwargs: dict[str, Any] = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": stderr_writer,
                    "cwd": attempt_work_dir or work_dir,
                    "check": False,
                    "timeout": timeout_seconds,
                }
                if child_env is not None:
                    run_kwargs["env"] = dict(child_env)
                try:
                    attempt_process = subprocess.run(command, **run_kwargs)
                except subprocess.TimeoutExpired as error:
                    attempt_timeout = error
                except BaseException as error:
                    attempt_error = error
        finally:
            stderr_reader.join()
        return attempt_process, attempt_timeout, attempt_error

    process, timeout_error, run_error = run_attempt()
    retryable_signals = {
        -value
        for value in (
            getattr(signal, "SIGSEGV", None),
            getattr(signal, "SIGBUS", None),
            getattr(signal, "SIGABRT", None),
        )
        if isinstance(value, int)
    }
    retry_reason: str | None = None
    if process is not None and process.returncode in retryable_signals:
        retry_reason = f"native signal {process.returncode}"
    elif process is not None and process.returncode == _DOCLING_PARTIAL_EXIT_CODE:
        retry_reason = "partial conversion"
    if (
        timeout_error is None
        and run_error is None
        and process is not None
        and retry_reason is not None
        and os.environ.get("PAPERTRANS_DOCLING_PARSER_THREADS") != "1"
    ):
        _cleanup_worker_output(output_path)
        retain_log_chunk(
            (
                f"\nPaperTrans: Docling worker returned {retry_reason}; "
                "retrying once with parser_threads=1.\n"
            ).encode()
        )
        retry_env = os.environ.copy()
        retry_env["PAPERTRANS_DOCLING_PARSER_THREADS"] = "1"
        with tempfile.TemporaryDirectory(
            prefix=".docling-retry-", dir=work_dir
        ) as retry_directory:
            process, timeout_error, run_error = run_attempt(
                retry_env, Path(retry_directory)
            )
    if timeout_error is not None:
        log = _capped_worker_log(
            bytes(retained_stderr),
            stderr_totals[0],
            suffix=(
                f"PaperTrans: Docling worker timed out after {timeout_seconds:g} seconds."
            ).encode(),
        )
        try:
            log_path.write_bytes(log)
        finally:
            _cleanup_worker_output(output_path)
        raise DoclingWorkerTimeoutError(
            f"Docling worker timed out after {timeout_seconds:g} seconds; see {log_path}"
        ) from timeout_error
    if run_error is not None:
        try:
            log_path.write_bytes(
                _capped_worker_log(bytes(retained_stderr), stderr_totals[0])
            )
        finally:
            _cleanup_worker_output(output_path)
        if isinstance(run_error, Exception):
            raise DoclingWorkerError(
                f"Docling worker could not start; see {log_path}"
            ) from run_error
        raise run_error.with_traceback(run_error.__traceback__)
    try:
        log_path.write_bytes(
            _capped_worker_log(bytes(retained_stderr), stderr_totals[0])
        )
        if process is None:
            raise DoclingWorkerError(f"Docling worker did not start; see {log_path}")
        if process.returncode != 0:
            raise DoclingWorkerError(
                f"Docling worker exited with status {process.returncode}; see {log_path}"
            )
        try:
            value = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DoclingWorkerError(
                f"Docling worker did not produce valid JSON at {output_path}; see {log_path}"
            ) from error
        document = docling_document_to_dict(value)
        _write_json_atomic(canonical_output_path, document)
        return document
    finally:
        _cleanup_worker_output(output_path)


def _as_sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    if isinstance(value, Mapping):
        return [value]
    return []


def _collection_values(value: Any) -> list[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        return [
            (str(key), item)
            for key, item in value.items()
            if isinstance(item, Mapping)
        ]
    return [
        (str(index), item)
        for index, item in enumerate(_as_sequence(value))
        if isinstance(item, Mapping)
    ]


def _ref_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for field in ("$ref", "cref", "self_ref"):
            candidate = value.get(field)
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _safe_id(value: str, prefix: str = "dl") -> str:
    normalized = value.removeprefix("#/")
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", normalized).strip("-").lower()
    return f"{prefix}-{normalized or 'item'}"


def _raw_text(item: Mapping[str, Any]) -> str:
    original = item.get("orig")
    if isinstance(original, str) and original.strip():
        return original
    value = item.get("text")
    return value if isinstance(value, str) else ""


def _text(item: Mapping[str, Any]) -> str:
    return _raw_text(item).strip()


def _label(item: Mapping[str, Any]) -> str:
    value = item.get("label")
    if value is None:
        return ""
    return str(value).rsplit(".", 1)[-1].strip().lower()


def _content_layer(item: Mapping[str, Any]) -> str:
    value = item.get("content_layer")
    if value is None:
        return ""
    return str(value).rsplit(".", 1)[-1].strip().lower()


def _provenance(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [value for value in _as_sequence(item.get("prov")) if isinstance(value, Mapping)]


def _page_number(provenance: Mapping[str, Any]) -> int | None:
    raw = provenance.get("page_no", provenance.get("page_number"))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _page_dimensions(document: Mapping[str, Any]) -> tuple[dict[int, tuple[float, float]], list[str]]:
    dimensions: dict[int, tuple[float, float]] = {}
    warnings: list[str] = []
    pages = document.get("pages", {})
    for key, page in _collection_values(pages):
        raw_page_number = page.get("page_no", page.get("page_number", key))
        try:
            page_number = int(raw_page_number)
        except (TypeError, ValueError):
            continue
        if page_number <= 0:
            continue
        size = page.get("size") if isinstance(page.get("size"), Mapping) else page
        width = _number(size.get("width") if isinstance(size, Mapping) else None)
        height = _number(size.get("height") if isinstance(size, Mapping) else None)
        if width <= 0 or height <= 0:
            width = height = 1.0
            warnings.append(
                f"Page {page_number}: Docling page size was missing; geometry uses a unit page."
            )
        dimensions[page_number] = (width, height)
    return dimensions, warnings


def _bbox(
    provenance: Mapping[str, Any],
    page_size: tuple[float, float],
) -> tuple[list[float], list[float], list[str], bool]:
    warnings: list[str] = []
    raw_bbox = provenance.get("bbox")
    if not isinstance(raw_bbox, Mapping):
        return [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0], ["Missing Docling bbox."], False
    width, height = page_size
    if {"l", "t", "r", "b"}.issubset(raw_bbox):
        left = _number(raw_bbox.get("l"))
        right = _number(raw_bbox.get("r"))
        top = _number(raw_bbox.get("t"))
        bottom = _number(raw_bbox.get("b"))
        default_origin = "bottomleft"
    elif {"x0", "y0", "x1", "y1"}.issubset(raw_bbox):
        left = _number(raw_bbox.get("x0"))
        right = _number(raw_bbox.get("x1"))
        top = _number(raw_bbox.get("y0"))
        bottom = _number(raw_bbox.get("y1"))
        default_origin = "topleft"
    else:
        return [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0], ["Invalid Docling bbox."], False
    origin_value = raw_bbox.get("coord_origin", provenance.get("coord_origin"))
    origin = str(origin_value).rsplit(".", 1)[-1].replace("_", "").lower() if origin_value else default_origin
    x0, x1 = sorted((left, right))
    if origin in {"bottomleft", "bottom-left"}:
        y0, y1 = sorted((height - top, height - bottom))
    else:
        y0, y1 = sorted((top, bottom))
        if origin not in {"topleft", "top-left"}:
            warnings.append(f"Unknown Docling coordinate origin {origin_value!r}; assumed top-left.")
    unclamped = (x0, y0, x1, y1)
    x0 = min(max(x0, 0.0), width)
    x1 = min(max(x1, 0.0), width)
    y0 = min(max(y0, 0.0), height)
    y1 = min(max(y1, 0.0), height)
    if (x0, y0, x1, y1) != unclamped:
        warnings.append("Docling bbox extended beyond the page and was clamped.")
    pdf = [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]
    normalized = [
        round(x0 / width, 5),
        round(y0 / height, 5),
        round(x1 / width, 5),
        round(y1 / height, 5),
    ]
    valid = x1 > x0 and y1 > y0
    if not valid:
        warnings.append("Docling bbox is degenerate.")
    return pdf, normalized, warnings, valid


def _char_span(provenance: Mapping[str, Any], text_length: int) -> tuple[int, int] | None:
    raw = provenance.get("charspan", provenance.get("char_span"))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or len(raw) != 2:
        return None
    try:
        start, end = int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        return None
    if 0 <= start < end <= text_length:
        return start, end
    return None


def _citations(text: str) -> list[str]:
    return list(
        dict.fromkeys(_NUMERIC_CITATION.findall(text) + _AUTHOR_YEAR_CITATION.findall(text))
    )


def _object_references(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in _OBJECT_REFERENCE.finditer(text)))


def _reference_label(text: str) -> str | None:
    match = _REFERENCE_LABEL.match(text)
    return next((value for value in match.groups() if value), None) if match else None


def _heading_details(text: str, item: Mapping[str, Any]) -> tuple[str | None, int]:
    number: str | None = None
    match = _NUMBERED_HEADING.match(text) or _APPENDIX_HEADING.match(text)
    if match:
        number = match.group(1)
    try:
        level = int(item.get("level", 0))
    except (TypeError, ValueError):
        level = 0
    if number:
        level = min(6, number.count(".") + 1)
    elif level <= 0:
        level = 1
    return number, min(6, max(1, level))


def _promote_missing_title(records: list[dict[str, Any]]) -> None:
    """Conservatively recover a title mislabeled as the first section header."""

    if any(
        record["role"] == "title" and record["pageNumber"] == 1
        for record in records
    ):
        return
    first_heading = next(
        (
            record
            for record in records
            if record["pageNumber"] == 1
            and record["role"] == "heading"
            and not record["furniture"]
        ),
        None,
    )
    if first_heading is None or not first_heading["bboxValid"]:
        return
    text = re.sub(r"\s+", " ", first_heading["text"]).strip()
    normalized = text.rstrip(":").lower()
    bbox = first_heading["bboxNormalized"]
    horizontally_centered = abs((float(bbox[0]) + float(bbox[2])) / 2 - 0.5) <= 0.12
    special_headings = {
        "abstract",
        "acknowledgment",
        "acknowledgments",
        "acknowledgement",
        "acknowledgements",
        "references",
        "bibliography",
    }
    if (
        not 3 <= len(text) <= 240
        or float(bbox[1]) >= 0.2
        or not horizontally_centered
        or normalized in special_headings
        or _NUMBERED_HEADING.match(text)
        or _APPENDIX_HEADING.match(text)
    ):
        return
    start = records.index(first_heading)
    confirmed_by_front_matter_boundary = False
    for record in records[start + 1 :]:
        if record["pageNumber"] != 1 or record["furniture"]:
            continue
        if (
            record["bboxValid"]
            and float(record["bboxNormalized"][1]) <= float(bbox[1])
        ):
            continue
        if record["role"] == "abstract":
            confirmed_by_front_matter_boundary = True
            break
        if record["role"] != "heading":
            continue
        number, _level = _heading_details(record["text"], record["item"])
        heading_title = _normalized_section_title(record["text"], number)
        if heading_title in {"abstract", "introduction"}:
            confirmed_by_front_matter_boundary = True
            break
    if not confirmed_by_front_matter_boundary:
        return
    first_heading["role"] = "title"
    first_heading["paragraphId"] = "front-title"
    first_heading["continuesFrom"] = None
    first_heading["warnings"].append(
        "Docling labeled the page-one title as a section header; it was conservatively promoted."
    )


def _preserve_unclassified_front_matter(records: list[dict[str, Any]]) -> None:
    """Keep unclassified page-one text available to a synthetic preamble."""

    first_section_index = next(
        (index for index, record in enumerate(records) if record["role"] == "heading"),
        None,
    )
    if first_section_index is None:
        return
    for record in records[:first_section_index]:
        if (
            record["pageNumber"] == 1
            and not record["furniture"]
            and record["role"] in {"paragraph", "footnote", "list_item"}
        ):
            record["warnings"].append(
                "Unclassified page-one text before the first section was preserved in a translatable preamble."
            )


def _normalized_section_title(text: str, number: str | None) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if number:
        appendix = r"(?:Appendix\s+)?" if re.match(r"^[A-Z]", number) else ""
        value = re.sub(
            rf"^\s*{appendix}{re.escape(number)}[.)]?\s*",
            "",
            value,
            count=1,
            flags=re.IGNORECASE,
        )
    return value.rstrip(":").lower()


def _link_cross_item_page_continuations(records: list[dict[str, Any]]) -> None:
    """Join only strong cross-page continuations split into separate TextItems."""

    eligible_roles = {"abstract", "paragraph"}
    first_body_on_page: dict[int, dict[str, Any]] = {}
    for record in records:
        if not record["furniture"] and record["role"] in eligible_roles:
            first_body_on_page.setdefault(record["pageNumber"], record)
    previous: dict[str, Any] | None = None
    for record in records:
        if record["role"] == "heading":
            previous = None
            continue
        if record["furniture"] or record["role"] not in eligible_roles:
            continue
        if previous is not None:
            previous_text = previous["text"].rstrip()
            current_text = record["text"].lstrip(" \t\n\"'“‘(")
            grammatical_continuation = (
                bool(current_text[:1].islower())
                or not bool(re.search(r"[.!?;:)\]}”’]\s*$", previous_text))
            )
            if (
                record["ref"] != previous["ref"]
                and record["pageNumber"] == previous["pageNumber"] + 1
                and first_body_on_page.get(record["pageNumber"]) is record
                and record.get("sectionId") == previous.get("sectionId")
                and record["role"] == previous["role"]
                and grammatical_continuation
            ):
                record["paragraphId"] = previous["paragraphId"]
                record["continuesFrom"] = previous["blockId"]
                record["warnings"].append(
                    "Separate Docling text items were joined at a strong page-continuation boundary."
                )
        previous = record


def _visual_kind(collection: str, item: Mapping[str, Any]) -> str | None:
    label = _label(item)
    if label in _VISUAL_LABELS:
        return _VISUAL_LABELS[label]
    if collection == "pictures":
        return "figure"
    if collection == "tables":
        return "table"
    return None


def _visual_label(caption_texts: list[str], kind: str) -> str | None:
    for text in caption_texts:
        match = _OBJECT_LABEL.match(text)
        if match:
            prefix = match.group(1).rstrip(".")
            if prefix.lower() == "fig":
                prefix = "Figure"
            elif prefix.lower() == "eq":
                prefix = "Equation"
            else:
                prefix = prefix.title()
            return f"{prefix} {match.group(2)}"
    return None if kind in {"figure", "table"} else kind.title()


def _object_label_kind(text: str) -> str | None:
    match = _OBJECT_LABEL.match(text)
    if match is None:
        return None
    prefix = match.group(1).lower().rstrip(".")
    if prefix in {"figure", "fig"}:
        return "figure"
    if prefix == "table":
        return "table"
    if prefix == "algorithm":
        return "algorithm"
    return "equation"


def _source_name(document: Mapping[str, Any], source_file: str | Path | None) -> str:
    if source_file is not None:
        return Path(source_file).name
    origin = document.get("origin")
    if isinstance(origin, Mapping) and isinstance(origin.get("filename"), str):
        return Path(origin["filename"]).name
    name = str(document.get("name") or "document")
    return name if Path(name).suffix else f"{name}.pdf"


def docling_document_to_ir(
    document: Any,
    source_file: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map Docling's native document graph directly to PaperTrans IR.

    The body/group reference graph supplies reading order. Provenance supplies
    page geometry, and Docling labels supply semantic roles. No Markdown export,
    OCR pass, or LLM structure analysis is involved.
    """

    raw = docling_document_to_dict(document)
    page_sizes, document_warnings = _page_dimensions(raw)
    ref_index: dict[str, tuple[str, Mapping[str, Any]]] = {}
    ordered_content_refs: list[str] = []

    def index_item(collection: str, key: str, item: Mapping[str, Any]) -> str:
        synthetic_ref = f"#/{collection}/{key}"
        self_ref = _ref_value(item.get("self_ref")) or synthetic_ref
        ref_index[self_ref] = (collection, item)
        ref_index.setdefault(synthetic_ref, (collection, item))
        return self_ref

    for collection in ("groups", *_CONTENT_COLLECTIONS):
        for key, item in _collection_values(raw.get(collection, [])):
            self_ref = index_item(collection, key, item)
            if collection != "groups":
                ordered_content_refs.append(self_ref)
    for root_name in ("body", "furniture"):
        root = raw.get(root_name)
        if isinstance(root, Mapping):
            index_item(root_name, "root", root)

    for _collection, item in ref_index.values():
        for provenance in _provenance(item):
            page_number = _page_number(provenance)
            if page_number is not None and page_number not in page_sizes:
                page_sizes[page_number] = (1.0, 1.0)
                document_warnings.append(
                    f"Page {page_number}: provenance had no page metadata; geometry uses a unit page."
                )
    if not page_sizes:
        page_sizes[1] = (1.0, 1.0)
        document_warnings.append("Docling supplied no page metadata; created a unit page 1.")
    max_page = max(page_sizes)
    for page_number in range(1, max_page + 1):
        if page_number not in page_sizes:
            page_sizes[page_number] = (1.0, 1.0)
            document_warnings.append(
                f"Page {page_number}: page metadata was missing; geometry uses a unit page."
            )

    visual_descendant_owners: dict[str, set[str]] = {}
    visual_caption_refs: set[str] = set()
    visual_footnote_refs: set[str] = set()
    visual_footnote_owners: dict[str, set[str]] = {}
    visual_reference_refs: set[str] = set()
    visual_regions_by_page: dict[int, list[tuple[str, list[float]]]] = {}

    def collect_visual_descendants(owner_ref: str, value: Any, seen: set[str]) -> None:
        ref = _ref_value(value)
        if ref is None or ref in seen or ref == owner_ref:
            return
        seen.add(ref)
        indexed = ref_index.get(ref)
        if indexed is None:
            return
        visual_descendant_owners.setdefault(ref, set()).add(owner_ref)
        _collection, item = indexed
        for child in _as_sequence(item.get("children")):
            collect_visual_descendants(owner_ref, child, seen)

    for owner_ref in dict.fromkeys(ordered_content_refs):
        collection, item = ref_index[owner_ref]
        if _visual_kind(collection, item) is None:
            continue
        if (provenance := next(iter(_provenance(item)), None)) is not None:
            page_number = _page_number(provenance)
            if page_number in page_sizes:
                _pdf, normalized, _warnings, valid = _bbox(
                    provenance, page_sizes[page_number]
                )
                if valid:
                    visual_regions_by_page.setdefault(page_number, []).append(
                        (owner_ref, normalized)
                    )
        for field, destination in (
            ("captions", visual_caption_refs),
            ("footnotes", visual_footnote_refs),
            ("references", visual_reference_refs),
        ):
            for value in _as_sequence(item.get(field)):
                if (ref := _ref_value(value)) is not None:
                    destination.add(ref)
                    if field == "footnotes":
                        visual_footnote_owners.setdefault(ref, set()).add(owner_ref)
        for child in _as_sequence(item.get("children")):
            collect_visual_descendants(owner_ref, child, set())

    independently_reachable_body_refs: set[str] = set()

    def collect_non_visual_body_refs(value: Any, seen: set[str]) -> None:
        ref = _ref_value(value)
        if ref is None or ref in seen:
            return
        seen.add(ref)
        indexed = ref_index.get(ref)
        if indexed is None:
            return
        collection, item = indexed
        if _visual_kind(collection, item) is not None:
            return
        if collection not in {"body", "furniture", "groups"}:
            independently_reachable_body_refs.add(ref)
        for child in _as_sequence(item.get("children")):
            collect_non_visual_body_refs(child, seen)

    body_root = raw.get("body")
    if isinstance(body_root, Mapping):
        for child in _as_sequence(body_root.get("children")):
            collect_non_visual_body_refs(child, set())

    preserved_visual_refs = visual_footnote_refs | visual_reference_refs
    suppressed_visual_refs = (
        set(visual_descendant_owners)
        - preserved_visual_refs
        - independently_reachable_body_refs
    )
    traversal: list[tuple[int, str, str, Mapping[str, Any], bool]] = []
    visited: set[str] = set()
    traversal_index = 0

    def walk(value: Any, furniture: bool = False) -> None:
        nonlocal traversal_index
        ref = _ref_value(value)
        if ref is None or ref in visited:
            return
        indexed = ref_index.get(ref)
        if indexed is None:
            document_warnings.append(f"Unresolved Docling reference: {ref}")
            return
        collection, item = indexed
        visited.add(ref)
        is_furniture = furniture or collection == "furniture" or _content_layer(item) == "furniture"
        if collection not in {"body", "furniture", "groups"}:
            traversal_index += 1
            traversal.append((traversal_index, ref, collection, item, is_furniture))
        for child in _as_sequence(item.get("children")):
            walk(child, is_furniture)

    body = raw.get("body")
    if isinstance(body, Mapping):
        for child in _as_sequence(body.get("children")):
            walk(child)
    furniture = raw.get("furniture")
    if isinstance(furniture, Mapping):
        for child in _as_sequence(furniture.get("children")):
            walk(child, True)

    def fallback_order(ref: str) -> tuple[int, float, float, str]:
        _collection, item = ref_index[ref]
        provenance = next(iter(_provenance(item)), {})
        page_number = _page_number(provenance) or max_page + 1
        _pdf, normalized, _warnings, _valid = _bbox(
            provenance, page_sizes.get(page_number, (1.0, 1.0))
        )
        return page_number, normalized[1], normalized[0], ref

    for ref in sorted((value for value in ordered_content_refs if value not in visited), key=fallback_order):
        collection, item = ref_index[ref]
        traversal_index += 1
        traversal.append(
            (
                traversal_index,
                ref,
                collection,
                item,
                _content_layer(item) == "furniture",
            )
        )
        document_warnings.append(
            f"Docling item {ref} was outside the body graph and was appended by provenance order."
        )

    records: list[dict[str, Any]] = []
    item_order: dict[str, int] = {}
    ref_to_block_ids: dict[str, list[str]] = {}

    def overlapping_visual_owners(
        page_number: int, bbox: list[float]
    ) -> set[str]:
        width = max(0.0, bbox[2] - bbox[0])
        height = max(0.0, bbox[3] - bbox[1])
        area = width * height
        if area <= 0:
            return set()
        center_x = (bbox[0] + bbox[2]) / 2
        center_y = (bbox[1] + bbox[3]) / 2
        owners: set[str] = set()
        for owner_ref, visual_bbox in visual_regions_by_page.get(page_number, []):
            intersection_width = max(
                0.0, min(bbox[2], visual_bbox[2]) - max(bbox[0], visual_bbox[0])
            )
            intersection_height = max(
                0.0, min(bbox[3], visual_bbox[3]) - max(bbox[1], visual_bbox[1])
            )
            overlap_ratio = intersection_width * intersection_height / area
            center_inside = (
                visual_bbox[0] <= center_x <= visual_bbox[2]
                and visual_bbox[1] <= center_y <= visual_bbox[3]
            )
            if center_inside or overlap_ratio >= 0.4:
                owners.add(owner_ref)
        return owners

    for order, ref, collection, item, furniture_item in traversal:
        item_order[ref] = order
        raw_text = _raw_text(item)
        text = raw_text.strip()
        if not text:
            continue
        provenance_values = _provenance(item)
        segment_values: list[tuple[str, Mapping[str, Any] | None, list[str]]] = []
        if len(provenance_values) <= 1:
            segment_values = [(text, provenance_values[0] if provenance_values else None, [])]
        else:
            spans = [_char_span(value, len(raw_text)) for value in provenance_values]
            spans_are_complete = all(span is not None for span in spans)
            previous_end = 0
            if spans_are_complete:
                for span in spans:
                    assert span is not None
                    start, end = span
                    if start < previous_end or raw_text[previous_end:start].strip():
                        spans_are_complete = False
                        break
                    previous_end = end
                if raw_text[previous_end:].strip():
                    spans_are_complete = False
            if spans_are_complete:
                for provenance, span in zip(provenance_values, spans, strict=True):
                    assert span is not None
                    segment = raw_text[span[0] : span[1]].strip()
                    if segment:
                        segment_values.append((segment, provenance, []))
            if not segment_values:
                segment_values = [
                    (
                        text,
                        provenance_values[0],
                        [
                            "Multiple Docling provenance entries lacked complete, ordered character spans; "
                            "the item was kept as one exact-text block."
                        ],
                    )
                ]
        base_id = _safe_id(ref)
        ref_to_block_ids[ref] = []
        if ref in visual_footnote_refs:
            base_role = "footnote"
        else:
            base_role = _ROLE_BY_LABEL.get(_label(item), "paragraph")
        if furniture_item and base_role not in _FURNITURE_ROLES:
            base_role = "header"
        previous_block_id: str | None = None
        for segment_index, (segment, provenance, segment_warnings) in enumerate(segment_values, start=1):
            block_id = base_id if segment_index == 1 else f"{base_id}-s{segment_index}"
            page_number = _page_number(provenance or {}) or 1
            if page_number not in page_sizes:
                page_number = 1
            if provenance is None:
                pdf_bbox = normalized_bbox = [0.0, 0.0, 0.0, 0.0]
                bbox_warnings = ["Docling item has no provenance; assigned to page 1."]
                valid_bbox = False
            else:
                pdf_bbox, normalized_bbox, bbox_warnings, valid_bbox = _bbox(
                    provenance, page_sizes[page_number]
                )
            embedded_visual_owners = (
                set(visual_descendant_owners.get(ref, set()))
                if ref in suppressed_visual_refs
                else set()
            )
            if (
                not embedded_visual_owners
                and collection == "texts"
                and _label(item) in {"text", "paragraph"}
                and 0 < len(segment) <= 160
                and valid_bbox
            ):
                embedded_visual_owners.update(
                    overlapping_visual_owners(page_number, normalized_bbox)
                )
            suppressed_visual_text = bool(embedded_visual_owners)
            role = "noise" if suppressed_visual_text else base_role
            paragraph_id = f"para-{base_id}" if role in _BODY_ROLES else None
            continues_from = previous_block_id if paragraph_id is not None else None
            warnings = [*segment_warnings, *bbox_warnings]
            records.append(
                {
                    "order": order,
                    "segmentIndex": segment_index,
                    "ref": ref,
                    "collection": collection,
                    "item": item,
                    "blockId": block_id,
                    "sourceBlockId": base_id,
                    "text": segment,
                    "pageNumber": page_number,
                    "bboxPdf": pdf_bbox,
                    "bboxNormalized": normalized_bbox,
                    "bboxValid": valid_bbox,
                    "role": role,
                    "paragraphId": paragraph_id,
                    "continuesFrom": continues_from,
                    "furniture": furniture_item,
                    "embeddedVisualOwnerRefs": sorted(embedded_visual_owners),
                    "suppressedVisualText": suppressed_visual_text,
                    "visualCaptionCandidate": False,
                    "associatedVisualCaption": False,
                    "warnings": warnings,
                }
            )
            ref_to_block_ids[ref].append(block_id)
            previous_block_id = block_id if paragraph_id is not None else None

    records.sort(key=lambda value: (value["order"], value["segmentIndex"]))
    _promote_missing_title(records)
    _preserve_unclassified_front_matter(records)
    sections: list[dict[str, Any]] = []
    section_stack: list[tuple[int, str]] = []
    current_section: str | None = None
    current_section_title = ""
    used_section_ids: set[str] = set()
    for record in records:
        role = record["role"]
        text = record["text"]
        item = record["item"]
        if role == "heading":
            number, level = _heading_details(text, item)
            base_section_id = _safe_id(record["ref"], prefix="sec")
            section_id = base_section_id
            suffix = 2
            while section_id in used_section_ids:
                section_id = f"{base_section_id}-{suffix}"
                suffix += 1
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            parent_section_id = section_stack[-1][1] if section_stack else None
            sections.append(
                {
                    "sectionId": section_id,
                    "number": number,
                    "titleBlockId": record["blockId"],
                    "level": level,
                    "parentSectionId": parent_section_id,
                    "pageStart": record["pageNumber"],
                }
            )
            used_section_ids.add(section_id)
            section_stack.append((level, section_id))
            current_section = section_id
            current_section_title = _normalized_section_title(text, number)
            record["sectionId"] = section_id
            record["paragraphId"] = None
        else:
            record["sectionId"] = current_section
            if role == "paragraph" and current_section_title in {"references", "bibliography"}:
                record["role"] = "reference"
                record["paragraphId"] = f"reference-{record['sourceBlockId']}"
            elif role == "paragraph" and current_section_title == "abstract":
                record["role"] = "abstract"
            if record["role"] == "reference":
                record["paragraphId"] = f"reference-{record['sourceBlockId']}"

    block_by_id = {record["blockId"]: record for record in records}
    page_records: dict[int, list[dict[str, Any]]] = {page: [] for page in page_sizes}
    for record in records:
        page_records[record["pageNumber"]].append(record)
    for values in page_records.values():
        values.sort(key=lambda value: (value["order"], value["segmentIndex"]))

    visuals_by_page: dict[int, list[dict[str, Any]]] = {page: [] for page in page_sizes}
    visual_entries: list[dict[str, Any]] = []
    explicit_caption_owners: dict[str, set[str]] = {}
    for order, ref, collection, item, _furniture_item in traversal:
        kind = _visual_kind(collection, item)
        if kind is None:
            continue
        provenance = next(iter(_provenance(item)), None)
        page_number = _page_number(provenance or {}) or 1
        if provenance is None or page_number not in page_sizes:
            document_warnings.append(f"Skipped {kind} {ref}: missing page provenance.")
            continue
        _pdf_bbox, normalized_bbox, bbox_warnings, valid_bbox = _bbox(
            provenance, page_sizes[page_number]
        )
        area = max(0.0, normalized_bbox[2] - normalized_bbox[0]) * max(
            0.0, normalized_bbox[3] - normalized_bbox[1]
        )
        if not valid_bbox or area < 0.0005:
            document_warnings.append(f"Skipped {kind} {ref}: invalid or implausibly small crop.")
            continue
        explicit_caption_refs = [
            caption_ref
            for value in _as_sequence(item.get("captions"))
            if (caption_ref := _ref_value(value)) is not None
        ]
        for caption_ref in explicit_caption_refs:
            explicit_caption_owners.setdefault(caption_ref, set()).add(ref)
        visual_entries.append(
            {
                "ref": ref,
                "order": order,
                "pageNumber": page_number,
                "kind": kind,
                "bboxNormalized": normalized_bbox,
                "explicitCaptionRefs": explicit_caption_refs,
                "objectId": _safe_id(ref, prefix=kind),
                "confidence": 0.99,
                "warnings": bbox_warnings,
            }
        )

    visual_entry_by_ref = {entry["ref"]: entry for entry in visual_entries}
    for record_index, record in enumerate(records):
        if record["role"] != "footnote" or record["ref"] not in visual_footnote_owners:
            continue
        owner_entries = [
            visual_entry_by_ref[owner_ref]
            for owner_ref in visual_footnote_owners[record["ref"]]
            if owner_ref in visual_entry_by_ref
            and visual_entry_by_ref[owner_ref]["pageNumber"] == record["pageNumber"]
        ]
        if not owner_entries:
            continue
        record_bbox = record["bboxNormalized"]
        minimum_gap = min(
            max(
                max(
                    0.0,
                    max(record_bbox[0], entry["bboxNormalized"][0])
                    - min(record_bbox[2], entry["bboxNormalized"][2]),
                ),
                max(
                    0.0,
                    max(record_bbox[1], entry["bboxNormalized"][1])
                    - min(record_bbox[3], entry["bboxNormalized"][3]),
                ),
            )
            for entry in owner_entries
        )
        previous_body = next(
            (
                candidate
                for candidate in reversed(records[:record_index])
                if not candidate["furniture"]
                and not candidate["suppressedVisualText"]
                and candidate["role"] in {"abstract", "paragraph"}
            ),
            None,
        )
        if (
            minimum_gap > 0.12
            and previous_body is not None
            and record["pageNumber"] == previous_body["pageNumber"] + 1
            and not re.search(r"[.!?;:)[\]}”’]\s*$", previous_body["text"].rstrip())
        ):
            record["role"] = "paragraph"
            record["paragraphId"] = f"para-{record['sourceBlockId']}"
            record["continuesFrom"] = None

    def is_caption_record(record: dict[str, Any]) -> bool:
        match = _OBJECT_LABEL.match(record["text"])
        if match is None:
            return False
        if (
            _label(record["item"]) == "caption"
            or record["ref"] in explicit_caption_owners
        ):
            return True
        remainder = record["text"][match.end() :].lstrip()
        return not remainder or remainder[0] in ":.-–—"

    caption_records = [record for record in records if is_caption_record(record)]
    caption_edges: list[tuple[float, int, int, dict[str, Any], dict[str, Any]]] = []
    for record in caption_records:
        caption_kind = _object_label_kind(record["text"])
        assert caption_kind is not None
        for visual_entry in visual_entries:
            if (
                visual_entry["pageNumber"] != record["pageNumber"]
                or visual_entry["kind"] != caption_kind
            ):
                continue
            score = visual_caption_score(
                caption_kind,
                tuple(float(value) for value in record["bboxNormalized"]),
                tuple(float(value) for value in visual_entry["bboxNormalized"]),
            )
            if score < 0:
                continue
            record["visualCaptionCandidate"] = True
            explicit_match = int(
                visual_entry["ref"] in explicit_caption_owners.get(record["ref"], set())
            )
            caption_edges.append(
                (
                    score,
                    explicit_match,
                    -abs(int(record["order"]) - int(visual_entry["order"])),
                    record,
                    visual_entry,
                )
            )

    assigned_caption_records: dict[str, list[dict[str, Any]]] = {
        visual_entry["ref"]: [] for visual_entry in visual_entries
    }
    claimed_caption_blocks: set[str] = set()
    claimed_caption_visuals: set[str] = set()
    associated_orphan_captions: list[dict[str, str]] = []
    associated_pairs: set[tuple[str, str]] = set()

    def assign_caption(
        record: dict[str, Any],
        visual_entry: dict[str, Any],
        *,
        primary: bool,
    ) -> None:
        block_id = record["blockId"]
        visual_ref = visual_entry["ref"]
        if block_id in claimed_caption_blocks:
            return
        claimed_caption_blocks.add(block_id)
        assigned_caption_records[visual_ref].append(record)
        record["role"] = "caption"
        record["paragraphId"] = None
        record["continuesFrom"] = None
        record["suppressedVisualText"] = False
        record["associatedVisualCaption"] = True
        if primary:
            claimed_caption_visuals.add(visual_ref)
            if visual_ref not in explicit_caption_owners.get(record["ref"], set()):
                pair = (record["ref"], visual_ref)
                if pair not in associated_pairs:
                    associated_pairs.add(pair)
                    associated_orphan_captions.append(
                        {"captionRef": record["ref"], "visualRef": visual_ref}
                    )

    caption_edges.sort(key=lambda value: value[:3], reverse=True)
    for _score, _explicit, _order_distance, record, visual_entry in caption_edges:
        if (
            record["blockId"] in claimed_caption_blocks
            or visual_entry["ref"] in claimed_caption_visuals
        ):
            continue
        assign_caption(record, visual_entry, primary=True)

    for record in caption_records:
        if record["blockId"] in claimed_caption_blocks:
            continue
        caption_kind = _object_label_kind(record["text"])
        explicit_entries = [
            visual_entry
            for visual_entry in visual_entries
            if visual_entry["ref"] in explicit_caption_owners.get(record["ref"], set())
            and visual_entry["pageNumber"] == record["pageNumber"]
            and visual_entry["kind"] == caption_kind
            and visual_entry["ref"] not in claimed_caption_visuals
        ]
        if explicit_entries:
            record["visualCaptionCandidate"] = True
            explicit_entry = min(
                explicit_entries,
                key=lambda value: abs(int(record["order"]) - int(value["order"])),
            )
            assign_caption(record, explicit_entry, primary=True)

    for visual_entry in visual_entries:
        visual_ref = visual_entry["ref"]
        if visual_ref in claimed_caption_visuals:
            continue
        fallback_records = [
            record
            for caption_ref in visual_entry["explicitCaptionRefs"]
            for block_id in ref_to_block_ids.get(caption_ref, [])
            if (record := block_by_id[block_id])["pageNumber"]
            == visual_entry["pageNumber"]
            and record["blockId"] not in claimed_caption_blocks
            and _object_label_kind(record["text"]) is None
            and _label(record["item"]) == "caption"
        ]
        ranked_fallbacks = sorted(
            (
                (
                    visual_caption_score(
                        visual_entry["kind"],
                        tuple(float(value) for value in record["bboxNormalized"]),
                        tuple(
                            float(value) for value in visual_entry["bboxNormalized"]
                        ),
                    ),
                    record,
                )
                for record in fallback_records
            ),
            key=lambda value: value[0],
            reverse=True,
        )
        if ranked_fallbacks and ranked_fallbacks[0][0] >= 0:
            ranked_fallbacks[0][1]["visualCaptionCandidate"] = True
            assign_caption(ranked_fallbacks[0][1], visual_entry, primary=True)

    def shares_caption_line(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_bbox = left["bboxNormalized"]
        right_bbox = right["bboxNormalized"]
        overlap = max(
            0.0, min(left_bbox[3], right_bbox[3]) - max(left_bbox[1], right_bbox[1])
        )
        minimum_height = max(
            1e-9,
            min(left_bbox[3] - left_bbox[1], right_bbox[3] - right_bbox[1]),
        )
        horizontal_gap = max(
            0.0,
            max(left_bbox[0], right_bbox[0]) - min(left_bbox[2], right_bbox[2]),
        )
        center_distance = abs(
            (left_bbox[1] + left_bbox[3]) / 2
            - (right_bbox[1] + right_bbox[3]) / 2
        )
        return (
            overlap / minimum_height >= 0.5
            and center_distance <= 0.008
            and horizontal_gap <= 0.02
        )

    for visual_entry in visual_entries:
        visual_ref = visual_entry["ref"]
        line_records = list(assigned_caption_records[visual_ref])
        if not line_records:
            continue
        changed = True
        while changed:
            changed = False
            for record in page_records[visual_entry["pageNumber"]]:
                if (
                    record["blockId"] in claimed_caption_blocks
                    or record["furniture"]
                    or record["collection"] != "texts"
                    or _object_label_kind(record["text"]) is not None
                    or abs(int(record["order"]) - int(line_records[0]["order"])) > 12
                    or not any(shares_caption_line(record, value) for value in line_records)
                ):
                    continue
                record["visualCaptionCandidate"] = True
                assign_caption(record, visual_entry, primary=False)
                line_records.append(record)
                changed = True

    _link_cross_item_page_continuations(records)

    for visual_entry in visual_entries:
        page_number = visual_entry["pageNumber"]
        visual_ref = visual_entry["ref"]
        visual_bbox = list(visual_entry["bboxNormalized"])
        for record in page_records[page_number]:
            if (
                not record["suppressedVisualText"]
                or visual_ref not in record["embeddedVisualOwnerRefs"]
                or not record["bboxValid"]
            ):
                continue
            record_bbox = record["bboxNormalized"]
            horizontal_gap = max(
                0.0,
                max(visual_bbox[0], record_bbox[0])
                - min(visual_bbox[2], record_bbox[2]),
            )
            vertical_gap = max(
                0.0,
                max(visual_bbox[1], record_bbox[1])
                - min(visual_bbox[3], record_bbox[3]),
            )
            if horizontal_gap <= 0.025 and vertical_gap <= 0.025:
                visual_bbox = [
                    min(visual_bbox[0], record_bbox[0]),
                    min(visual_bbox[1], record_bbox[1]),
                    max(visual_bbox[2], record_bbox[2]),
                    max(visual_bbox[3], record_bbox[3]),
                ]

        same_page_candidates = [
            record
            for record in records
            if record["pageNumber"] == page_number
            and record["order"] <= visual_entry["order"]
            and not record["furniture"]
            and not record["suppressedVisualText"]
            and record["role"] not in {"caption", "equation", "algorithm"}
        ]
        preceding_candidates = [
            record
            for record in records
            if record["order"] <= visual_entry["order"]
            and not record["furniture"]
            and not record["suppressedVisualText"]
            and record["role"] not in {"caption", "equation", "algorithm"}
        ]
        fallback_candidates = [
            record
            for record in page_records[page_number]
            if not record["furniture"]
            and not record["suppressedVisualText"]
            and record["role"] not in {"caption", "equation", "algorithm"}
        ]
        anchor_record = next(
            (
                values[-1]
                for values in (
                    same_page_candidates,
                    preceding_candidates,
                    fallback_candidates,
                )
                if values
            ),
            None,
        )
        caption_values = sorted(
            assigned_caption_records[visual_ref],
            key=lambda value: (value["order"], value["segmentIndex"]),
        )
        visuals_by_page[page_number].append(
            {
                "objectId": visual_entry["objectId"],
                "kind": visual_entry["kind"],
                "label": _visual_label(
                    [record["text"] for record in caption_values],
                    visual_entry["kind"],
                ),
                "bboxNormalized": visual_bbox,
                "captionBlockIds": [record["blockId"] for record in caption_values],
                "insertAfterBlockId": anchor_record["blockId"]
                if anchor_record
                else None,
                "confidence": visual_entry["confidence"],
                "warnings": visual_entry["warnings"],
            }
        )

    evidence_pages: list[dict[str, Any]] = []
    structure_pages: list[dict[str, Any]] = []
    for page_number in sorted(page_sizes):
        width, height = page_sizes[page_number]
        values = page_records[page_number]
        blocks = [
            {
                "blockId": record["blockId"],
                "sourceBlockId": record["sourceBlockId"],
                "text": record["text"],
                "bboxPdf": record["bboxPdf"],
                "bboxNormalized": record["bboxNormalized"],
                "lineCount": max(1, record["text"].count("\n") + 1),
                "fontSizeMin": 0.0,
                "fontSizeMax": 0.0,
                "fonts": [],
                "bold": False,
                "italic": False,
                "mathCharacterRatio": 1.0 if record["role"] == "equation" else 0.0,
                "embeddedVisualOwnerRefs": record["embeddedVisualOwnerRefs"],
                "suppressedVisualText": record["suppressedVisualText"],
                "visualCaptionCandidate": record["visualCaptionCandidate"],
                "associatedVisualCaption": record["associatedVisualCaption"],
            }
            for record in values
        ]
        assignments = []
        for reading_order, record in enumerate(values, start=1):
            role = record["role"]
            hidden = bool(
                record["furniture"]
                or record["suppressedVisualText"]
                or role in _FURNITURE_ROLES
            )
            assignments.append(
                {
                    "blockId": record["blockId"],
                    "role": role,
                    "readingOrder": reading_order,
                    "sectionId": None if hidden else record.get("sectionId"),
                    "paragraphId": record["paragraphId"],
                    "continuesFrom": record["continuesFrom"],
                    "hidden": hidden,
                    "citations": _citations(record["text"]),
                    "objectReferences": _object_references(record["text"]),
                    "referenceLabel": _reference_label(record["text"])
                    if role == "reference"
                    else None,
                    "suppressedVisualText": record["suppressedVisualText"],
                    "visualCaptionCandidate": record["visualCaptionCandidate"],
                    "associatedVisualCaption": record["associatedVisualCaption"],
                    "confidence": 0.99 if record["bboxValid"] else 0.7,
                    "warnings": record["warnings"],
                }
            )
        evidence_pages.append(
            {
                "pageNumber": page_number,
                "widthPdf": round(width, 2),
                "heightPdf": round(height, 2),
                "image": None,
                "imageWidth": 0,
                "imageHeight": 0,
                "drawingClusters": [],
                "imageRegions": [],
                "blocks": blocks,
            }
        )
        structure_pages.append(
            {
                "pageNumber": page_number,
                "blockAssignments": assignments,
                "visualObjects": visuals_by_page[page_number],
            }
        )

    source_name = _source_name(raw, source_file)
    evidence = {
        "version": 4,
        "sourceFile": source_name,
        "pageCount": len(evidence_pages),
        "extractionEngine": "docling",
        "pages": evidence_pages,
    }
    structure = {
        "version": 2,
        "sourceFile": source_name,
        "model": {"name": "docling", "reasoningEffort": "none"},
        "pages": structure_pages,
        "sections": sections,
        "doclingDiagnostics": {
            "embeddedVisualTextItems": len(
                {
                    record["ref"]
                    for record in records
                    if record["suppressedVisualText"]
                }
            ),
            "suppressedEmbeddedVisualTextBlocks": sum(
                bool(record["suppressedVisualText"]) for record in records
            ),
            "associatedOrphanCaptions": associated_orphan_captions,
        },
        "warnings": list(dict.fromkeys(document_warnings)),
    }
    validate_structure_batch(evidence_pages, structure)
    return evidence, structure


def extract_docling_semantics(
    source: Path,
    work_dir: Path,
    evidence_path: Path,
    structure_path: Path,
    visuals_path: Path,
    *,
    worker_timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Convert a PDF and persist the three inputs used by semantic_document."""

    source = Path(source).resolve()
    work_dir = Path(work_dir)
    evidence_path = Path(evidence_path)
    structure_path = Path(structure_path)
    visuals_path = Path(visuals_path)
    raw_document = _run_docling_worker(
        source,
        work_dir,
        timeout_seconds=worker_timeout_seconds,
    )
    evidence, structure = docling_document_to_ir(raw_document, source_file=source.name)
    visuals = render_visual_objects(source, structure, work_dir / "assets")
    for path, value in (
        (evidence_path, evidence),
        (structure_path, structure),
        (visuals_path, visuals),
    ):
        _write_json_atomic(path, value)
    return evidence, structure, visuals
