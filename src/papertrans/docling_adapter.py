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
import time
import unicodedata
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf as fitz

from .deterministic_structure import visual_caption_score
from .docling_contract import (
    DOCLING_CONTENT_COLLECTIONS as _CONTENT_COLLECTIONS,
    DOCLING_DOCUMENT_TIMEOUT_SECONDS as _DOCLING_DOCUMENT_TIMEOUT_SECONDS,
    DOCLING_MAX_CONTENT_ITEMS,
    DOCLING_MAX_CPU_SECONDS,
    DOCLING_MAX_DOCUMENT_JSON_BYTES,
    DOCLING_MAX_MEMORY_BYTES,
    DOCLING_MAX_OUTPUT_FILE_BYTES,
    DOCLING_MAX_PAGE_DIMENSION_POINTS,
    DOCLING_MAX_PAGES,
    DOCLING_MAX_PARSER_THREADS,
    DOCLING_MAX_PDF_OBJECTS,
    DOCLING_MAX_RASTER_OUTPUT_BYTES,
    DOCLING_MAX_RASTER_RENDERS,
    DOCLING_MAX_SINGLE_RASTER_PIXELS,
    DOCLING_MAX_SOURCE_BYTES,
    DOCLING_MAX_TEXT_CHARACTERS,
    DOCLING_MAX_TOTAL_RASTER_PIXELS,
    DOCLING_MAX_VISUAL_OBJECTS,
    DOCLING_MAX_WORKER_TIMEOUT_SECONDS,
    DOCLING_MEMORY_POLL_SECONDS as _DOCLING_MEMORY_POLL_SECONDS,
    DOCLING_PARTIAL_EXIT_CODE as _DOCLING_PARTIAL_EXIT_CODE,
    DOCLING_RESOURCE_LIMIT_EXIT_CODE as _DOCLING_RESOURCE_LIMIT_EXIT_CODE,
    DOCLING_WORKER_LOG_LIMIT_BYTES as _DOCLING_WORKER_LOG_LIMIT_BYTES,
    DOCLING_WORKER_TIMEOUT_SECONDS as _DOCLING_WORKER_TIMEOUT_SECONDS,
    DoclingAdapterError,
    DoclingPartialConversionError,
    DoclingResourceLimitError,
    DoclingUnavailableError,
    DoclingWorkerError,
    DoclingWorkerTimeoutError,
)
from .docling_resources import (
    load_resource_module,
    new_raster_budget,
    resolve_worker_timeout,
    temporary_process_resource_limits,
    validate_document_limits,
    validate_source_pdf,
)
from .docling_worker_runtime import (
    load_psutil_module,
    read_bounded_worker_json,
    run_supervised_command,
    terminate_process_tree,
    write_bounded_json_atomic,
)
from .pdf_caption_refinement import refine_pdf_caption_texts
from .structure import (
    PdfRenderBudget,
    render_visual_objects,
    validate_structure_batch,
)


def _resolve_docling_worker_timeout(value: float | None) -> float:
    return resolve_worker_timeout(
        value,
        default_seconds=_DOCLING_WORKER_TIMEOUT_SECONDS,
        max_seconds=DOCLING_MAX_WORKER_TIMEOUT_SECONDS,
    )


def _load_resource_module() -> Any | None:
    return load_resource_module()


def _load_psutil_module() -> Any:
    return load_psutil_module()


def _temporary_docling_resource_limits(timeout_seconds: float):
    return temporary_process_resource_limits(
        timeout_seconds,
        load_resource=_load_resource_module,
        max_memory_bytes=DOCLING_MAX_MEMORY_BYTES,
        max_cpu_seconds=DOCLING_MAX_CPU_SECONDS,
        max_output_file_bytes=DOCLING_MAX_OUTPUT_FILE_BYTES,
    )


def _validate_docling_source_pdf(source: Path) -> int:
    return validate_source_pdf(
        source,
        max_source_bytes=DOCLING_MAX_SOURCE_BYTES,
        max_pages=DOCLING_MAX_PAGES,
        max_pdf_objects=DOCLING_MAX_PDF_OBJECTS,
        max_page_dimension_points=DOCLING_MAX_PAGE_DIMENSION_POINTS,
    )


def _new_docling_raster_budget() -> PdfRenderBudget:
    return new_raster_budget(
        max_renders=DOCLING_MAX_RASTER_RENDERS,
        max_single_pixels=DOCLING_MAX_SINGLE_RASTER_PIXELS,
        max_total_pixels=DOCLING_MAX_TOTAL_RASTER_PIXELS,
        max_output_bytes=DOCLING_MAX_RASTER_OUTPUT_BYTES,
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
    r"\b(?P<kind>Figures?|Figs?\.?|Tables?|Algorithms?|Equations?|Eqs?\.?)\s*"
    r"(?P<values>"
    r"\(?(?:[A-Z]\.)?\d+(?:[.\-][A-Za-z0-9]+)*\)?"
    r"(?:\s*(?:,|and|&)\s*\(?(?:[A-Z]\.)?\d+(?:[.\-][A-Za-z0-9]+)*\)?)*"
    r")",
    re.IGNORECASE,
)
_OBJECT_REFERENCE_VALUE = re.compile(
    r"(?:[A-Z]\.)?\d+(?:[.\-][A-Za-z0-9]+)*", re.IGNORECASE
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


def _write_json_atomic(
    path: Path,
    value: Any,
    *,
    max_bytes: int | None = None,
) -> None:
    write_bounded_json_atomic(path, value, max_bytes=max_bytes)


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
            "`uv sync --group docling`."
        ) from error
    try:
        importlib.import_module("onnxruntime")
    except (ImportError, ModuleNotFoundError) as error:
        raise DoclingUnavailableError(
            "Docling's ONNX layout backend is unavailable; install the "
            "PaperTrans source-checkout `docling` group with `uv sync --group docling`."
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
    if document_timeout > DOCLING_MAX_WORKER_TIMEOUT_SECONDS:
        raise DoclingResourceLimitError(
            "PAPERTRANS_DOCLING_DOCUMENT_TIMEOUT must not exceed "
            f"{DOCLING_MAX_WORKER_TIMEOUT_SECONDS:g} seconds"
        )
    raw_parser_threads = os.environ.get("PAPERTRANS_DOCLING_PARSER_THREADS")
    parser_threads = DOCLING_MAX_PARSER_THREADS
    if raw_parser_threads is not None:
        try:
            parser_threads = int(raw_parser_threads)
        except ValueError as error:
            raise DoclingAdapterError(
                "PAPERTRANS_DOCLING_PARSER_THREADS must be a positive integer"
            ) from error
        if not 1 <= parser_threads <= DOCLING_MAX_PARSER_THREADS:
            raise DoclingAdapterError(
                "PAPERTRANS_DOCLING_PARSER_THREADS must be between 1 and "
                f"{DOCLING_MAX_PARSER_THREADS}"
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
    result = converter.convert(
        Path(source),
        raises_on_error=False,
        max_num_pages=DOCLING_MAX_PAGES,
        max_file_size=DOCLING_MAX_SOURCE_BYTES,
    )
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


def _terminate_docling_process_tree(
    process: subprocess.Popen[Any],
    *,
    tracked_processes: Sequence[Any] = (),
    timeout: float = 2.0,
) -> None:
    terminate_process_tree(
        process,
        psutil_module=_load_psutil_module(),
        tracked_processes=tracked_processes,
        timeout=timeout,
    )


def _run_docling_command(
    command: Sequence[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    return run_supervised_command(
        command,
        max_memory_bytes=DOCLING_MAX_MEMORY_BYTES,
        memory_poll_seconds=_DOCLING_MEMORY_POLL_SECONDS,
        psutil_module=_load_psutil_module(),
        terminate_tree=_terminate_docling_process_tree,
        **kwargs,
    )


def _read_docling_worker_json(output_path: Path) -> Any:
    return read_bounded_worker_json(
        output_path,
        max_bytes=DOCLING_MAX_DOCUMENT_JSON_BYTES,
    )


def _run_docling_worker(
    source: Path,
    work_dir: Path,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run only native Docling conversion in a crash-isolated child process."""

    source = Path(source).resolve()
    work_dir = Path(work_dir).resolve()
    timeout_seconds = _resolve_docling_worker_timeout(timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
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
        *,
        timeout_for_attempt: float,
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
        attempt_timeout_error: subprocess.TimeoutExpired | None = None
        attempt_error: BaseException | None = None
        try:
            with os.fdopen(write_fd, "wb", buffering=0) as stderr_writer:
                run_kwargs: dict[str, Any] = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": stderr_writer,
                    "cwd": attempt_work_dir or work_dir,
                    "check": False,
                    "timeout": timeout_for_attempt,
                }
                if child_env is not None:
                    run_kwargs["env"] = dict(child_env)
                try:
                    attempt_process = _run_docling_command(command, **run_kwargs)
                except subprocess.TimeoutExpired as error:
                    attempt_timeout_error = error
                except BaseException as error:
                    attempt_error = error
        finally:
            stderr_reader.join()
        return attempt_process, attempt_timeout_error, attempt_error

    process, timeout_error, run_error = run_attempt(
        timeout_for_attempt=timeout_seconds
    )
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
        remaining_timeout = deadline - time.monotonic()
        if remaining_timeout <= 0:
            timeout_error = subprocess.TimeoutExpired(command, timeout_seconds)
        else:
            with tempfile.TemporaryDirectory(
                prefix=".docling-retry-", dir=work_dir
            ) as retry_directory:
                process, timeout_error, run_error = run_attempt(
                    retry_env,
                    Path(retry_directory),
                    timeout_for_attempt=remaining_timeout,
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
        if isinstance(
            run_error,
            (DoclingResourceLimitError, DoclingUnavailableError),
        ):
            raise run_error
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
        if process.returncode == _DOCLING_RESOURCE_LIMIT_EXIT_CODE:
            raise DoclingResourceLimitError(
                f"Docling worker exceeded a resource limit; see {log_path}"
            )
        if process.returncode != 0:
            raise DoclingWorkerError(
                f"Docling worker exited with status {process.returncode}; see {log_path}"
            )
        try:
            value = _read_docling_worker_json(output_path)
        except DoclingResourceLimitError:
            raise
        except DoclingWorkerError as error:
            raise DoclingWorkerError(
                f"Docling worker did not produce valid JSON at {output_path}; see {log_path}"
            ) from error
        document = docling_document_to_dict(value)
        _write_json_atomic(
            canonical_output_path,
            document,
            max_bytes=DOCLING_MAX_DOCUMENT_JSON_BYTES,
        )
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


def _validate_docling_document_limits(
    document: Mapping[str, Any],
    *,
    source_pages: int,
) -> None:
    validate_document_limits(
        document,
        source_pages=source_pages,
        content_collections=_CONTENT_COLLECTIONS,
        collection_values=_collection_values,
        max_content_items=DOCLING_MAX_CONTENT_ITEMS,
        max_text_characters=DOCLING_MAX_TEXT_CHARACTERS,
        max_visual_objects=DOCLING_MAX_VISUAL_OBJECTS,
    )


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


def _normalize_displaced_pdf_diacritics(text: str) -> str:
    """Move a detached diaeresis from the prior consonant onto its vowel."""

    return re.sub(
        r"([^\W\d_])\u00a8\s*([AEIOUYaeiouy])",
        lambda match: match.group(1)
        + unicodedata.normalize("NFC", match.group(2) + "\u0308"),
        text,
    )


def _normalize_spaced_urls(text: str) -> str:
    """Normalize only the URL scheme; path repair needs PDF annotations."""

    return re.sub(
        r"\b(https?)\s*:\s*/\s*/\s*",
        lambda match: f"{match.group(1).lower()}://",
        text,
        flags=re.IGNORECASE,
    )


_UNANNOTATED_HOST = re.compile(
    r"\bhttps?://\s*(?:[A-Za-z0-9-]+\s*\.\s*)+"
    r"(?:com|org|net|edu|gov|io|ai|co|dev|app|info|me|us|uk|de|fr|jp|cn|ch|ly|tv|xyz)\b",
    re.IGNORECASE,
)
_URL_SAFE_PREFIX = r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+;=%-]+"


def _normalize_unannotated_printed_urls(text: str) -> str:
    """Apply only structurally unambiguous URL spacing repairs.

    Unlike annotation-backed recovery, this never guesses a missing path
    token.  It only removes PDF-inserted spaces inside a DNS host, around a
    path slash, or before a familiar file extension.
    """

    value = _normalize_spaced_urls(text)
    value = _UNANNOTATED_HOST.sub(
        lambda match: re.sub(r"\s+", "", match.group(0)), value
    )
    slash_spacing = re.compile(
        rf"({_URL_SAFE_PREFIX})\s*/\s*(?=[A-Za-z0-9])",
        re.IGNORECASE,
    )
    extension_spacing = re.compile(
        rf"({_URL_SAFE_PREFIX})\s*\.\s*(?=(?:html?|pdf)\b)",
        re.IGNORECASE,
    )
    while True:
        repaired = slash_spacing.sub(r"\1/", value)
        repaired = extension_spacing.sub(r"\1.", repaired)
        if repaired == value:
            return value
        value = repaired


def _normalize_extracted_text(text: str) -> str:
    return _normalize_displaced_pdf_diacritics(text)


def _text(item: Mapping[str, Any]) -> str:
    return _normalize_extracted_text(_raw_text(item).strip())


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
    # Docling occasionally reports the final span one or two code points past
    # its exported text after ligature normalization.  A tiny terminal clamp
    # preserves otherwise exact multi-page provenance without guessing an
    # interior boundary.
    if 0 <= start < min(end, text_length) and end <= text_length + 2:
        return start, min(end, text_length)
    return None


def _partition_provenance_char_spans(
    provenance_values: list[Mapping[str, Any]], raw_text: str
) -> list[tuple[int, int]] | None:
    """Return ordered spans, absorbing only tiny Docling boundary drift."""

    terminal_overrun = False
    for provenance in provenance_values:
        raw_span = provenance.get("charspan", provenance.get("char_span"))
        if (
            isinstance(raw_span, Sequence)
            and not isinstance(raw_span, (str, bytes, bytearray))
            and len(raw_span) == 2
        ):
            try:
                terminal_overrun = terminal_overrun or (
                    len(raw_text) < int(raw_span[1]) <= len(raw_text) + 2
                )
            except (TypeError, ValueError):
                pass
    raw_spans = [_char_span(value, len(raw_text)) for value in provenance_values]
    if not all(span is not None for span in raw_spans):
        return None
    spans = [span for span in raw_spans if span is not None]
    for index in range(len(spans)):
        start, end = spans[index]
        previous_end = spans[index - 1][1] if index else 0
        if start < previous_end:
            return None
        gap = raw_text[previous_end:start]
        if gap.strip():
            if len(gap) > 2 or not terminal_overrun or index == 0:
                return None
            word_char = lambda value: value.isalnum() or value in "-_"
            word_start = previous_end
            while word_start > 0 and word_char(raw_text[word_start - 1]):
                word_start -= 1
            word_end = start
            while word_end < len(raw_text) and word_char(raw_text[word_end]):
                word_end += 1
            backward_distance = previous_end - word_start
            forward_distance = word_end - start
            boundary = (
                word_end
                if forward_distance <= backward_distance
                else word_start
            )
            previous_start, _ = spans[index - 1]
            if previous_start >= boundary or boundary >= end:
                return None
            spans[index - 1] = (previous_start, boundary)
            spans[index] = (boundary, end)
    trailing = raw_text[spans[-1][1] :]
    if trailing.strip():
        if len(trailing) > 2:
            return None
        start, _ = spans[-1]
        spans[-1] = (start, len(raw_text))
    return spans


def _citations(text: str) -> list[str]:
    return list(
        dict.fromkeys(_NUMERIC_CITATION.findall(text) + _AUTHOR_YEAR_CITATION.findall(text))
    )


def _object_references(text: str) -> list[str]:
    references: list[str] = []
    for match in _OBJECT_REFERENCE.finditer(text):
        raw_kind = match.group("kind").casefold().rstrip(".")
        if raw_kind.startswith("fig"):
            kind = "Figure"
        elif raw_kind.startswith("table"):
            kind = "Table"
        elif raw_kind.startswith("algorithm"):
            kind = "Algorithm"
        else:
            kind = "Equation"
        values = _OBJECT_REFERENCE_VALUE.findall(match.group("values"))
        for value in values:
            reference = f"{kind} {value}"
            if reference not in references:
                references.append(reference)
    return references


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


def _align_visual_captions(
    caption_records: list[dict[str, Any]],
    visual_entries: list[dict[str, Any]],
    explicit_caption_owners: dict[str, set[str]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return a maximum-score, order-preserving caption/visual alignment.

    Docling's body graph usually places each visual immediately before its
    caption.  Purely greedy geometry can nevertheless shift a whole run of
    tables when a caption is a few points closer to the next table.  Aligning
    each page/kind sequence globally preserves that graph order while still
    allowing unlabeled visuals and orphan captions to be skipped.

    Explicit ownership is supporting evidence rather than a hard constraint:
    some real documents attach Figure 6 to the Figure 7 picture.  A small bonus
    repairs ambiguous table boundaries without overpowering a clear geometric
    match.  An explicit same-page/same-kind edge is also retained as a guarded
    fallback when Docling's crop already contains the caption.
    """

    grouped_captions: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for record in caption_records:
        kind = _object_label_kind(record["text"])
        if kind is not None:
            grouped_captions.setdefault((record["pageNumber"], kind), []).append(
                record
            )
    grouped_visuals: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for visual_entry in visual_entries:
        grouped_visuals.setdefault(
            (visual_entry["pageNumber"], visual_entry["kind"]), []
        ).append(visual_entry)

    aligned: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for group_key in sorted(set(grouped_captions) & set(grouped_visuals)):
        captions = sorted(
            grouped_captions[group_key],
            key=lambda value: (int(value["order"]), int(value["segmentIndex"])),
        )
        visuals = sorted(
            grouped_visuals[group_key], key=lambda value: int(value["order"])
        )
        edges: dict[tuple[int, int], tuple[float, int, int]] = {}
        for caption_index, record in enumerate(captions):
            caption_bbox = tuple(
                float(value) for value in record["bboxNormalized"]
            )
            for visual_index, visual_entry in enumerate(visuals):
                visual_bbox = tuple(
                    float(value) for value in visual_entry["bboxNormalized"]
                )
                geometry_score = visual_caption_score(
                    group_key[1], caption_bbox, visual_bbox
                )
                explicit = int(
                    visual_entry["ref"]
                    in explicit_caption_owners.get(record["ref"], set())
                )
                if geometry_score < 0 and not (
                    explicit
                    and _explicit_caption_fallback_plausible(
                        caption_bbox, visual_bbox
                    )
                ):
                    continue
                record["visualCaptionCandidate"] = True
                # Explicit fallback remains positive so an overlapping caption
                # can complete an otherwise monotone run.  The 0.18 ownership
                # bonus is smaller than the observed Figure 6 geometry margin.
                utility = (geometry_score if geometry_score >= 0 else 2.25) + (
                    0.18 if explicit else 0.0
                )
                order_distance = abs(
                    int(record["order"]) - int(visual_entry["order"])
                )
                edges[(caption_index, visual_index)] = (
                    utility,
                    explicit,
                    order_distance,
                )

        # Each state stores a lexicographic quality key and matched index pairs.
        # Score dominates; then prefer more evidence, explicit support, and a
        # shorter graph distance.  Skips carry zero cost.
        empty_state: tuple[
            tuple[float, int, int, int], tuple[tuple[int, int], ...]
        ] = ((0.0, 0, 0, 0), ())
        states = [
            [empty_state for _visual in range(len(visuals) + 1)]
            for _caption in range(len(captions) + 1)
        ]
        for caption_count in range(len(captions) + 1):
            for visual_count in range(len(visuals) + 1):
                if caption_count == 0 and visual_count == 0:
                    continue
                options: list[
                    tuple[
                        tuple[float, int, int, int],
                        tuple[tuple[int, int], ...],
                    ]
                ] = []
                if caption_count:
                    options.append(states[caption_count - 1][visual_count])
                if visual_count:
                    options.append(states[caption_count][visual_count - 1])
                edge = edges.get((caption_count - 1, visual_count - 1))
                if caption_count and visual_count and edge is not None:
                    previous_key, previous_pairs = states[caption_count - 1][
                        visual_count - 1
                    ]
                    utility, explicit, order_distance = edge
                    options.append(
                        (
                            (
                                round(previous_key[0] + utility, 12),
                                previous_key[1] + 1,
                                previous_key[2] + explicit,
                                previous_key[3] - order_distance,
                            ),
                            previous_pairs
                            + ((caption_count - 1, visual_count - 1),),
                        )
                    )
                states[caption_count][visual_count] = max(
                    options, key=lambda value: value[0]
                )
        for caption_index, visual_index in states[-1][-1][1]:
            aligned.append((captions[caption_index], visuals[visual_index]))
    return aligned


def _explicit_caption_fallback_plausible(
    caption_bbox: tuple[float, float, float, float],
    visual_bbox: tuple[float, float, float, float],
) -> bool:
    """Accept an explicit fallback only at an overlapping/near crop edge."""

    horizontal_overlap = max(
        0.0,
        min(caption_bbox[2], visual_bbox[2])
        - max(caption_bbox[0], visual_bbox[0]),
    )
    minimum_width = max(
        1e-9,
        min(
            caption_bbox[2] - caption_bbox[0],
            visual_bbox[2] - visual_bbox[0],
        ),
    )
    if horizontal_overlap / minimum_width < 0.15:
        return False
    vertical_gap = max(
        0.0,
        max(caption_bbox[1], visual_bbox[1])
        - min(caption_bbox[3], visual_bbox[3]),
    )
    if vertical_gap > 0.035:
        return False
    if vertical_gap > 0:
        return True
    boundary_distance = min(
        abs(caption_bbox[1] - visual_bbox[1]),
        abs(caption_bbox[1] - visual_bbox[3]),
        abs(caption_bbox[3] - visual_bbox[1]),
        abs(caption_bbox[3] - visual_bbox[3]),
    )
    return boundary_distance <= 0.035


def _caption_same_line_vertical(
    left: dict[str, Any], right: dict[str, Any]
) -> bool:
    left_bbox = left["bboxNormalized"]
    right_bbox = right["bboxNormalized"]
    overlap = max(
        0.0, min(left_bbox[3], right_bbox[3]) - max(left_bbox[1], right_bbox[1])
    )
    minimum_height = max(
        1e-9,
        min(left_bbox[3] - left_bbox[1], right_bbox[3] - right_bbox[1]),
    )
    center_distance = abs(
        (left_bbox[1] + left_bbox[3]) / 2
        - (right_bbox[1] + right_bbox[3]) / 2
    )
    return overlap / minimum_height >= 0.35 and center_distance <= 0.012


def _caption_line_groups(
    records: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group fragments into visual text lines, then order each line by x."""

    lines: list[list[dict[str, Any]]] = []
    for record in sorted(
        records,
        key=lambda value: (
            (value["bboxNormalized"][1] + value["bboxNormalized"][3]) / 2,
            value["bboxNormalized"][0],
            int(value["order"]),
        ),
    ):
        matching_line = next(
            (
                line
                for line in lines
                if any(_caption_same_line_vertical(record, value) for value in line)
            ),
            None,
        )
        if matching_line is None:
            lines.append([record])
        else:
            matching_line.append(record)
    for line in lines:
        line.sort(
            key=lambda value: (
                value["bboxNormalized"][0],
                value["bboxNormalized"][1],
                int(value["order"]),
            )
        )
    lines.sort(
        key=lambda line: (
            min(value["bboxNormalized"][1] for value in line),
            min(value["bboxNormalized"][0] for value in line),
        )
    )
    return lines


_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _normalize_caption_superscripts(line: list[dict[str, Any]]) -> None:
    """Recover only isolated, geometrically unambiguous superscript digits."""

    for index in range(1, len(line) - 1):
        record = line[index]
        text = record["text"].strip()
        if len(text) != 1 or text not in "0123456789":
            continue
        previous = line[index - 1]
        following = line[index + 1]
        if not previous["text"].rstrip()[-1:].isalnum() or not following[
            "text"
        ].lstrip()[:1].isalpha():
            continue
        bbox = record["bboxNormalized"]
        previous_bbox = previous["bboxNormalized"]
        following_bbox = following["bboxNormalized"]
        horizontal_gap_before = max(0.0, bbox[0] - previous_bbox[2])
        horizontal_gap_after = max(0.0, following_bbox[0] - bbox[2])
        if horizontal_gap_before > 0.012 or horizontal_gap_after > 0.012:
            continue
        height = bbox[3] - bbox[1]
        neighbor_height = min(
            previous_bbox[3] - previous_bbox[1],
            following_bbox[3] - following_bbox[1],
        )
        center = (bbox[1] + bbox[3]) / 2
        neighbor_center = (
            (previous_bbox[1] + previous_bbox[3]) / 2
            + (following_bbox[1] + following_bbox[3]) / 2
        ) / 2
        if (
            height > neighbor_height * 0.85
            or neighbor_center - center < max(0.0015, neighbor_height * 0.18)
        ):
            continue
        record["text"] = text.translate(_SUPERSCRIPT_DIGITS)
        record["warnings"].append(
            "An isolated elevated caption digit was normalized to its Unicode superscript form."
        )


def _caption_fragment_connected(
    candidate: dict[str, Any], existing: dict[str, Any]
) -> bool:
    candidate_bbox = candidate["bboxNormalized"]
    existing_bbox = existing["bboxNormalized"]
    horizontal_gap = max(
        0.0,
        max(candidate_bbox[0], existing_bbox[0])
        - min(candidate_bbox[2], existing_bbox[2]),
    )
    vertical_gap = max(
        0.0,
        max(candidate_bbox[1], existing_bbox[1])
        - min(candidate_bbox[3], existing_bbox[3]),
    )
    horizontal_overlap = max(
        0.0,
        min(candidate_bbox[2], existing_bbox[2])
        - max(candidate_bbox[0], existing_bbox[0]),
    )
    minimum_width = max(
        1e-9,
        min(
            candidate_bbox[2] - candidate_bbox[0],
            existing_bbox[2] - existing_bbox[0],
        ),
    )
    return vertical_gap <= 0.035 and (
        horizontal_gap <= 0.035 or horizontal_overlap / minimum_width >= 0.15
    )


def _rectangle_intersection_area(left: list[float], right: list[float]) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


_RECOVERED_INTERNAL_CAPTION_WARNING = (
    "Docling's picture crop crossed an internal caption boundary; the crop was "
    "recovered from source-PDF geometry."
)


def _normalized_pdf_rect(rect: fitz.Rect, page: fitz.Page) -> list[float]:
    return [
        (rect.x0 - page.rect.x0) / page.rect.width,
        (rect.y0 - page.rect.y0) / page.rect.height,
        (rect.x1 - page.rect.x0) / page.rect.width,
        (rect.y1 - page.rect.y0) / page.rect.height,
    ]


def _pdf_text_regions(page: fitz.Page) -> list[dict[str, Any]]:
    """Return source-PDF text blocks and line-level font evidence.

    This is intentionally only used after the narrow internal-caption trigger
    fires.  Docling text remains authoritative; PDF spans provide geometry and
    font evidence for separating a run-in heading, equation, and prose that a
    malformed picture node swallowed.
    """

    regions: list[dict[str, Any]] = []
    raw = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT)
    for region_index, block in enumerate(raw.get("blocks", [])):
        if int(block.get("type", -1)) != 0 or not block.get("bbox"):
            continue
        lines: list[dict[str, Any]] = []
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("bbox")]
            if not spans:
                continue
            character_count = 0
            bold_count = 0
            math_count = 0
            text_parts: list[str] = []
            for span in spans:
                text = str(span.get("text", ""))
                count = len(text.strip())
                character_count += count
                text_parts.append(text)
                font = str(span.get("font", "")).lower()
                flags = int(span.get("flags", 0))
                if "bold" in font or "medi" in font or flags & 16:
                    bold_count += count
                if any(
                    token in font
                    for token in ("cmmi", "cmsy", "cmex", "math", "symbol")
                ):
                    math_count += count
            line_rect = fitz.Rect(line.get("bbox") or spans[0]["bbox"])
            lines.append(
                {
                    "bboxNormalized": _normalized_pdf_rect(line_rect, page),
                    "text": "".join(text_parts).strip(),
                    "boldRatio": bold_count / character_count
                    if character_count
                    else 0.0,
                    "mathRatio": math_count / character_count
                    if character_count
                    else 0.0,
                    "fontSizeMax": max(
                        (float(span.get("size", 0)) for span in spans), default=0.0
                    ),
                }
            )
        if not lines:
            continue
        regions.append(
            {
                "regionIndex": region_index,
                "bboxNormalized": _normalized_pdf_rect(
                    fitz.Rect(block["bbox"]), page
                ),
                "lines": lines,
            }
        )
    return regions


_FRONT_AFFILIATION = re.compile(
    r"\b(?:berkeley|college|corporation|department|facebook|google|institute|laborator(?:y|ies)|"
    r"microsoft|research|runway|school|university)\b",
    re.IGNORECASE,
)
_FRONT_EMAIL = re.compile(r"\S+@\S+")


def _front_line_role(text: str) -> str:
    if "{" in text or "}" in text or re.search(r"\bhttps?://|\bwww\.", text):
        return "metadata"
    email = _FRONT_EMAIL.search(text)
    if email is not None:
        prefix = text[: email.start()].strip(" ,;:({")
        name_tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿŁł'’\-]+", prefix)
        if len(name_tokens) >= 2 and not text.lstrip().startswith("{"):
            return "author"
        return "metadata"
    if _FRONT_AFFILIATION.search(text):
        return "affiliation"
    if re.match(r"^\s*\(?version\b", text, re.IGNORECASE):
        return "metadata"
    return "author"


def _front_lines_in_reading_order(
    lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[list[dict[str, Any]]] = []
    for line in sorted(
        lines,
        key=lambda value: (
            (float(value["bboxNormalized"][1]) + float(value["bboxNormalized"][3]))
            / 2,
            float(value["bboxNormalized"][0]),
        ),
    ):
        center = (
            float(line["bboxNormalized"][1]) + float(line["bboxNormalized"][3])
        ) / 2
        matching = next(
            (
                row
                for row in rows
                if abs(
                    center
                    - sum(
                        (
                            float(value["bboxNormalized"][1])
                            + float(value["bboxNormalized"][3])
                        )
                        / 2
                        for value in row
                    )
                    / len(row)
                )
                <= 0.006
            ),
            None,
        )
        if matching is None:
            rows.append([line])
        else:
            matching.append(line)
    ordered: list[dict[str, Any]] = []
    for row in rows:
        ordered.extend(
            sorted(row, key=lambda value: float(value["bboxNormalized"][0]))
        )
    return ordered


def _recover_page_one_front_matter(
    evidence: dict[str, Any],
    structure: dict[str, Any],
    pdf_lines: list[dict[str, Any]],
) -> int:
    """Rebuild the title-to-abstract band in source-PDF row order."""

    evidence_page = next(
        (page for page in evidence.get("pages", []) if page.get("pageNumber") == 1),
        None,
    )
    structure_page = next(
        (page for page in structure.get("pages", []) if page.get("pageNumber") == 1),
        None,
    )
    if evidence_page is None or structure_page is None:
        return 0
    blocks = {
        str(block.get("blockId")): block for block in evidence_page.get("blocks", [])
    }
    all_blocks = {
        str(block.get("blockId")): block
        for page in evidence.get("pages", [])
        for block in page.get("blocks", [])
    }
    assignments = list(structure_page.get("blockAssignments", []))
    title_assignments = [
        assignment
        for assignment in assignments
        if not assignment.get("hidden")
        and assignment.get("role") == "title"
        and str(assignment.get("blockId")) in blocks
    ]
    if not title_assignments:
        return 0
    title_bottom = max(
        float(blocks[str(assignment["blockId"])]["bboxNormalized"][3])
        for assignment in title_assignments
    )
    boundary_candidates: list[tuple[float, dict[str, Any]]] = []
    for assignment in assignments:
        block = blocks.get(str(assignment.get("blockId")))
        if (
            block is None
            or assignment.get("hidden")
            or assignment.get("role") != "heading"
        ):
            continue
        text = re.sub(r"\s+", " ", str(block.get("text", ""))).strip().lower()
        text = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", text)
        top = float(block["bboxNormalized"][1])
        if top > title_bottom and text.rstrip(":") in {"abstract", "introduction"}:
            boundary_candidates.append((top, assignment))
    if not boundary_candidates:
        return 0
    boundary_top = min(value[0] for value in boundary_candidates)
    candidate_lines = []
    seen_lines: set[tuple[str, tuple[float, ...]]] = set()
    for line in pdf_lines:
        text = _normalize_extracted_text(
            re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
        )
        bbox = [float(value) for value in line.get("bboxNormalized", [])]
        if (
            not text
            or len(bbox) != 4
            or float(line.get("fontSizeMax", 0)) < 7.0
            or bbox[1] < title_bottom - 0.002
            or bbox[3] > boundary_top + 0.002
        ):
            continue
        key = (text, tuple(round(value, 5) for value in bbox))
        if key in seen_lines:
            continue
        seen_lines.add(key)
        candidate_lines.append({**line, "text": text, "bboxNormalized": bbox})
    ordered_lines = _front_lines_in_reading_order(candidate_lines)
    if not ordered_lines or not any(
        _front_line_role(line["text"]) == "author" for line in ordered_lines
    ):
        return 0

    candidate_ids: set[str] = set()
    allowed_roles = {
        "affiliation",
        "author",
        "footnote",
        "list_item",
        "metadata",
        "paragraph",
    }
    for assignment in assignments:
        block_id = str(assignment.get("blockId"))
        block = blocks.get(block_id)
        if block is None or assignment.get("role") not in allowed_roles:
            continue
        bbox = [float(value) for value in block.get("bboxNormalized", [])]
        if len(bbox) != 4:
            continue
        center = (bbox[1] + bbox[3]) / 2
        if title_bottom - 0.002 <= center <= boundary_top + 0.002:
            candidate_ids.add(block_id)
            assignment["role"] = "noise"
            assignment["paragraphId"] = None
            assignment["continuesFrom"] = None
            assignment["hidden"] = True
            assignment.setdefault("warnings", []).append(
                "A Docling column-ordered front-matter block was replaced by source-PDF row geometry."
            )
    all_assignments_by_id = {
        str(assignment["blockId"]): assignment
        for page in structure.get("pages", [])
        for assignment in page.get("blockAssignments", [])
    }
    for page in structure.get("pages", []):
        for visual in page.get("visualObjects", []):
            if str(visual.get("insertAfterBlockId") or "") not in candidate_ids:
                continue
            caption_anchor = next(
                (
                    str(block_id)
                    for block_id in reversed(visual.get("captionBlockIds", []))
                    if str(block_id) in all_assignments_by_id
                    and not all_assignments_by_id[str(block_id)].get("hidden")
                ),
                None,
            )
            if caption_anchor is not None:
                visual["insertAfterBlockId"] = caption_anchor
                visual.setdefault("warnings", []).append(
                    "A visual anchored to misordered front matter was reanchored to its exact caption."
                )
    ordered_assignments = [
        assignment
        for page in sorted(
            structure.get("pages", []), key=lambda value: int(value["pageNumber"])
        )
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value["readingOrder"]),
        )
    ]
    for position, assignment in enumerate(ordered_assignments):
        if str(assignment.get("continuesFrom") or "") not in candidate_ids:
            continue
        text = str(all_blocks.get(str(assignment.get("blockId")), {}).get("text", "")).lstrip()
        previous = None
        if text[:1].islower() or text[:1] in ",.;:)]}":
            previous = next(
                (
                    value
                    for value in reversed(ordered_assignments[:position])
                    if not value.get("hidden")
                    and str(value.get("blockId")) not in candidate_ids
                    and value.get("role") == assignment.get("role")
                    and value.get("sectionId") == assignment.get("sectionId")
                    and value.get("paragraphId")
                ),
                None,
            )
        if previous is not None:
            assignment["paragraphId"] = previous["paragraphId"]
            assignment["continuesFrom"] = previous["blockId"]
            warning = (
                "A lowercase body tail falsely attached to page-one front matter "
                "was rejoined to the latest visible paragraph in its section."
            )
        else:
            assignment["paragraphId"] = f"para-{assignment['blockId']}"
            assignment["continuesFrom"] = None
            warning = "A false body continuation from page-one front matter was severed."
        assignment.setdefault("warnings", []).append(warning)

    width = float(evidence_page.get("widthPdf", 1))
    height = float(evidence_page.get("heightPdf", 1))
    synthetic_blocks: list[dict[str, Any]] = []
    synthetic_assignments: list[dict[str, Any]] = []
    for index, line in enumerate(ordered_lines, start=1):
        block_id = f"pdf-front-{index:03d}"
        bbox = line["bboxNormalized"]
        role = _front_line_role(line["text"])
        synthetic_blocks.append(
            {
                "blockId": block_id,
                "sourceBlockId": block_id,
                "text": line["text"],
                "bboxPdf": [
                    round(bbox[0] * width, 2),
                    round(bbox[1] * height, 2),
                    round(bbox[2] * width, 2),
                    round(bbox[3] * height, 2),
                ],
                "bboxNormalized": [round(value, 5) for value in bbox],
                "lineCount": 1,
                "fontSizeMin": float(line.get("fontSizeMax", 0)),
                "fontSizeMax": float(line.get("fontSizeMax", 0)),
                "fonts": [],
                "bold": float(line.get("boldRatio", 0)) >= 0.5,
                "italic": False,
                "mathCharacterRatio": float(line.get("mathRatio", 0)),
                "embeddedVisualOwnerRefs": [],
                "suppressedVisualText": False,
                "visualCaptionCandidate": False,
                "associatedVisualCaption": False,
            }
        )
        synthetic_assignments.append(
            {
                "blockId": block_id,
                "role": role,
                "readingOrder": 0,
                "sectionId": None,
                "paragraphId": None,
                "continuesFrom": None,
                "hidden": False,
                "citations": [],
                "objectReferences": [],
                "referenceLabel": None,
                "suppressedVisualText": False,
                "visualCaptionCandidate": False,
                "associatedVisualCaption": False,
                "confidence": 1.0,
                "warnings": [
                    "Front matter was reconstructed in row-major order from exact source-PDF text geometry."
                ],
            }
        )

    title_position = max(
        index
        for index, assignment in enumerate(assignments)
        if assignment in title_assignments
    )
    assignments[title_position + 1 : title_position + 1] = synthetic_assignments
    for reading_order, assignment in enumerate(assignments, start=1):
        assignment["readingOrder"] = reading_order
    evidence_page["blocks"].extend(synthetic_blocks)
    structure_page["blockAssignments"] = assignments
    diagnostics = structure.setdefault("doclingDiagnostics", {})
    diagnostics["reconstructedFrontMatterBlocks"] = len(synthetic_assignments)
    return len(synthetic_assignments)


def _recover_page_one_front_matter_from_pdf(
    source: Path,
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> int:
    try:
        with fitz.open(source) as pdf:
            if pdf.page_count < 1:
                return 0
            lines = [
                line
                for region in _pdf_text_regions(pdf[0])
                for line in region.get("lines", [])
            ]
    except (OSError, RuntimeError, ValueError):
        return 0
    return _recover_page_one_front_matter(evidence, structure, lines)


_EXTRACTED_URL_START = re.compile(
    r"\bhttps?\s*:\s*/\s*/", re.IGNORECASE
)


def _pdf_url_alignment_span(
    text: str, start: int, uri: str
) -> dict[str, Any] | None:
    """Align a printed PDF URL with its annotation, ignoring PDF spacing.

    PDF text extraction commonly inserts spaces around punctuation or at a
    column boundary.  Comparing only ASCII alphanumerics lets the annotation
    supply the canonical punctuation without consuming the prose that follows
    the printed URL.
    """

    expected = re.sub(r"[^a-z0-9]", "", uri.casefold())
    if not expected or not 0 <= start < len(text):
        return None
    cursor = start
    matched = 0
    last_match_end = start
    while cursor < len(text) and matched < len(expected):
        character = text[cursor]
        if character.isascii() and character.isalnum():
            if character.casefold() != expected[matched]:
                break
            matched += 1
            last_match_end = cursor + 1
        cursor += 1
    complete = matched == len(expected)
    end = last_match_end
    if complete and uri.rstrip().endswith("/"):
        trailing_slash = re.match(r"\s*/", text[end:])
        if trailing_slash is not None:
            end += trailing_slash.end()
    elif not complete:
        # Retain only URL-like continuation punctuation.  A following full
        # stop/comma belongs to prose and must not be swallowed by repair.
        continuation = re.match(r"[\s\-_/]*", text[end:])
        if continuation is not None:
            end += continuation.end()
        end = len(text[:end].rstrip())
    ratio = matched / max(1, len(expected))
    candidate = text[start:end]
    literal_prefix = _normalize_spaced_urls(candidate).casefold()
    accepted_prefix = (
        literal_prefix.endswith(("-", "_", "/"))
        and uri.casefold().startswith(literal_prefix)
    )
    return {
        "start": start,
        "end": end,
        "complete": complete,
        "ratio": ratio,
        "accepted": complete or ratio >= 0.55 or accepted_prefix,
        "printed": candidate,
    }


def _normalize_url_text_with_annotations(
    text: str, uris: list[str]
) -> str:
    """Canonicalize URL-like spans in derived text such as caption overrides."""

    value = _normalize_displaced_pdf_diacritics(text)
    for match in reversed(list(_EXTRACTED_URL_START.finditer(value))):
        ranked: list[tuple[tuple[int, float, int], str, dict[str, Any]]] = []
        for uri in dict.fromkeys(uris):
            alignment = _pdf_url_alignment_span(value, match.start(), uri)
            if alignment is None or not alignment["accepted"]:
                continue
            ranked.append(
                (
                    (
                        int(bool(alignment["complete"])),
                        float(alignment["ratio"]),
                        int(alignment["end"]),
                    ),
                    uri,
                    alignment,
                )
            )
        if not ranked:
            continue
        ranked.sort(key=lambda item: item[0], reverse=True)
        score, uri, alignment = ranked[0]
        if (
            not alignment["complete"]
            and any(item[0] == score and item[1] != uri for item in ranked[1:])
        ):
            continue
        value = value[: match.start()] + uri + value[int(alignment["end"]) :]
    return _normalize_unannotated_printed_urls(value)


def _recover_pdf_link_annotations(
    source: Path,
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> list[dict[str, str]]:
    """Restore printed URLs from canonical PDF link annotations."""

    diagnostics = structure.setdefault("doclingDiagnostics", {})
    diagnostics.setdefault("recoveredExternalLinks", [])
    diagnostics.setdefault("unrecoveredPrintedUrlLinks", [])
    try:
        pdf = fitz.open(source)
    except Exception:
        return []
    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    structure_pages = {
        int(page["pageNumber"]): page for page in structure.get("pages", [])
    }
    recovered: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    try:
        for page_number in sorted(evidence_pages):
            if not 1 <= page_number <= len(pdf):
                continue
            pdf_page = pdf[page_number - 1]
            uri_rects: dict[str, list[list[float]]] = {}
            for link in pdf_page.get_links():
                uri = str(link.get("uri") or "").strip()
                rect = link.get("from")
                if (
                    not uri.lower().startswith(("http://", "https://"))
                    or rect is None
                ):
                    continue
                uri_rects.setdefault(uri, []).append(
                    _normalized_pdf_rect(fitz.Rect(rect), pdf_page)
                )
            if not uri_rects:
                continue
            assignments = {
                str(assignment["blockId"]): assignment
                for assignment in structure_pages.get(page_number, {}).get(
                    "blockAssignments", []
                )
            }
            records: list[dict[str, Any]] = []
            for evidence_index, block in enumerate(
                evidence_pages[page_number].get("blocks", [])
            ):
                block_id = str(block["blockId"])
                assignment = assignments.get(block_id)
                if assignment is None or assignment.get("hidden"):
                    continue
                bbox = [
                    float(value) for value in block.get("bboxNormalized", [])
                ]
                if len(bbox) != 4:
                    continue
                text = _normalize_extracted_text(str(block.get("text", "")))
                block["text"] = text
                overlapping_uris: list[str] = []
                for uri, rects in uri_rects.items():
                    if any(
                        _rectangle_intersection_area(bbox, rect)
                        / max(
                            1e-9,
                            (rect[2] - rect[0]) * (rect[3] - rect[1]),
                        )
                        >= 0.25
                        for rect in rects
                    ):
                        overlapping_uris.append(uri)
                records.append(
                    {
                        "block": block,
                        "blockId": block_id,
                        "assignment": assignment,
                        "bbox": bbox,
                        "uris": list(dict.fromkeys(overlapping_uris)),
                        "evidenceIndex": evidence_index,
                    }
                )
            records.sort(
                key=lambda value: (
                    int(value["assignment"].get("readingOrder", 10**9)),
                    int(value["evidenceIndex"]),
                )
            )
            for record_index, record in enumerate(records):
                block = record["block"]
                assignment = record["assignment"]
                if assignment.get("hidden"):
                    continue
                text = str(block.get("text", ""))
                matches = list(_EXTRACTED_URL_START.finditer(text))
                if not matches:
                    continue
                if not record["uris"]:
                    block["text"] = _normalize_unannotated_printed_urls(text)
                    continue
                links = list(block.get("externalLinks", []))
                for match in reversed(matches):
                    start = match.start()
                    ranked: list[dict[str, Any]] = []
                    for uri in record["uris"]:
                        stream = text[start:]
                        segments = [
                            {
                                "recordIndex": record_index,
                                "streamStart": 0,
                                "length": len(stream),
                                "localStart": start,
                            }
                        ]
                        for continuation_index in range(
                            record_index + 1, len(records)
                        ):
                            continuation = records[continuation_index]
                            if continuation["assignment"].get("hidden"):
                                continue
                            if uri not in continuation["uris"]:
                                break
                            continuation_text = str(
                                continuation["block"].get("text", "")
                            )
                            stream += " "
                            stream_start = len(stream)
                            stream += continuation_text
                            segments.append(
                                {
                                    "recordIndex": continuation_index,
                                    "streamStart": stream_start,
                                    "length": len(continuation_text),
                                    "localStart": 0,
                                }
                            )
                        alignment = _pdf_url_alignment_span(stream, 0, uri)
                        if alignment is None or not alignment["accepted"]:
                            continue
                        ranked.append(
                            {
                                "uri": uri,
                                "alignment": alignment,
                                "segments": segments,
                                "score": (
                                    int(bool(alignment["complete"])),
                                    float(alignment["ratio"]),
                                    int(alignment["end"]),
                                ),
                            }
                        )
                    if not ranked:
                        unresolved.append(
                            {
                                "blockId": str(record["blockId"]),
                                "pageNumber": str(page_number),
                                "printedUrl": text[start : start + 200],
                            }
                        )
                        continue
                    ranked.sort(key=lambda value: value["score"], reverse=True)
                    best = ranked[0]
                    tied = [
                        value
                        for value in ranked
                        if value["score"] == best["score"]
                        and value["uri"] != best["uri"]
                    ]
                    if tied and not best["alignment"]["complete"]:
                        unresolved.append(
                            {
                                "blockId": str(record["blockId"]),
                                "pageNumber": str(page_number),
                                "printedUrl": text[start : start + 200],
                            }
                        )
                        continue
                    uri = str(best["uri"])
                    alignment = best["alignment"]
                    end = int(alignment["end"])
                    final_segment = best["segments"][0]
                    for segment in best["segments"]:
                        segment_end = int(segment["streamStart"]) + int(
                            segment["length"]
                        )
                        if end <= segment_end:
                            final_segment = segment
                            break
                    final_record_index = int(final_segment["recordIndex"])
                    merged_block_ids: list[str] = []
                    if final_record_index == record_index:
                        text = text[:start] + uri + text[start + end :]
                    else:
                        final_record = records[final_record_index]
                        local_end = max(
                            0, end - int(final_segment["streamStart"])
                        )
                        suffix = str(final_record["block"].get("text", ""))[
                            local_end:
                        ]
                        suffix = re.sub(r"^\s+([,.;:)])", r"\1", suffix)
                        text = text[:start] + uri
                        paragraph_id = assignment.get("paragraphId") or (
                            f"para-{record['blockId']}"
                        )
                        assignment["paragraphId"] = paragraph_id
                        for merged_index in range(
                            record_index + 1, final_record_index + 1
                        ):
                            merged_record = records[merged_index]
                            is_final_suffix = (
                                merged_index == final_record_index
                                and bool(suffix.strip())
                            )
                            if is_final_suffix:
                                merged_record["block"]["text"] = suffix
                                merged_assignment = merged_record["assignment"]
                                for field in (
                                    "role",
                                    "sectionId",
                                    "referenceLabel",
                                ):
                                    merged_assignment[field] = assignment.get(field)
                                merged_assignment["paragraphId"] = paragraph_id
                                merged_assignment["continuesFrom"] = str(
                                    record["blockId"]
                                )
                                merged_assignment["hidden"] = False
                                merged_assignment.setdefault("warnings", []).append(
                                    "URL suffix remains in its source block and "
                                    "continues the annotation-backed URL block."
                                )
                            else:
                                merged_record["block"]["text"] = ""
                                merged_record["assignment"]["hidden"] = True
                                merged_record["assignment"].setdefault(
                                    "warnings", []
                                ).append(
                                    "URL continuation was consolidated into the "
                                    "preceding source block from PDF link geometry."
                                )
                                assignment.setdefault(
                                    "mergedSourceBlockIds", []
                                ).append(str(merged_record["blockId"]))
                            merged_block_ids.append(
                                str(merged_record["blockId"])
                            )
                    links.append(uri)
                    recovered.append(
                        {
                            "blockId": str(record["blockId"]),
                            "pageNumber": str(page_number),
                            "original": str(alignment["printed"]),
                            "uri": uri,
                            "similarity": f"{float(alignment['ratio']):.3f}",
                            "mergedBlockIds": merged_block_ids,
                        }
                    )
                block["text"] = text
                if links:
                    unique_links = list(dict.fromkeys(str(value) for value in links))
                    block["externalLinks"] = unique_links
                    assignment["externalLinks"] = unique_links
    finally:
        pdf.close()
    diagnostics["recoveredExternalLinks"] = recovered
    diagnostics["unrecoveredPrintedUrlLinks"] = unresolved
    return recovered


def _restore_lexical_linebreak_hyphens_from_pdf(
    source: Path,
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> list[dict[str, str]]:
    """Restore compounds whose hyphen is lexical elsewhere in the same PDF."""

    try:
        pdf = fitz.open(source)
    except Exception:
        return []
    canonical_terms: dict[str, str] = {}
    break_candidates: list[dict[str, Any]] = []
    try:
        for page_number in range(1, len(pdf) + 1):
            regions = _pdf_text_regions(pdf[page_number - 1])
            for region in regions:
                lines = region.get("lines", [])
                for line in lines:
                    for term in re.findall(
                        r"(?<![\w-])([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)(?![\w-])",
                        str(line.get("text", "")),
                    ):
                        parts = term.split("-")
                        for left_part, right_part in zip(parts, parts[1:]):
                            pair = f"{left_part}-{right_part}"
                            canonical_terms.setdefault(pair.casefold(), pair)
                for left, right in zip(lines, lines[1:]):
                    left_match = re.search(
                        r"([A-Za-z0-9]+)-\s*$", str(left.get("text", ""))
                    )
                    right_match = re.match(
                        r"\s*([A-Za-z0-9]+)\b", str(right.get("text", ""))
                    )
                    if left_match is not None and right_match is not None:
                        break_candidates.append(
                            {
                                "pageNumber": page_number,
                                "left": left_match.group(1),
                                "right": right_match.group(1),
                                "bboxNormalized": [
                                    min(
                                        float(left["bboxNormalized"][0]),
                                        float(right["bboxNormalized"][0]),
                                    ),
                                    min(
                                        float(left["bboxNormalized"][1]),
                                        float(right["bboxNormalized"][1]),
                                    ),
                                    max(
                                        float(left["bboxNormalized"][2]),
                                        float(right["bboxNormalized"][2]),
                                    ),
                                    max(
                                        float(left["bboxNormalized"][3]),
                                        float(right["bboxNormalized"][3]),
                                    ),
                                ],
                            }
                        )
    finally:
        pdf.close()
    recoverable: list[dict[str, Any]] = []
    for candidate in break_candidates:
        left = str(candidate["left"])
        right = str(candidate["right"])
        canonical = canonical_terms.get(f"{left}-{right}".casefold())
        if canonical is not None:
            recoverable.append(
                {
                    **candidate,
                    "collapsed": left + right,
                    "canonical": canonical,
                }
            )
    diagnostics = structure.setdefault("doclingDiagnostics", {})
    if not recoverable:
        diagnostics["restoredLexicalLinebreakHyphens"] = []
        diagnostics["ambiguousLexicalLinebreakHyphens"] = []
        return []
    assignments = {
        str(assignment["blockId"]): assignment
        for page in structure.get("pages", [])
        for assignment in page.get("blockAssignments", [])
    }
    repaired: list[dict[str, str]] = []
    ambiguous: list[dict[str, str]] = []
    for page in evidence.get("pages", []):
        page_number = int(page["pageNumber"])
        for block in page.get("blocks", []):
            block_id = str(block["blockId"])
            assignment = assignments.get(block_id)
            if assignment is None or assignment.get("hidden"):
                continue
            block_bbox = [
                float(value) for value in block.get("bboxNormalized", [])
            ]
            if len(block_bbox) != 4:
                continue
            text = str(block.get("text", ""))
            grouped: dict[tuple[str, str], int] = {}
            for candidate in recoverable:
                if int(candidate["pageNumber"]) != page_number:
                    continue
                candidate_bbox = [
                    float(value) for value in candidate["bboxNormalized"]
                ]
                candidate_area = max(
                    1e-9,
                    (candidate_bbox[2] - candidate_bbox[0])
                    * (candidate_bbox[3] - candidate_bbox[1]),
                )
                if (
                    _rectangle_intersection_area(block_bbox, candidate_bbox)
                    / candidate_area
                    < 0.5
                ):
                    continue
                key = (str(candidate["collapsed"]), str(candidate["canonical"]))
                grouped[key] = grouped.get(key, 0) + 1
            for (collapsed, canonical), expected_count in sorted(
                grouped.items(), key=lambda value: len(value[0][0]), reverse=True
            ):
                canonical_parts = canonical.split("-", 1)
                spaced_hyphen = (
                    rf"{re.escape(canonical_parts[0])}-\s+"
                    rf"{re.escape(canonical_parts[1])}"
                    if len(canonical_parts) == 2
                    else r"(?!)"
                )
                pattern = re.compile(
                    rf"(?<!\w)(?:{re.escape(collapsed)}|{spaced_hyphen})(?!\w)",
                    re.IGNORECASE,
                )
                actual_count = len(pattern.findall(text))
                if actual_count == 0:
                    continue
                if actual_count != expected_count:
                    ambiguous.append(
                        {
                            "blockId": block_id,
                            "collapsed": collapsed,
                            "canonical": canonical,
                        }
                    )
                    continue

                def restore_case(match: re.Match[str]) -> str:
                    matched = match.group(0)
                    if matched.islower():
                        return canonical.lower()
                    if matched.isupper():
                        return canonical.upper()
                    return canonical

                text = pattern.sub(restore_case, text)
                repaired.append(
                    {
                        "blockId": block_id,
                        "collapsed": collapsed,
                        "canonical": canonical,
                    }
                )
            block["text"] = text
    blocks_by_id = {
        str(block["blockId"]): block
        for page in evidence.get("pages", [])
        for block in page.get("blocks", [])
    }
    assignments_by_id = {
        str(assignment["blockId"]): assignment
        for page in structure.get("pages", [])
        for assignment in page.get("blockAssignments", [])
    }
    for page in structure.get("pages", []):
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value.get("readingOrder", 0)),
        ):
            if assignment.get("hidden") or not assignment.get("continuesFrom"):
                continue
            block_id = str(assignment["blockId"])
            previous_id = str(assignment["continuesFrom"])
            previous_assignment = assignments_by_id.get(previous_id)
            block = blocks_by_id.get(block_id)
            previous_block = blocks_by_id.get(previous_id)
            if (
                previous_assignment is None
                or block is None
                or previous_block is None
                or previous_assignment.get("hidden")
                or assignment.get("paragraphId")
                != previous_assignment.get("paragraphId")
            ):
                continue
            left_text = str(previous_block.get("text", ""))
            right_text = str(block.get("text", ""))
            left_match = re.search(r"([A-Za-z0-9]+)-\s*$", left_text)
            right_match = re.match(r"\s*([A-Za-z0-9]+)\b", right_text)
            if left_match is None or right_match is None:
                continue
            canonical = canonical_terms.get(
                f"{left_match.group(1)}-{right_match.group(1)}".casefold()
            )
            if canonical is None:
                continue
            previous_block["text"] = left_text[: left_match.start()].rstrip()
            block["text"] = (
                canonical + right_text[right_match.end() :]
            )
            repaired.append(
                {
                    "blockId": block_id,
                    "collapsed": (
                        left_match.group(1) + right_match.group(1)
                    ),
                    "canonical": canonical,
                    "boundaryFromBlockId": previous_id,
                }
            )
    diagnostics["restoredLexicalLinebreakHyphens"] = repaired
    diagnostics["ambiguousLexicalLinebreakHyphens"] = ambiguous
    return repaired


def _best_pdf_text_region(
    block: dict[str, Any], regions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    bbox = [float(value) for value in block.get("bboxNormalized", [])]
    if len(bbox) != 4:
        return None
    area = max(1e-9, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    center_x = (bbox[0] + bbox[2]) / 2
    center_y = (bbox[1] + bbox[3]) / 2
    ranked: list[tuple[float, dict[str, Any]]] = []
    for region in regions:
        region_bbox = region["bboxNormalized"]
        overlap = _rectangle_intersection_area(bbox, region_bbox) / area
        center_inside = (
            region_bbox[0] - 0.002 <= center_x <= region_bbox[2] + 0.002
            and region_bbox[1] - 0.002 <= center_y <= region_bbox[3] + 0.002
        )
        if overlap <= 0 and not center_inside:
            continue
        ranked.append((overlap + (0.2 if center_inside else 0.0), region))
    if not ranked:
        return None
    score, region = max(ranked, key=lambda value: value[0])
    return region if score >= 0.12 else None


def _best_pdf_text_line_index(
    block: dict[str, Any], region: dict[str, Any] | None
) -> int | None:
    """Return the source-PDF line that materially overlaps one Docling block."""

    if region is None:
        return None
    bbox = [float(value) for value in block.get("bboxNormalized", [])]
    if len(bbox) != 4:
        return None
    area = max(1e-9, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    center_x = (bbox[0] + bbox[2]) / 2
    center_y = (bbox[1] + bbox[3]) / 2
    ranked: list[tuple[float, int]] = []
    for index, line in enumerate(region.get("lines", [])):
        line_bbox = line["bboxNormalized"]
        overlap = _rectangle_intersection_area(bbox, line_bbox) / area
        center_inside = (
            line_bbox[0] - 0.002 <= center_x <= line_bbox[2] + 0.002
            and line_bbox[1] - 0.002 <= center_y <= line_bbox[3] + 0.002
        )
        if overlap <= 0 and not center_inside:
            continue
        ranked.append((overlap + (0.2 if center_inside else 0.0), index))
    if not ranked:
        return None
    score, index = max(ranked)
    return index if score >= 0.12 else None


def _caption_blocks_for_start(
    start: dict[str, Any],
    next_start: dict[str, Any] | None,
    owner_blocks: list[dict[str, Any]],
    source_regions: dict[str, dict[str, Any] | None],
    block_positions: dict[str, int],
) -> list[dict[str, Any]]:
    """Build one caption from a contiguous source-PDF line chain.

    PyMuPDF may place a short caption and the following body line in one text
    block.  Region identity alone therefore cannot define caption ownership.
    A terminal line which ends well before the region's right edge terminates
    the chain; a terminal line reaching the edge is treated as a wrapped line.
    """

    start_id = str(start["blockId"])
    region = source_regions.get(start_id)
    start_line_index = _best_pdf_text_line_index(start, region)
    if region is None or start_line_index is None:
        return []
    lines = list(region.get("lines", []))
    if not lines:
        return []
    boundary = len(lines)
    if next_start is not None:
        next_id = str(next_start["blockId"])
        if source_regions.get(next_id) is region:
            next_line_index = _best_pdf_text_line_index(next_start, region)
            if next_line_index is not None and next_line_index > start_line_index:
                boundary = next_line_index

    region_bbox = [float(value) for value in region["bboxNormalized"]]
    region_width = max(1e-9, region_bbox[2] - region_bbox[0])
    selected_lines: set[int] = set()
    previous_bbox: list[float] | None = None
    terminal = re.compile(r"[.!?](?:[\"'\u2019\u201d)\]])?\s*$")
    for line_index in range(start_line_index, boundary):
        line = lines[line_index]
        line_bbox = [float(value) for value in line["bboxNormalized"]]
        if previous_bbox is not None:
            vertical_gap = line_bbox[1] - previous_bbox[3]
            line_height = max(
                1e-9,
                min(
                    previous_bbox[3] - previous_bbox[1],
                    line_bbox[3] - line_bbox[1],
                ),
            )
            if vertical_gap > max(0.012, line_height * 1.25):
                break
        selected_lines.add(line_index)
        text = str(line.get("text", "")).strip()
        right_gap = max(0.0, region_bbox[2] - line_bbox[2])
        reaches_wrap_edge = right_gap <= max(0.012, region_width * 0.10)
        if terminal.search(text) and not reaches_wrap_edge:
            break
        previous_bbox = line_bbox

    group = [
        block
        for block in owner_blocks
        if source_regions.get(str(block["blockId"])) is region
        and _best_pdf_text_line_index(block, region) in selected_lines
    ]
    group.sort(key=lambda block: block_positions[str(block["blockId"])])
    return group if any(str(block["blockId"]) == start_id for block in group) else []


def _pdf_line_evidence(
    block: dict[str, Any], region: dict[str, Any] | None
) -> tuple[float, float, float]:
    if region is None:
        return 0.0, 0.0, 0.0
    bbox = [float(value) for value in block["bboxNormalized"]]
    area = max(1e-9, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for line in region["lines"]:
        line_bbox = line["bboxNormalized"]
        overlap = _rectangle_intersection_area(bbox, line_bbox) / area
        if overlap > 0:
            ranked.append((overlap, line))
    if not ranked:
        return 0.0, 0.0, 0.0
    _score, line = max(ranked, key=lambda value: value[0])
    return (
        float(line["boldRatio"]),
        float(line["mathRatio"]),
        float(line["fontSizeMax"]),
    )


def _is_material_internal_figure_caption(
    block: dict[str, Any], visual_bbox: list[float]
) -> bool:
    match = _OBJECT_LABEL.match(str(block.get("text", "")))
    if match is None or _object_label_kind(str(block.get("text", ""))) != "figure":
        return False
    remainder = str(block.get("text", ""))[match.end() :].lstrip()
    if not remainder or remainder[0] not in ":.-–—":
        return False
    bbox = [float(value) for value in block.get("bboxNormalized", [])]
    if len(bbox) != 4 or visual_bbox[3] - visual_bbox[1] < 0.22:
        return False
    horizontal_overlap = max(
        0.0, min(bbox[2], visual_bbox[2]) - max(bbox[0], visual_bbox[0])
    )
    block_width = max(1e-9, bbox[2] - bbox[0])
    return (
        len(str(block.get("text", "")).strip()) >= 12
        and horizontal_overlap / block_width >= 0.7
        and bbox[1] >= visual_bbox[1] + 0.025
        and bbox[3] <= visual_bbox[3] - 0.025
    )


def _rendered_ink_bbox(
    page: fitz.Page,
    bbox_normalized: list[float],
    *,
    raster_budget: PdfRenderBudget | None = None,
) -> list[float] | None:
    """Tighten a triggered crop to visible rendered ink, honoring PDF clips."""

    rect = fitz.Rect(
        bbox_normalized[0] * page.rect.width + page.rect.x0,
        bbox_normalized[1] * page.rect.height + page.rect.y0,
        bbox_normalized[2] * page.rect.width + page.rect.x0,
        bbox_normalized[3] * page.rect.height + page.rect.y0,
    )
    if rect.is_empty:
        return None
    scale = 2.5
    if raster_budget is not None:
        raster_budget.reserve_render(rect.width * scale, rect.height * scale)
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False
    )
    gray = fitz.Pixmap(fitz.csGRAY, pixmap)
    samples = gray.samples
    stride = int(gray.stride)
    first_y: int | None = None
    last_y: int | None = None
    first_x = int(gray.width)
    last_x = -1
    for y in range(int(gray.height)):
        row = samples[y * stride : y * stride + int(gray.width)]
        first_match = re.search(rb"[\x00-\xf9]", row)
        if first_match is None:
            continue
        reverse_match = re.search(rb"[\x00-\xf9]", row[::-1])
        assert reverse_match is not None
        first_y = y if first_y is None else first_y
        last_y = y
        first_x = min(first_x, first_match.start())
        last_x = max(last_x, int(gray.width) - reverse_match.start() - 1)
    if first_y is None or last_y is None or last_x < first_x:
        return None
    padding = max(3, round(scale * 1.5))
    first_x = max(0, first_x - padding)
    first_y = max(0, first_y - padding)
    last_x = min(int(gray.width) - 1, last_x + padding)
    last_y = min(int(gray.height) - 1, last_y + padding)
    width = max(1, int(gray.width))
    height = max(1, int(gray.height))
    tightened = [
        bbox_normalized[0]
        + (bbox_normalized[2] - bbox_normalized[0]) * first_x / width,
        bbox_normalized[1]
        + (bbox_normalized[3] - bbox_normalized[1]) * first_y / height,
        bbox_normalized[0]
        + (bbox_normalized[2] - bbox_normalized[0]) * (last_x + 1) / width,
        bbox_normalized[1]
        + (bbox_normalized[3] - bbox_normalized[1]) * (last_y + 1) / height,
    ]
    if (tightened[2] - tightened[0]) * (tightened[3] - tightened[1]) < 0.0005:
        return None
    return [round(max(0.0, min(1.0, value)), 5) for value in tightened]


def _rendered_bbox_has_ink(
    page: fitz.Page,
    bbox_normalized: list[float],
    *,
    raster_budget: PdfRenderBudget | None = None,
) -> bool:
    """Return whether an exact source rectangle contains any visible ink.

    This deliberately does not reuse ``_rendered_ink_bbox``: that crop helper
    rejects very small tightened regions, while a short but real heading (for
    example a figure panel label) can legitimately occupy such a region.
    """

    rect = fitz.Rect(
        bbox_normalized[0] * page.rect.width + page.rect.x0,
        bbox_normalized[1] * page.rect.height + page.rect.y0,
        bbox_normalized[2] * page.rect.width + page.rect.x0,
        bbox_normalized[3] * page.rect.height + page.rect.y0,
    )
    if rect.is_empty:
        return False
    if raster_budget is not None:
        raster_budget.reserve_render(rect.width * 2.5, rect.height * 2.5)
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(2.5, 2.5), clip=rect, alpha=False
    )
    gray = fitz.Pixmap(fitz.csGRAY, pixmap)
    samples = gray.samples
    stride = int(gray.stride)
    width = int(gray.width)
    return any(
        re.search(rb"[\x00-\xf9]", samples[y * stride : y * stride + width])
        is not None
        for y in range(int(gray.height))
    )


def _suppress_blank_docling_headings(
    source: Path,
    evidence: dict[str, Any],
    structure: dict[str, Any],
    *,
    raster_budget: PdfRenderBudget | None = None,
) -> None:
    """Hide repeated figure metadata that Docling exposed as section headings.

    Some tagged PDFs contain invisible accessibility metadata immediately before
    a figure.  Docling can label that metadata as ``section_header`` even though
    its exact source rectangle renders completely white.  Silently dropping an
    arbitrary blank heading would be risky because its provenance could merely
    be wrong, so automatic suppression requires three independent signals:

    * the heading is unnumbered and not on page one;
    * a same-page figure is anchored directly after it; and
    * the same blank heading text occurs on another page.

    Any other visible semantic heading whose source rectangle is blank remains
    in the document and is reported as an unresolved diagnostic so publication
    QA fails closed.
    """

    diagnostics = structure.setdefault("doclingDiagnostics", {})
    diagnostics.setdefault("suppressedBlankHeadingBlockIds", [])
    diagnostics.setdefault("blankVisibleHeadingBlockIds", [])
    try:
        pdf = fitz.open(source)
    except Exception:
        return

    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    structure_pages = {
        int(page["pageNumber"]): page for page in structure.get("pages", [])
    }
    blocks = {
        str(block["blockId"]): block
        for page in evidence_pages.values()
        for block in page.get("blocks", [])
    }
    ordered_assignments = [
        assignment
        for page in sorted(
            structure.get("pages", []), key=lambda value: int(value["pageNumber"])
        )
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value["readingOrder"]),
        )
    ]
    assignment_positions = {
        str(assignment["blockId"]): index
        for index, assignment in enumerate(ordered_assignments)
    }

    blank_candidates: list[dict[str, Any]] = []
    try:
        for page_number, page_structure in structure_pages.items():
            if not 1 <= page_number <= len(pdf):
                continue
            pdf_page = pdf[page_number - 1]
            for assignment in page_structure.get("blockAssignments", []):
                if assignment.get("role") != "heading" or assignment.get("hidden"):
                    continue
                block_id = str(assignment["blockId"])
                block = blocks.get(block_id)
                if block is None:
                    continue
                bbox = [float(value) for value in block.get("bboxNormalized", [])]
                if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    continue
                if _rendered_bbox_has_ink(
                    pdf_page, bbox, raster_budget=raster_budget
                ):
                    continue
                text = re.sub(r"\s+", " ", str(block.get("text", ""))).strip()
                anchored_visuals = [
                    visual
                    for visual in page_structure.get("visualObjects", [])
                    if visual.get("kind") == "figure"
                    and str(visual.get("insertAfterBlockId") or "") == block_id
                ]
                blank_candidates.append(
                    {
                        "assignment": assignment,
                        "block": block,
                        "blockId": block_id,
                        "pageNumber": page_number,
                        "textKey": text.casefold(),
                        "numbered": bool(
                            _NUMBERED_HEADING.match(text)
                            or _APPENDIX_HEADING.match(text)
                        ),
                        "anchoredVisuals": anchored_visuals,
                    }
                )
    finally:
        pdf.close()

    repeated_text_keys = {
        candidate["textKey"]
        for candidate in blank_candidates
        if candidate["textKey"]
        and len(
            {
                int(other["pageNumber"])
                for other in blank_candidates
                if other["textKey"] == candidate["textKey"]
            }
        )
        >= 2
    }
    suppressible = [
        candidate
        for candidate in blank_candidates
        if int(candidate["pageNumber"]) > 1
        and not candidate["numbered"]
        and candidate["anchoredVisuals"]
        and candidate["textKey"] in repeated_text_keys
    ]
    suppressible_ids = {
        str(candidate["blockId"]) for candidate in suppressible
    }
    unresolved_ids = sorted(
        str(candidate["blockId"])
        for candidate in blank_candidates
        if str(candidate["blockId"]) not in suppressible_ids
    )

    semantic_anchor_roles = {
        "title",
        "author",
        "affiliation",
        "abstract",
        "heading",
        "paragraph",
        "list_item",
        "reference",
        "footnote",
    }
    fallback_by_block_id: dict[str, tuple[str | None, str | None]] = {}
    for candidate in suppressible:
        block_id = str(candidate["blockId"])
        position = assignment_positions.get(block_id, 0)
        fallback_assignment = next(
            (
                previous
                for previous in reversed(ordered_assignments[:position])
                if not previous.get("hidden")
                and str(previous.get("blockId") or "") not in suppressible_ids
                and str(previous.get("role") or "") in semantic_anchor_roles
            ),
            None,
        )
        fallback_by_block_id[block_id] = (
            str(fallback_assignment["blockId"])
            if fallback_assignment is not None
            else None,
            str(fallback_assignment["sectionId"])
            if fallback_assignment is not None
            and fallback_assignment.get("sectionId")
            else None,
        )

    sections = list(structure.get("sections", []))
    removed_sections = {
        str(section["sectionId"]): section
        for section in sections
        if str(section.get("titleBlockId")) in suppressible_ids
    }
    fallback_section_by_removed_id = {
        section_id: fallback_by_block_id.get(
            str(section.get("titleBlockId") or ""), (None, None)
        )[1]
        for section_id, section in removed_sections.items()
    }

    def surviving_parent(section_id: str | None) -> str | None:
        seen: set[str] = set()
        while section_id and section_id in removed_sections and section_id not in seen:
            seen.add(section_id)
            parent = removed_sections[section_id].get("parentSectionId")
            section_id = (
                str(parent)
                if parent
                else fallback_section_by_removed_id.get(section_id)
            )
        return section_id

    for assignment in ordered_assignments:
        section_id = assignment.get("sectionId")
        if section_id and str(section_id) in removed_sections:
            assignment["sectionId"] = surviving_parent(str(section_id))
    for section in sections:
        parent = section.get("parentSectionId")
        if parent and str(parent) in removed_sections:
            section["parentSectionId"] = surviving_parent(str(parent))
    structure["sections"] = [
        section
        for section in sections
        if str(section["sectionId"]) not in removed_sections
    ]

    for candidate in suppressible:
        block_id = str(candidate["blockId"])
        block = candidate["block"]
        assignment = candidate["assignment"]
        owner_ids = sorted(
            str(visual["objectId"])
            for visual in candidate["anchoredVisuals"]
            if visual.get("objectId")
        )
        block["embeddedVisualOwnerRefs"] = owner_ids
        block["suppressedVisualText"] = True
        block["suppressedBlankSourceHeading"] = True
        block["visualCaptionCandidate"] = False
        block["associatedVisualCaption"] = False
        assignment["role"] = "noise"
        assignment["sectionId"] = None
        assignment["paragraphId"] = None
        assignment["continuesFrom"] = None
        assignment["hidden"] = True
        assignment["suppressedVisualText"] = True
        assignment["suppressedBlankSourceHeading"] = True
        assignment["visualCaptionCandidate"] = False
        assignment["associatedVisualCaption"] = False
        warning = (
            "Repeated invisible figure metadata mislabeled as a section heading "
            "was suppressed after its exact source rectangle rendered blank."
        )
        if warning not in assignment.setdefault("warnings", []):
            assignment["warnings"].append(warning)

        fallback_anchor = fallback_by_block_id.get(block_id, (None, None))[0]
        for visual in candidate["anchoredVisuals"]:
            if fallback_anchor is None:
                visual.pop("insertAfterBlockId", None)
            else:
                visual["insertAfterBlockId"] = fallback_anchor

    diagnostics["suppressedBlankHeadingBlockIds"] = sorted(suppressible_ids)
    diagnostics["blankVisibleHeadingBlockIds"] = unresolved_ids
    if unresolved_ids:
        warning = (
            "A visible Docling section heading has a blank source-PDF rectangle; "
            "publication QA must fail closed."
        )
        if warning not in structure.setdefault("warnings", []):
            structure["warnings"].append(warning)


def _demote_mislabeled_docling_headings(
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> list[str]:
    """Return unmistakable inline labels that Docling promoted into the TOC."""

    blocks = {
        str(block["blockId"]): block
        for page in evidence.get("pages", [])
        for block in page.get("blocks", [])
    }
    assignments = [
        assignment
        for page in sorted(
            structure.get("pages", []), key=lambda value: int(value["pageNumber"])
        )
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value["readingOrder"]),
        )
    ]
    assignment_positions = {
        str(assignment["blockId"]): index
        for index, assignment in enumerate(assignments)
    }
    sections = list(structure.get("sections", []))
    section_by_title = {
        str(section.get("titleBlockId")): section for section in sections
    }
    demotions: dict[str, tuple[str, str]] = {}
    plate_label = re.compile(
        r"^(?:nearest\s+neighbors|random\s+(?:class\s+conditional\s+)?samples?\b|"
        r"layout-to-image\s+synthesis\b|semantic\s+synthesis\b|"
        r"text-to-image\s+synthesis\b)",
        re.IGNORECASE,
    )
    visual_owners_by_block: dict[str, list[str]] = {}
    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    for page in structure.get("pages", []):
        page_number = int(page["pageNumber"])
        page_blocks = {
            str(block["blockId"]): block
            for block in evidence_pages.get(page_number, {}).get("blocks", [])
        }
        for assignment in page.get("blockAssignments", []):
            if assignment.get("role") != "heading" or assignment.get("hidden"):
                continue
            block_id = str(assignment["blockId"])
            block_bbox = [
                float(value)
                for value in page_blocks.get(block_id, {}).get("bboxNormalized", [])
            ]
            if len(block_bbox) != 4:
                continue
            area = max(
                1e-9,
                (block_bbox[2] - block_bbox[0])
                * (block_bbox[3] - block_bbox[1]),
            )
            owners = [
                str(visual["objectId"])
                for visual in page.get("visualObjects", [])
                if visual.get("objectId")
                and _rectangle_intersection_area(
                    block_bbox,
                    [float(value) for value in visual.get("bboxNormalized", [])],
                )
                / area
                >= 0.5
            ]
            if owners:
                visual_owners_by_block[block_id] = owners
    for assignment in assignments:
        block_id = str(assignment.get("blockId"))
        block = blocks.get(block_id)
        section = section_by_title.get(block_id)
        if (
            block is None
            or section is None
            or assignment.get("hidden")
            or assignment.get("role") != "heading"
        ):
            continue
        text = re.sub(r"\s+", " ", str(block.get("text", ""))).strip()
        if not text or _NUMBERED_HEADING.match(text) or _APPENDIX_HEADING.match(text):
            continue
        normalized = text.rstrip(":").casefold()
        if normalized in {
            "abstract",
            "acknowledgment",
            "acknowledgments",
            "acknowledgement",
            "acknowledgements",
            "bibliography",
            "conclusion",
            "conclusions",
            "introduction",
            "references",
        }:
            continue
        level = int(section.get("level", 1))
        role: str | None = None
        reason = ""
        if re.match(r"^\s*\[\d+\]\s+\S", text):
            role = "reference"
            reason = "reference entry"
        elif re.match(r"^\s*[•·▪◦‣]\s*\S", text):
            role = "list_item"
            reason = "bullet item"
        elif level >= 4 and re.fullmatch(
            r"\s*\[(?!\d+\]$)[^\]]+\]\s*", text
        ):
            role = "paragraph"
            reason = "bracketed inline label"
        elif (
            level >= 4
            and
            len(text) >= 2
            and text[0] in "'\"‘“"
            and text[-1] in "'\"’”"
        ):
            role = "paragraph"
            reason = "quoted prompt"
        elif level >= 4 and plate_label.match(text):
            role = "paragraph"
            reason = "qualitative plate label"
        if role is not None:
            if role == "paragraph" and block_id in visual_owners_by_block:
                role = "noise"
                reason = "figure-internal label"
            demotions[block_id] = (role, reason)
    if not demotions:
        structure.setdefault("doclingDiagnostics", {}).setdefault(
            "demotedMislabeledHeadingBlockIds", []
        )
        return []

    removed_sections = {
        str(section["sectionId"]): section
        for section in sections
        if str(section.get("titleBlockId")) in demotions
    }
    removed_ids = set(removed_sections)
    fallback_by_section: dict[str, str | None] = {}
    for section_id, section in removed_sections.items():
        parent = section.get("parentSectionId")
        fallback = str(parent) if parent else None
        if fallback is None or fallback in removed_ids:
            title_id = str(section.get("titleBlockId"))
            position = assignment_positions.get(title_id, 0)
            previous = next(
                (
                    value
                    for value in reversed(assignments[:position])
                    if not value.get("hidden")
                    and value.get("sectionId")
                    and str(value.get("sectionId")) not in removed_ids
                ),
                None,
            )
            fallback = (
                str(previous["sectionId"]) if previous is not None else None
            )
        fallback_by_section[section_id] = fallback

    def surviving_parent(section_id: str | None) -> str | None:
        seen: set[str] = set()
        while section_id and section_id in removed_sections and section_id not in seen:
            seen.add(section_id)
            parent = removed_sections[section_id].get("parentSectionId")
            section_id = (
                str(parent) if parent else fallback_by_section.get(section_id)
            )
        return section_id

    for assignment in assignments:
        section_id = assignment.get("sectionId")
        if section_id and str(section_id) in removed_sections:
            assignment["sectionId"] = surviving_parent(str(section_id))
    for section in sections:
        parent = section.get("parentSectionId")
        if parent and str(parent) in removed_sections:
            section["parentSectionId"] = surviving_parent(str(parent))
    structure["sections"] = [
        section
        for section in sections
        if str(section["sectionId"]) not in removed_sections
    ]

    for block_id, (role, reason) in demotions.items():
        assignment = next(
            value for value in assignments if str(value.get("blockId")) == block_id
        )
        block = blocks[block_id]
        assignment["role"] = role
        assignment["paragraphId"] = None if role == "noise" else (
            f"reference-{block.get('sourceBlockId', block_id)}"
            if role == "reference"
            else f"para-{block_id}"
        )
        assignment["continuesFrom"] = None
        if role == "noise":
            owners = visual_owners_by_block[block_id]
            assignment["hidden"] = True
            assignment["suppressedVisualText"] = True
            block["embeddedVisualOwnerRefs"] = owners
            block["suppressedVisualText"] = True
        assignment["referenceLabel"] = (
            _reference_label(str(block.get("text", "")))
            if role == "reference"
            else None
        )
        assignment.setdefault("warnings", []).append(
            f"A Docling section header was restored as a {reason}."
        )

    demoted_ids = sorted(demotions)
    structure.setdefault("doclingDiagnostics", {})[
        "demotedMislabeledHeadingBlockIds"
    ] = demoted_ids
    return demoted_ids


def _repair_lowercase_body_continuations(
    evidence: dict[str, Any],
    structure: dict[str, Any],
    pdf_lines_by_page: dict[int, list[dict[str, Any]]] | None = None,
) -> list[str]:
    """Join a lowercase tail only across an incomplete same-section paragraph."""

    block_entries = {
        str(block["blockId"]): (int(page["pageNumber"]), block)
        for page in evidence.get("pages", [])
        for block in page.get("blocks", [])
    }
    ordered = [
        (int(page["pageNumber"]), assignment)
        for page in sorted(
            structure.get("pages", []), key=lambda value: int(value["pageNumber"])
        )
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value["readingOrder"]),
        )
    ]
    repaired: list[str] = []
    body_roles = {"abstract", "paragraph"}
    terminal = re.compile(r"[.!?;:](?:['\"’”\])}]*)\s*$")
    for position, (page_number, assignment) in enumerate(ordered):
        block_id = str(assignment.get("blockId"))
        block_entry = block_entries.get(block_id)
        if (
            block_entry is None
            or assignment.get("hidden")
            or assignment.get("role") not in body_roles
            or not assignment.get("sectionId")
            or any(
                str(warning).startswith(
                    "A Docling section header was restored as a "
                )
                for warning in assignment.get("warnings", [])
            )
        ):
            continue
        text = str(block_entry[1].get("text", "")).lstrip()
        if not text[:1].islower() and text[:1] not in ",.;)]}":
            continue
        previous_entry = next(
            (
                (previous_page, previous)
                for previous_page, previous in reversed(ordered[:position])
                if not previous.get("hidden")
                and previous.get("role") == assignment.get("role")
                and previous.get("sectionId") == assignment.get("sectionId")
                and previous.get("paragraphId")
                and str(previous.get("blockId")) in block_entries
            ),
            None,
        )
        if previous_entry is None:
            continue
        previous_page, previous = previous_entry
        previous_id = str(previous["blockId"])
        previous_block = block_entries[previous_id][1]
        previous_text = str(previous_block.get("text", "")).rstrip()
        source_block_id = str(block_entry[1].get("sourceBlockId") or "")
        previous_source_block_id = str(previous_block.get("sourceBlockId") or "")
        if (
            source_block_id
            and source_block_id == previous_source_block_id
            and assignment.get("paragraphId") == previous.get("paragraphId")
            and assignment.get("continuesFrom") == previous_id
        ):
            # Character-span partitioning has already linked exact segments of
            # one Docling item.  A single PDF line can overlap only part of
            # such a segment, so treating that line as a replacement would
            # silently discard the segment's remaining characters.
            continue
        if terminal.search(previous_text) or page_number - previous_page not in {0, 1}:
            continue
        previous_bbox = [
            float(value) for value in previous_block.get("bboxNormalized", [])
        ]
        block_bbox = [float(value) for value in block_entry[1].get("bboxNormalized", [])]
        if len(previous_bbox) != 4 or len(block_bbox) != 4:
            continue
        if page_number == previous_page:
            same_column = abs(previous_bbox[0] - block_bbox[0]) <= 0.04
            touches_previous = block_bbox[1] <= previous_bbox[3] + 0.025
            if not same_column or not touches_previous:
                continue
        elif previous_bbox[3] < 0.72:
            continue

        candidate_block = block_entry[1]
        original_candidate_text = str(candidate_block.get("text", ""))
        original_previous_text = str(previous_block.get("text", ""))
        original_paragraph_id = assignment.get("paragraphId")
        original_continues_from = assignment.get("continuesFrom")
        lines = (pdf_lines_by_page or {}).get(page_number, [])
        exact_line = None
        area = max(
            1e-9,
            (block_bbox[2] - block_bbox[0]) * (block_bbox[3] - block_bbox[1]),
        )
        ranked_lines = sorted(
            (
                (
                    _rectangle_intersection_area(
                        block_bbox,
                        [float(value) for value in line.get("bboxNormalized", [])],
                    )
                    / area,
                    line,
                )
                for line in lines
                if len(line.get("bboxNormalized", [])) == 4
                and str(line.get("text", "")).strip()
            ),
            key=lambda value: value[0],
            reverse=True,
        )
        if ranked_lines and ranked_lines[0][0] >= 0.5:
            best_line = ranked_lines[0][1]
            line_bbox = [
                float(value) for value in best_line.get("bboxNormalized", [])
            ]
            block_width = max(1e-9, block_bbox[2] - block_bbox[0])
            horizontal_tolerance = max(0.012, block_width * 0.05)
            horizontally_equivalent = (
                len(line_bbox) == 4
                and abs(line_bbox[0] - block_bbox[0]) <= horizontal_tolerance
                and abs(line_bbox[2] - block_bbox[2]) <= horizontal_tolerance
                and (line_bbox[2] - line_bbox[0]) <= block_width * 1.12
            )
            if horizontally_equivalent:
                line_text = re.sub(
                    r"\s+", " ", str(best_line.get("text", ""))
                ).strip()
                normalized_tail = re.sub(r"\s+", " ", text)
                if SequenceMatcher(None, normalized_tail, line_text).ratio() >= 0.82:
                    exact_line = line_text
        if exact_line is not None:
            candidate_block["text"] = exact_line
            assignment["citations"] = _citations(exact_line)
            assignment["objectReferences"] = _object_references(exact_line)
            trailing_token = re.search(r"\b([A-Za-z0-9])\b\s*$", previous_text)
            if (
                trailing_token is not None
                and re.search(
                    rf"\b{re.escape(trailing_token.group(1))}\b", exact_line
                )
                and not re.search(
                    rf"\b{re.escape(trailing_token.group(1))}\b", text
                )
            ):
                previous_block["text"] = previous_text[: trailing_token.start()].rstrip()
                previous["citations"] = _citations(previous_block["text"])
                previous["objectReferences"] = _object_references(
                    previous_block["text"]
                )
        assignment["paragraphId"] = previous["paragraphId"]
        assignment["continuesFrom"] = previous_id
        linkage_changed = (
            original_paragraph_id != assignment.get("paragraphId")
            or original_continues_from != assignment.get("continuesFrom")
        )
        text_changed = (
            original_candidate_text != str(candidate_block.get("text", ""))
            or original_previous_text != str(previous_block.get("text", ""))
        )
        if not linkage_changed and not text_changed:
            continue
        warnings = assignment.setdefault("warnings", [])
        if linkage_changed:
            warning = (
                "A lowercase body tail separated by float traversal was "
                "rejoined to its incomplete source paragraph."
            )
            if warning not in warnings:
                warnings.append(warning)
        if text_changed:
            warning = (
                "A lowercase body tail was restored from a horizontally "
                "matching source-PDF line."
            )
            if warning not in warnings:
                warnings.append(warning)
        repaired.append(block_id)
    structure.setdefault("doclingDiagnostics", {})[
        "rejoinedLowercaseBodyBlockIds"
    ] = sorted(repaired)
    return sorted(repaired)


def _repair_lowercase_body_continuations_from_pdf(
    source: Path,
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> list[str]:
    try:
        with fitz.open(source) as pdf:
            lines_by_page = {
                page_number: [
                    line
                    for region in _pdf_text_regions(pdf[page_number - 1])
                    for line in region.get("lines", [])
                ]
                for page_number in range(1, len(pdf) + 1)
            }
    except (OSError, RuntimeError, ValueError):
        lines_by_page = {}
    return _repair_lowercase_body_continuations(
        evidence, structure, lines_by_page
    )


def _suppress_overlapping_equation_fragments(
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> list[str]:
    """Remove symbol-only overlays duplicated by a numbered equation stack."""

    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    suppressed: list[str] = []
    for page in structure.get("pages", []):
        page_number = int(page["pageNumber"])
        blocks = {
            str(block["blockId"]): block
            for block in evidence_pages.get(page_number, {}).get("blocks", [])
        }
        assignments = {
            str(assignment["blockId"]): assignment
            for assignment in page.get("blockAssignments", [])
        }
        equations = [
            visual
            for visual in page.get("visualObjects", [])
            if visual.get("kind") == "equation"
            and len(visual.get("bboxNormalized", [])) == 4
        ]
        duplicate_ids: set[str] = set()
        for candidate in equations:
            source_id = f"dl-{str(candidate.get('objectId', '')).removeprefix('equation-')}"
            source_block = blocks.get(source_id)
            if source_block is None:
                continue
            source_text = str(source_block.get("text", ""))
            if (
                len(re.findall(r"[A-Za-z0-9]", source_text)) > 3
                or re.search(
                    r"\(\s*\d+[A-Za-z]?\s*\)\s*$", source_text
                )
                is not None
            ):
                continue
            candidate_bbox = [
                float(value) for value in candidate["bboxNormalized"]
            ]
            overlaps = 0
            for other in equations:
                if other is candidate:
                    continue
                other_source_id = (
                    f"dl-{str(other.get('objectId', '')).removeprefix('equation-')}"
                )
                other_text = str(blocks.get(other_source_id, {}).get("text", ""))
                if re.search(r"\(\s*\d+[A-Za-z]?\s*\)\s*$", other_text) is None:
                    continue
                other_bbox = [float(value) for value in other["bboxNormalized"]]
                horizontal_overlap = max(
                    0.0,
                    min(candidate_bbox[2], other_bbox[2])
                    - max(candidate_bbox[0], other_bbox[0]),
                )
                vertical_overlap = max(
                    0.0,
                    min(candidate_bbox[3], other_bbox[3])
                    - max(candidate_bbox[1], other_bbox[1]),
                )
                if (
                    horizontal_overlap
                    / max(
                        1e-9,
                        min(
                            candidate_bbox[2] - candidate_bbox[0],
                            other_bbox[2] - other_bbox[0],
                        ),
                    )
                    >= 0.65
                    and vertical_overlap
                    / max(1e-9, other_bbox[3] - other_bbox[1])
                    >= 0.55
                ):
                    overlaps += 1
            if overlaps >= 2:
                duplicate_ids.add(str(candidate["objectId"]))
                assignment = assignments.get(source_id)
                if assignment is not None:
                    assignment["role"] = "noise"
                    assignment["hidden"] = True
                    assignment["paragraphId"] = None
                    assignment["continuesFrom"] = None
                    assignment["suppressedVisualText"] = True
                    warning = (
                        "A symbol-only equation overlay duplicated a numbered "
                        "equation stack and was suppressed."
                    )
                    warnings = assignment.setdefault("warnings", [])
                    if warning not in warnings:
                        warnings.append(warning)
                source_block["suppressedVisualText"] = True
                suppressed.append(str(candidate["objectId"]))
        if duplicate_ids:
            page["visualObjects"] = [
                visual
                for visual in page.get("visualObjects", [])
                if str(visual.get("objectId")) not in duplicate_ids
            ]
    structure.setdefault("doclingDiagnostics", {})[
        "suppressedOverlappingEquationObjectIds"
    ] = sorted(suppressed)
    return sorted(suppressed)


def _absorb_adjacent_equation_signs(
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> list[str]:
    """Expand an equation crop to include its immediately preceding sign."""

    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    absorbed: list[str] = []
    for page in structure.get("pages", []):
        page_number = int(page["pageNumber"])
        blocks = {
            str(block["blockId"]): block
            for block in evidence_pages.get(page_number, {}).get("blocks", [])
        }
        assignments = sorted(
            page.get("blockAssignments", []),
            key=lambda assignment: int(assignment.get("readingOrder", 0)),
        )
        assignment_positions = {
            str(assignment["blockId"]): index
            for index, assignment in enumerate(assignments)
        }
        assignment_by_id = {
            str(assignment["blockId"]): assignment for assignment in assignments
        }
        for visual in page.get("visualObjects", []):
            if visual.get("kind") != "equation":
                continue
            source_id = (
                f"dl-{str(visual.get('objectId', '')).removeprefix('equation-')}"
            )
            source_position = assignment_positions.get(source_id)
            if source_position is None or source_position == 0:
                continue
            sign_assignment = assignments[source_position - 1]
            sign_id = str(sign_assignment["blockId"])
            sign_block = blocks.get(sign_id)
            if (
                sign_block is None
                or sign_assignment.get("hidden")
                or str(sign_block.get("text", "")).strip() not in {"-", "−", "+", "="}
                or str(visual.get("insertAfterBlockId") or "") != sign_id
            ):
                continue
            sign_bbox = [
                float(value) for value in sign_block.get("bboxNormalized", [])
            ]
            visual_bbox = [
                float(value) for value in visual.get("bboxNormalized", [])
            ]
            if len(sign_bbox) != 4 or len(visual_bbox) != 4:
                continue
            vertical_overlap = max(
                0.0,
                min(sign_bbox[3], visual_bbox[3])
                - max(sign_bbox[1], visual_bbox[1]),
            )
            horizontal_gap = visual_bbox[0] - sign_bbox[2]
            if (
                vertical_overlap / max(1e-9, sign_bbox[3] - sign_bbox[1]) < 0.55
                or not -0.003 <= horizontal_gap <= 0.025
            ):
                continue
            visual["bboxNormalized"] = [
                min(sign_bbox[0], visual_bbox[0]),
                min(sign_bbox[1], visual_bbox[1]),
                max(sign_bbox[2], visual_bbox[2]),
                max(sign_bbox[3], visual_bbox[3]),
            ]
            fallback = next(
                (
                    assignment
                    for assignment in reversed(assignments[: source_position - 1])
                    if not assignment.get("hidden")
                    and assignment.get("role")
                    in {"abstract", "equation", "footnote", "heading", "list_item", "paragraph"}
                ),
                None,
            )
            if fallback is not None:
                visual["insertAfterBlockId"] = str(fallback["blockId"])
            sign_assignment["role"] = "noise"
            sign_assignment["hidden"] = True
            sign_assignment["paragraphId"] = None
            sign_assignment["continuesFrom"] = None
            sign_assignment["suppressedVisualText"] = True
            sign_block["suppressedVisualText"] = True
            sign_block["embeddedVisualOwnerRefs"] = [str(visual["objectId"])]
            warning = (
                "An immediately preceding standalone sign was folded into its "
                "source-equation crop."
            )
            warnings = visual.setdefault("warnings", [])
            if warning not in warnings:
                warnings.append(warning)
            absorbed.append(sign_id)
    structure.setdefault("doclingDiagnostics", {})[
        "absorbedEquationSignBlockIds"
    ] = sorted(absorbed)
    return sorted(absorbed)


def _reorder_displaced_equation_prose_by_geometry(
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> list[str]:
    """Move narrow left-hand equation prefaces back above their equations."""

    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    moved: list[str] = []
    for page in structure.get("pages", []):
        page_number = int(page["pageNumber"])
        blocks = {
            str(block["blockId"]): block
            for block in evidence_pages.get(page_number, {}).get("blocks", [])
        }
        assignments = sorted(
            page.get("blockAssignments", []),
            key=lambda assignment: int(assignment.get("readingOrder", 0)),
        )
        equation_ids = {
            str(assignment["blockId"])
            for assignment in assignments
            if not assignment.get("hidden") and assignment.get("role") == "equation"
        }
        candidates = sorted(
            (
                assignment
                for assignment in assignments
                if not assignment.get("hidden")
                and assignment.get("role") == "paragraph"
                and assignment.get("sectionId")
                and len(blocks.get(str(assignment["blockId"]), {}).get("bboxNormalized", []))
                == 4
            ),
            key=lambda assignment: float(
                blocks[str(assignment["blockId"])]["bboxNormalized"][1]
            ),
        )
        for candidate in candidates:
            candidate_id = str(candidate["blockId"])
            candidate_bbox = [
                float(value)
                for value in blocks[candidate_id].get("bboxNormalized", [])
            ]
            if candidate_bbox[2] - candidate_bbox[0] > 0.5:
                continue
            positions = {
                str(assignment["blockId"]): index
                for index, assignment in enumerate(assignments)
            }
            candidate_position = positions[candidate_id]
            targets: list[tuple[float, str]] = []
            for equation_id in equation_ids:
                equation_position = positions.get(equation_id)
                equation = next(
                    (
                        assignment
                        for assignment in assignments
                        if str(assignment["blockId"]) == equation_id
                    ),
                    None,
                )
                equation_bbox = [
                    float(value)
                    for value in blocks.get(equation_id, {}).get("bboxNormalized", [])
                ]
                if (
                    equation_position is None
                    or equation is None
                    or equation_position >= candidate_position
                    or equation.get("sectionId") != candidate.get("sectionId")
                    or len(equation_bbox) != 4
                ):
                    continue
                vertical_gap = equation_bbox[1] - candidate_bbox[3]
                horizontal_gap = equation_bbox[0] - candidate_bbox[2]
                if (
                    -0.002 <= vertical_gap <= 0.06
                    and -0.02 <= horizontal_gap <= 0.1
                ):
                    targets.append((equation_bbox[1], equation_id))
            if not targets:
                continue
            target_id = min(targets)[1]
            assignments.remove(candidate)
            target_position = next(
                index
                for index, assignment in enumerate(assignments)
                if str(assignment["blockId"]) == target_id
            )
            assignments.insert(target_position, candidate)
            if candidate.get("continuesFrom"):
                candidate["paragraphId"] = f"para-{candidate_id}"
                candidate["continuesFrom"] = None
            warning = (
                "A narrow equation preface displaced by Docling reading order "
                "was restored from source-page geometry."
            )
            warnings = candidate.setdefault("warnings", [])
            if warning not in warnings:
                warnings.append(warning)
            moved.append(candidate_id)
        for reading_order, assignment in enumerate(assignments, start=1):
            assignment["readingOrder"] = reading_order
        page["blockAssignments"] = assignments
    structure.setdefault("doclingDiagnostics", {})[
        "geometryReorderedEquationProseBlockIds"
    ] = sorted(set(moved))
    return sorted(set(moved))


def _reanchor_equations_by_geometry(
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> list[str]:
    """Anchor equations after the closest source block above them, then split prose."""

    _reorder_displaced_equation_prose_by_geometry(evidence, structure)
    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    structure_pages = {
        int(page["pageNumber"]): page for page in structure.get("pages", [])
    }
    changed: list[str] = []
    anchor_roles = {
        "abstract",
        "equation",
        "footnote",
        "heading",
        "list_item",
        "paragraph",
    }
    for page_number, page in structure_pages.items():
        blocks = {
            str(block["blockId"]): block
            for block in evidence_pages.get(page_number, {}).get("blocks", [])
        }
        assignments = {
            str(assignment["blockId"]): assignment
            for assignment in page.get("blockAssignments", [])
        }
        for visual in page.get("visualObjects", []):
            if visual.get("kind") != "equation":
                continue
            visual_bbox = [
                float(value) for value in visual.get("bboxNormalized", [])
            ]
            if len(visual_bbox) != 4:
                continue
            visual_width = max(1e-9, visual_bbox[2] - visual_bbox[0])
            source_suffix = str(visual.get("objectId", "")).removeprefix(
                "equation-"
            )
            source_block_id = f"dl-{source_suffix}"
            existing_anchor_id = str(visual.get("insertAfterBlockId") or "")
            candidates: list[tuple[float, float, int, str]] = []
            for block_id, assignment in assignments.items():
                block = blocks.get(block_id)
                if (
                    block is None
                    or block_id == source_block_id
                    or assignment.get("hidden")
                    or assignment.get("role") not in anchor_roles
                ):
                    continue
                bbox = [float(value) for value in block.get("bboxNormalized", [])]
                if (
                    len(bbox) != 4
                    or bbox[1] > visual_bbox[1] + 0.002
                    or bbox[3] > visual_bbox[1] + 0.016
                ):
                    continue
                horizontal_overlap = max(
                    0.0, min(bbox[2], visual_bbox[2]) - max(bbox[0], visual_bbox[0])
                )
                minimum_width = max(
                    1e-9, min(bbox[2] - bbox[0], visual_width)
                )
                left_preface = (
                    -0.02 <= visual_bbox[0] - bbox[2] <= 0.1
                    and -0.002 <= visual_bbox[1] - bbox[3] <= 0.06
                )
                if (
                    horizontal_overlap / minimum_width < 0.15
                    and block_id != existing_anchor_id
                    and not left_preface
                ):
                    continue
                candidates.append(
                    (
                        bbox[1],
                        bbox[3],
                        int(assignment.get("readingOrder", 0)),
                        block_id,
                    )
                )
            if not candidates:
                continue
            anchor_id = max(candidates)[3]
            if str(visual.get("insertAfterBlockId") or "") != anchor_id:
                visual["insertAfterBlockId"] = anchor_id
                visual.setdefault("warnings", []).append(
                    "Equation order was recovered from source-page geometry."
                )
                changed.append(str(visual.get("objectId")))

    ordered_assignments = [
        assignment
        for page in sorted(
            structure.get("pages", []), key=lambda value: int(value["pageNumber"])
        )
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value["readingOrder"]),
        )
    ]
    positions = {
        str(assignment["blockId"]): index
        for index, assignment in enumerate(ordered_assignments)
    }
    assignment_by_id = {
        str(assignment["blockId"]): assignment
        for assignment in ordered_assignments
    }
    equation_visuals = [
        visual
        for page in structure.get("pages", [])
        for visual in page.get("visualObjects", [])
        if visual.get("kind") == "equation"
    ]
    for visual in sorted(
        equation_visuals,
        key=lambda value: positions.get(str(value.get("insertAfterBlockId")), 10**12),
    ):
        anchor_id = str(visual.get("insertAfterBlockId") or "")
        anchor = assignment_by_id.get(anchor_id)
        if anchor is None or not anchor.get("paragraphId"):
            continue
        anchor_position = positions[anchor_id]
        paragraph_id = anchor["paragraphId"]
        tails = [
            assignment
            for assignment in ordered_assignments[anchor_position + 1 :]
            if assignment.get("paragraphId") == paragraph_id
            and not assignment.get("hidden")
        ]
        if not tails:
            continue
        new_paragraph_id = f"para-{tails[0]['blockId']}"
        previous_tail_id: str | None = None
        for tail in tails:
            tail["paragraphId"] = new_paragraph_id
            tail["continuesFrom"] = previous_tail_id
            tail.setdefault("warnings", []).append(
                "A multi-provenance paragraph was split at an intervening equation."
            )
            previous_tail_id = str(tail["blockId"])

    diagnostics = structure.setdefault("doclingDiagnostics", {})
    diagnostics["geometryReanchoredEquationObjectIds"] = sorted(set(changed))
    return sorted(set(changed))


def _absorb_aligned_figure_panel_headings(
    source: Path,
    evidence: dict[str, Any],
    structure: dict[str, Any],
) -> None:
    """Fold a row of panel labels back into its owning combined figure.

    Docling occasionally exposes two column labels at the top of one figure as
    nested section headers.  The trigger intentionally requires a row of at
    least two short, unnumbered headings straddling one figure's top edge, plus
    an explicit visual anchor to one member of that row.  A lone real section
    heading above a figure therefore remains untouched.
    """

    diagnostics = structure.setdefault("doclingDiagnostics", {})
    diagnostics.setdefault("absorbedPanelHeadingGroups", [])
    diagnostics.setdefault("unabsorbedPanelHeadingBlockIds", [])
    try:
        pdf = fitz.open(source)
    except Exception:
        return

    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    structure_pages = {
        int(page["pageNumber"]): page for page in structure.get("pages", [])
    }
    blocks = {
        str(block["blockId"]): block
        for page in evidence_pages.values()
        for block in page.get("blocks", [])
    }
    ordered_assignments = [
        assignment
        for page in sorted(
            structure.get("pages", []), key=lambda value: int(value["pageNumber"])
        )
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value["readingOrder"]),
        )
    ]
    assignment_positions = {
        str(assignment["blockId"]): index
        for index, assignment in enumerate(ordered_assignments)
    }
    proposals: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    try:
        for page_number, page_structure in structure_pages.items():
            if not 1 <= page_number <= len(pdf):
                continue
            visible_headings = []
            for assignment in page_structure.get("blockAssignments", []):
                if assignment.get("role") != "heading" or assignment.get("hidden"):
                    continue
                block_id = str(assignment["blockId"])
                block = blocks.get(block_id)
                if block is None:
                    continue
                text = re.sub(r"\s+", " ", str(block.get("text", ""))).strip()
                bbox = [float(value) for value in block.get("bboxNormalized", [])]
                if (
                    not 1 <= len(text) <= 80
                    or len(bbox) != 4
                    or _NUMBERED_HEADING.match(text)
                    or _APPENDIX_HEADING.match(text)
                ):
                    continue
                visible_headings.append(
                    {
                        "assignment": assignment,
                        "block": block,
                        "blockId": block_id,
                        "bbox": bbox,
                    }
                )

            for visual in page_structure.get("visualObjects", []):
                if visual.get("kind") != "figure":
                    continue
                visual_bbox = [
                    float(value) for value in visual.get("bboxNormalized", [])
                ]
                if len(visual_bbox) != 4:
                    continue
                candidates = []
                for heading in visible_headings:
                    bbox = heading["bbox"]
                    width = max(1e-9, bbox[2] - bbox[0])
                    horizontal_overlap = max(
                        0.0,
                        min(bbox[2], visual_bbox[2])
                        - max(bbox[0], visual_bbox[0]),
                    )
                    if (
                        horizontal_overlap / width >= 0.75
                        and bbox[1] <= visual_bbox[1] + 0.025
                        and bbox[3] >= visual_bbox[1] - 0.012
                        and bbox[3] <= visual_bbox[1] + 0.04
                    ):
                        candidates.append(heading)
                if len(candidates) < 2:
                    continue
                center_ys = [
                    (heading["bbox"][1] + heading["bbox"][3]) / 2
                    for heading in candidates
                ]
                center_xs = sorted(
                    (heading["bbox"][0] + heading["bbox"][2]) / 2
                    for heading in candidates
                )
                candidate_ids = {
                    str(heading["blockId"]) for heading in candidates
                }
                if (
                    max(center_ys) - min(center_ys) > 0.02
                    or center_xs[-1] - center_xs[0] < 0.16
                    or str(visual.get("insertAfterBlockId") or "")
                    not in candidate_ids
                ):
                    continue
                proposals.append(
                    {
                        "pageNumber": page_number,
                        "visual": visual,
                        "headings": candidates,
                    }
                )
    finally:
        pdf.close()

    heading_owner_counts: dict[str, int] = {}
    for proposal in proposals:
        for heading in proposal["headings"]:
            block_id = str(heading["blockId"])
            heading_owner_counts[block_id] = heading_owner_counts.get(block_id, 0) + 1
    for block_id, count in heading_owner_counts.items():
        if count > 1:
            unresolved.add(block_id)

    semantic_anchor_roles = {
        "title",
        "author",
        "affiliation",
        "abstract",
        "heading",
        "paragraph",
        "list_item",
        "reference",
        "footnote",
    }
    accepted: list[dict[str, Any]] = []
    accepted_ids: set[str] = set()
    for proposal in proposals:
        candidate_ids = {
            str(heading["blockId"]) for heading in proposal["headings"]
        }
        if candidate_ids & unresolved:
            continue
        earliest_position = min(
            assignment_positions.get(block_id, 10**12)
            for block_id in candidate_ids
        )
        fallback = next(
            (
                previous
                for previous in reversed(ordered_assignments[:earliest_position])
                if not previous.get("hidden")
                and str(previous.get("blockId") or "") not in candidate_ids
                and str(previous.get("role") or "") in semantic_anchor_roles
                and previous.get("sectionId")
            ),
            None,
        )
        if fallback is None:
            unresolved.update(candidate_ids)
            continue
        proposal["fallbackBlockId"] = str(fallback["blockId"])
        proposal["fallbackSectionId"] = str(fallback["sectionId"])
        accepted.append(proposal)
        accepted_ids.update(candidate_ids)

    sections = list(structure.get("sections", []))
    removed_sections = {
        str(section["sectionId"]): section
        for section in sections
        if str(section.get("titleBlockId")) in accepted_ids
    }
    fallback_section_by_removed_id = {
        section_id: next(
            (
                str(proposal["fallbackSectionId"])
                for proposal in accepted
                if str(section.get("titleBlockId"))
                in {
                    str(heading["blockId"])
                    for heading in proposal["headings"]
                }
            ),
            None,
        )
        for section_id, section in removed_sections.items()
    }

    def surviving_parent(section_id: str | None) -> str | None:
        seen: set[str] = set()
        while section_id and section_id in removed_sections and section_id not in seen:
            seen.add(section_id)
            parent = removed_sections[section_id].get("parentSectionId")
            section_id = (
                str(parent)
                if parent
                else fallback_section_by_removed_id.get(section_id)
            )
        return section_id

    for assignment in ordered_assignments:
        section_id = assignment.get("sectionId")
        if section_id and str(section_id) in removed_sections:
            assignment["sectionId"] = surviving_parent(str(section_id))
    for section in sections:
        parent = section.get("parentSectionId")
        if parent and str(parent) in removed_sections:
            section["parentSectionId"] = surviving_parent(str(parent))
    structure["sections"] = [
        section
        for section in sections
        if str(section["sectionId"]) not in removed_sections
    ]

    absorbed_groups: list[dict[str, Any]] = []
    replacement_anchor_by_heading: dict[str, str] = {}
    for proposal in accepted:
        heading_ids = sorted(
            str(heading["blockId"]) for heading in proposal["headings"]
        )
        fallback_block_id = str(proposal["fallbackBlockId"])
        for block_id in heading_ids:
            replacement_anchor_by_heading[block_id] = fallback_block_id
        visual = proposal["visual"]
        bbox = [float(value) for value in visual["bboxNormalized"]]
        label_top = min(
            float(heading["bbox"][1]) for heading in proposal["headings"]
        )
        visual["bboxNormalized"] = [
            bbox[0],
            round(max(0.0, min(bbox[1], label_top - 0.004)), 5),
            bbox[2],
            bbox[3],
        ]
        for heading in proposal["headings"]:
            block = heading["block"]
            assignment = heading["assignment"]
            block["embeddedVisualOwnerRefs"] = [str(visual["objectId"])]
            block["suppressedVisualText"] = True
            block["suppressedFigurePanelHeading"] = True
            block["visualCaptionCandidate"] = False
            block["associatedVisualCaption"] = False
            assignment["role"] = "noise"
            assignment["sectionId"] = None
            assignment["paragraphId"] = None
            assignment["continuesFrom"] = None
            assignment["hidden"] = True
            assignment["suppressedVisualText"] = True
            assignment["suppressedFigurePanelHeading"] = True
            assignment["visualCaptionCandidate"] = False
            assignment["associatedVisualCaption"] = False
            warning = (
                "Aligned column labels were folded into their combined figure "
                "instead of being exposed as document sections."
            )
            if warning not in assignment.setdefault("warnings", []):
                assignment["warnings"].append(warning)
        absorbed_groups.append(
            {
                "objectId": str(visual["objectId"]),
                "pageNumber": int(proposal["pageNumber"]),
                "panelHeadingBlockIds": heading_ids,
                "fallbackBlockId": fallback_block_id,
                "fallbackSectionId": str(proposal["fallbackSectionId"]),
            }
        )

    if replacement_anchor_by_heading:
        for page_structure in structure.get("pages", []):
            for visual in page_structure.get("visualObjects", []):
                anchor = str(visual.get("insertAfterBlockId") or "")
                if anchor in replacement_anchor_by_heading:
                    visual["insertAfterBlockId"] = replacement_anchor_by_heading[anchor]

    diagnostics["absorbedPanelHeadingGroups"] = absorbed_groups
    diagnostics["unabsorbedPanelHeadingBlockIds"] = sorted(unresolved)
    if unresolved:
        warning = (
            "Aligned headings overlapped a figure boundary but could not be "
            "absorbed unambiguously; publication QA must fail closed."
        )
        if warning not in structure.setdefault("warnings", []):
            structure["warnings"].append(warning)


def _set_recovered_block(
    block: dict[str, Any],
    assignment: dict[str, Any],
    *,
    role: str,
    section_id: str | None,
    paragraph_id: str | None = None,
    continues_from: str | None = None,
    caption: bool = False,
) -> None:
    block["embeddedVisualOwnerRefs"] = []
    block["suppressedVisualText"] = False
    block["visualCaptionCandidate"] = caption
    block["associatedVisualCaption"] = caption
    assignment["role"] = role
    assignment["sectionId"] = section_id
    assignment["paragraphId"] = paragraph_id
    assignment["continuesFrom"] = continues_from
    assignment["hidden"] = False
    assignment["suppressedVisualText"] = False
    assignment["visualCaptionCandidate"] = caption
    assignment["associatedVisualCaption"] = caption
    warning = (
        "Text swallowed by an overlarge Docling picture was recovered from "
        "source-PDF geometry."
    )
    warnings = assignment.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)


def _move_bottom_footnotes_after_recovered_body(
    page_blocks: list[dict[str, Any]],
    page_structure: dict[str, Any],
    recovered_body_ids: set[str],
) -> list[str]:
    """Restore bottom-footnote order after Docling swallowed the page body."""

    if not recovered_body_ids:
        return []
    blocks = {str(block["blockId"]): block for block in page_blocks}
    assignments = list(page_structure.get("blockAssignments", []))
    assignment_by_id = {
        str(assignment["blockId"]): assignment for assignment in assignments
    }
    recovered_bottoms = [
        float(blocks[block_id]["bboxNormalized"][3])
        for block_id in recovered_body_ids
        if block_id in blocks
        and assignment_by_id.get(block_id, {}).get("role")
        in {"paragraph", "heading", "equation"}
    ]
    if not recovered_bottoms:
        return []
    recovered_bottom = max(recovered_bottoms)
    ordered = sorted(assignments, key=lambda value: int(value["readingOrder"]))
    positions = {
        str(assignment["blockId"]): index
        for index, assignment in enumerate(ordered)
    }
    recovered_positions = [
        positions[block_id] for block_id in recovered_body_ids if block_id in positions
    ]
    if not recovered_positions:
        return []
    last_recovered_position = max(recovered_positions)
    movable = [
        assignment
        for assignment in ordered
        if assignment.get("role") == "footnote"
        and not assignment.get("hidden")
        and str(assignment["blockId"]) in blocks
        and float(blocks[str(assignment["blockId"])]["bboxNormalized"][1])
        >= recovered_bottom - 0.004
        and positions[str(assignment["blockId"])] < last_recovered_position
    ]
    if not movable:
        return []
    movable_ids = {str(assignment["blockId"]) for assignment in movable}
    rebuilt = [
        assignment
        for assignment in ordered
        if str(assignment["blockId"]) not in movable_ids
    ]
    insertion = max(
        index
        for index, assignment in enumerate(rebuilt)
        if str(assignment["blockId"]) in recovered_body_ids
    ) + 1
    rebuilt[insertion:insertion] = movable
    for reading_order, assignment in enumerate(rebuilt, start=1):
        assignment["readingOrder"] = reading_order
    page_structure["blockAssignments"] = rebuilt
    return [str(assignment["blockId"]) for assignment in movable]


def _recover_overlarge_docling_pictures(
    source: Path,
    evidence: dict[str, Any],
    structure: dict[str, Any],
    *,
    raster_budget: PdfRenderBudget | None = None,
) -> None:
    """Recover pictures whose raw Docling bbox crosses internal captions.

    The trigger is deliberately narrow: a tall figure must own a caption-like
    descendant whose label begins materially inside the figure.  Once fired,
    source-PDF text blocks separate caption fragments from following prose and
    rendered ink honors Form-XObject clipping that Docling's raw bbox lost.
    """

    detected_caption_ids: set[str] = set()
    attached_caption_ids: set[str] = set()
    unresolved_visual_ids: set[str] = set()
    recovered_diagnostics: list[dict[str, Any]] = []
    section_by_id = {
        str(section["sectionId"]): section
        for section in structure.get("sections", [])
    }
    page_structures = {
        int(page["pageNumber"]): page for page in structure.get("pages", [])
    }

    try:
        probe = fitz.open(source)
        probe.close()
    except Exception:
        diagnostics = structure.setdefault("doclingDiagnostics", {})
        diagnostics.setdefault("recoveredOverlargeVisuals", [])
        diagnostics.setdefault("suppressedInternalCaptionBlockIds", [])
        diagnostics.setdefault("overlargeVisualObjectIds", [])
        return

    with fitz.open(source) as pdf:
        for evidence_page in evidence.get("pages", []):
            page_number = int(evidence_page["pageNumber"])
            page_structure = page_structures.get(page_number)
            if page_structure is None or not 1 <= page_number <= len(pdf):
                continue
            pdf_page = pdf[page_number - 1]
            pdf_regions = _pdf_text_regions(pdf_page)
            page_blocks = list(evidence_page.get("blocks", []))
            block_positions = {
                str(block["blockId"]): index for index, block in enumerate(page_blocks)
            }
            assignments = {
                str(assignment["blockId"]): assignment
                for assignment in page_structure.get("blockAssignments", [])
            }
            source_regions = {
                str(block["blockId"]): _best_pdf_text_region(block, pdf_regions)
                for block in page_blocks
            }

            original_visuals = list(page_structure.get("visualObjects", []))
            replacement_visuals: list[dict[str, Any]] = []
            for visual in original_visuals:
                if visual.get("kind") != "figure":
                    replacement_visuals.append(visual)
                    continue
                object_id = str(visual.get("objectId", ""))
                visual_bbox = [float(value) for value in visual["bboxNormalized"]]
                owner_refs = {
                    str(owner_ref)
                    for block in page_blocks
                    for owner_ref in block.get("embeddedVisualOwnerRefs", [])
                    if _safe_id(str(owner_ref), prefix="figure") == object_id
                }
                owner_blocks = [
                    block
                    for block in page_blocks
                    if owner_refs
                    & {
                        str(value)
                        for value in block.get("embeddedVisualOwnerRefs", [])
                    }
                ]
                caption_starts = [
                    block
                    for block in owner_blocks
                    if _is_material_internal_figure_caption(block, visual_bbox)
                ]
                caption_starts.sort(
                    key=lambda block: (
                        float(block["bboxNormalized"][1]),
                        block_positions[str(block["blockId"])],
                    )
                )
                detected_caption_ids.update(
                    str(block["blockId"]) for block in caption_starts
                )
                if not caption_starts:
                    replacement_visuals.append(visual)
                    continue
                if any(
                    source_regions.get(str(start["blockId"])) is None
                    for start in caption_starts
                ):
                    unresolved_visual_ids.add(object_id)
                    replacement_visuals.append(visual)
                    continue

                caption_groups = [
                    _caption_blocks_for_start(
                        start,
                        caption_starts[index + 1]
                        if index + 1 < len(caption_starts)
                        else None,
                        owner_blocks,
                        source_regions,
                        block_positions,
                    )
                    for index, start in enumerate(caption_starts)
                ]
                if any(not group for group in caption_groups):
                    unresolved_visual_ids.add(object_id)
                    replacement_visuals.append(visual)
                    continue

                group_bboxes = []
                for group in caption_groups:
                    boxes = [
                        [float(value) for value in block["bboxNormalized"]]
                        for block in group
                    ]
                    group_bboxes.append(
                        [
                            min(box[0] for box in boxes),
                            min(box[1] for box in boxes),
                            max(box[2] for box in boxes),
                            max(box[3] for box in boxes),
                        ]
                    )
                caption_group_ids = {
                    str(block["blockId"])
                    for group in caption_groups
                    for block in group
                }
                inter_caption_blocks = [
                    block
                    for index in range(len(group_bboxes) - 1)
                    for block in owner_blocks
                    if str(block["blockId"]) not in caption_group_ids
                    and block.get("suppressedVisualText")
                    and float(block["bboxNormalized"][3])
                    > group_bboxes[index][3] + 0.001
                    and float(block["bboxNormalized"][1])
                    < group_bboxes[index + 1][1] - 0.001
                ]
                if inter_caption_blocks:
                    # The following figure body cannot be separated safely from
                    # semantic text in the same interval.  Preserve the raw
                    # state and force publication QA to fail closed.
                    unresolved_visual_ids.add(object_id)
                    replacement_visuals.append(visual)
                    continue
                final_caption_bottom = group_bboxes[-1][3]
                if any(
                    str(block["blockId"]) not in caption_group_ids
                    and block.get("suppressedVisualText")
                    and float(block["bboxNormalized"][1])
                    >= final_caption_bottom - 0.002
                    and float(block["bboxNormalized"][1]) < 0.92
                    and source_regions.get(str(block["blockId"])) is None
                    for block in owner_blocks
                ):
                    unresolved_visual_ids.add(object_id)
                    replacement_visuals.append(visual)
                    continue

                recovered_bboxes: list[list[float]] = []
                previous_caption_bottom: float | None = None
                for caption_bbox in group_bboxes:
                    body_top = (
                        visual_bbox[1]
                        if previous_caption_bottom is None
                        else previous_caption_bottom + 0.004
                    )
                    body_bottom = caption_bbox[1] - 0.004
                    candidate_bbox = [
                        visual_bbox[0],
                        body_top,
                        visual_bbox[2],
                        body_bottom,
                    ]
                    if (
                        body_bottom <= body_top
                        or (candidate_bbox[2] - candidate_bbox[0])
                        * (candidate_bbox[3] - candidate_bbox[1])
                        < 0.0005
                    ):
                        recovered_bboxes = []
                        break
                    tightened = _rendered_ink_bbox(
                        pdf_page,
                        candidate_bbox,
                        raster_budget=raster_budget,
                    )
                    if tightened is None:
                        recovered_bboxes = []
                        break
                    recovered_bboxes.append(tightened)
                    previous_caption_bottom = caption_bbox[3]
                if len(recovered_bboxes) != len(caption_groups):
                    unresolved_visual_ids.add(object_id)
                    replacement_visuals.append(visual)
                    continue

                anchor_assignment = assignments.get(
                    str(visual.get("insertAfterBlockId") or "")
                )
                base_section_id = (
                    str(anchor_assignment.get("sectionId"))
                    if anchor_assignment and anchor_assignment.get("sectionId")
                    else None
                )
                recovered_figures: list[dict[str, Any]] = []
                for index, (group, bbox) in enumerate(
                    zip(caption_groups, recovered_bboxes, strict=True), start=1
                ):
                    caption_ids = [str(block["blockId"]) for block in group]
                    for block in group:
                        assignment = assignments[str(block["blockId"])]
                        _set_recovered_block(
                            block,
                            assignment,
                            role="caption",
                            section_id=base_section_id,
                            caption=True,
                        )
                    attached_caption_ids.update(caption_ids)
                    recovered = copy.deepcopy(visual)
                    if index > 1:
                        recovered["objectId"] = f"{object_id}-split-{index}"
                    recovered["bboxNormalized"] = bbox
                    recovered["captionBlockIds"] = caption_ids
                    recovered["label"] = _visual_label(
                        [str(block["text"]) for block in group], "figure"
                    )
                    warnings = recovered.setdefault("warnings", [])
                    if _RECOVERED_INTERNAL_CAPTION_WARNING not in warnings:
                        warnings.append(_RECOVERED_INTERNAL_CAPTION_WARNING)
                    recovered_figures.append(recovered)
                replacement_visuals.extend(recovered_figures)

                released_blocks = [
                    block
                    for block in owner_blocks
                    if str(block["blockId"]) not in attached_caption_ids
                    and block.get("suppressedVisualText")
                    and float(block["bboxNormalized"][1])
                    >= final_caption_bottom - 0.002
                    and float(block["bboxNormalized"][1]) < 0.92
                    and source_regions.get(str(block["blockId"])) is not None
                ]
                released_blocks.sort(
                    key=lambda block: (
                        int(
                            source_regions[str(block["blockId"])]["regionIndex"]
                        ),
                        _best_pdf_text_line_index(
                            block, source_regions[str(block["blockId"])]
                        )
                        or 0,
                        round(float(block["bboxNormalized"][0]), 4),
                        block_positions[str(block["blockId"])],
                    )
                )
                released_positions = {
                    str(block["blockId"]): index
                    for index, block in enumerate(released_blocks)
                }

                equation_blocks: set[str] = set()
                recovered_equations: list[dict[str, Any]] = []
                for label_block in released_blocks:
                    label_match = re.fullmatch(
                        r"\s*\((\d+[a-z]?)\)\s*", str(label_block.get("text", ""))
                    )
                    if label_match is None:
                        continue
                    label_bbox = [
                        float(value) for value in label_block["bboxNormalized"]
                    ]
                    group = [
                        block
                        for block in released_blocks
                        if float(block["bboxNormalized"][3])
                        >= label_bbox[1] - 0.022
                        and float(block["bboxNormalized"][1])
                        <= label_bbox[3] + 0.022
                    ]
                    math_support = any(
                        _pdf_line_evidence(
                            block, source_regions.get(str(block["blockId"]))
                        )[1]
                        >= 0.2
                        for block in group
                    ) or any(
                        re.search(
                            r"[=<>\u2264\u2265\u2211\u220f\u222b\u221a\u00b1\u00d7\u00f7\u2202\u2207]",
                            str(block.get("text", "")),
                        )
                        for block in group
                    )
                    if len(group) < 3 or not math_support:
                        continue
                    boxes = [
                        [float(value) for value in block["bboxNormalized"]]
                        for block in group
                    ]
                    equation_bbox = [
                        max(0.0, min(box[0] for box in boxes) - 0.003),
                        max(0.0, min(box[1] for box in boxes) - 0.003),
                        min(1.0, max(box[2] for box in boxes) + 0.003),
                        min(1.0, max(box[3] for box in boxes) + 0.003),
                    ]
                    tightened_equation = _rendered_ink_bbox(
                        pdf_page,
                        equation_bbox,
                        raster_budget=raster_budget,
                    )
                    if tightened_equation is None:
                        continue
                    equation_ids = [str(block["blockId"]) for block in group]
                    equation_blocks.update(equation_ids)
                    first_position = min(
                        released_positions[block_id] for block_id in equation_ids
                    )
                    previous_id = next(
                        (
                            str(block["blockId"])
                            for block in reversed(released_blocks)
                            if released_positions[str(block["blockId"])] < first_position
                            and str(block["blockId"]) not in equation_ids
                        ),
                        str(visual.get("insertAfterBlockId") or "") or None,
                    )
                    recovered_equations.append(
                        {
                            "objectId": (
                                "equation-recovered-"
                                f"{str(label_block['blockId']).removeprefix('dl-')}"
                            ),
                            "kind": "equation",
                            "label": f"Equation {label_match.group(1)}",
                            "bboxNormalized": tightened_equation,
                            "captionBlockIds": [],
                            "insertAfterBlockId": previous_id,
                            "confidence": 0.96,
                            "warnings": [
                                "Equation swallowed by an overlarge Docling picture was recovered from source-PDF coordinates."
                            ],
                        }
                    )
                replacement_visuals.extend(recovered_equations)

                current_section_id = base_section_id
                paragraph_groups: list[
                    tuple[str | None, int, list[dict[str, Any]]]
                ] = []
                active_paragraph_key: tuple[str | None, int] | None = None
                for block in released_blocks:
                    block_id = str(block["blockId"])
                    if block_id in equation_blocks:
                        active_paragraph_key = None
                        _set_recovered_block(
                            block,
                            assignments[block_id],
                            role="equation",
                            section_id=current_section_id,
                        )
                        continue
                    region = source_regions[block_id]
                    bold_ratio, _math_ratio, _font_size = _pdf_line_evidence(
                        block, region
                    )
                    text = str(block.get("text", "")).strip()
                    is_heading = 4 <= len(text) <= 120 and bold_ratio >= 0.72
                    if is_heading:
                        active_paragraph_key = None
                        base_section = section_by_id.get(str(base_section_id))
                        parent_level = (
                            int(base_section.get("level", 1))
                            if base_section
                            else 1
                        )
                        current_section_id = _safe_id(
                            str(block["sourceBlockId"]).removeprefix("dl-"),
                            prefix="sec",
                        )
                        section = {
                            "sectionId": current_section_id,
                            "number": None,
                            "titleBlockId": block_id,
                            "level": min(6, parent_level + 1),
                            "parentSectionId": base_section_id,
                            "pageStart": page_number,
                        }
                        if current_section_id not in section_by_id:
                            structure.setdefault("sections", []).append(section)
                            section_by_id[current_section_id] = section
                        _set_recovered_block(
                            block,
                            assignments[block_id],
                            role="heading",
                            section_id=current_section_id,
                        )
                        continue
                    paragraph_key = (
                        current_section_id,
                        int(region["regionIndex"]),
                    )
                    if active_paragraph_key != paragraph_key:
                        paragraph_groups.append(
                            (current_section_id, paragraph_key[1], [])
                        )
                        active_paragraph_key = paragraph_key
                    paragraph_groups[-1][2].append(block)

                last_paragraph_group: list[dict[str, Any]] = []
                for paragraph_section_id, _region_index, group in paragraph_groups:
                    paragraph_id = f"para-{group[0]['blockId']}"
                    previous_block_id: str | None = None
                    for block in group:
                        block_id = str(block["blockId"])
                        _set_recovered_block(
                            block,
                            assignments[block_id],
                            role="paragraph",
                            section_id=paragraph_section_id,
                            paragraph_id=paragraph_id,
                            continues_from=previous_block_id,
                        )
                        previous_block_id = block_id
                    last_paragraph_group = group

                moved_footnote_ids = _move_bottom_footnotes_after_recovered_body(
                    page_blocks,
                    page_structure,
                    {str(block["blockId"]) for block in released_blocks},
                )

                if last_paragraph_group and page_number < len(pdf):
                    last_block = last_paragraph_group[-1]
                    last_text = str(last_block.get("text", "")).rstrip()
                    last_bbox = [
                        float(value) for value in last_block["bboxNormalized"]
                    ]
                    next_structure = page_structures.get(page_number + 1)
                    if (
                        next_structure is not None
                        and last_bbox[3] >= 0.88
                        and not re.search(r"[.!?;:)\]}\u201d\u2019]\s*$", last_text)
                    ):
                        next_assignments = sorted(
                            next_structure.get("blockAssignments", []),
                            key=lambda value: int(value["readingOrder"]),
                        )
                        next_body = next(
                            (
                                assignment
                                for assignment in next_assignments
                                if not assignment.get("hidden")
                                and assignment.get("role") in {"abstract", "paragraph"}
                            ),
                            None,
                        )
                        if next_body is not None:
                            continuation_section_id = assignments[
                                str(last_block["blockId"])
                            ].get("sectionId")
                            next_body["paragraphId"] = assignments[
                                str(last_block["blockId"])
                            ]["paragraphId"]
                            next_body["continuesFrom"] = str(last_block["blockId"])
                            next_body["sectionId"] = continuation_section_id
                            warning = (
                                "Cross-page continuation was relinked after recovering "
                                "text swallowed by an overlarge picture."
                            )
                            warnings = next_body.setdefault("warnings", [])
                            if warning not in warnings:
                                warnings.append(warning)
                            for assignment in next_assignments:
                                if assignment.get("role") == "heading":
                                    break
                                if assignment.get("sectionId") in {
                                    None,
                                    base_section_id,
                                }:
                                    assignment["sectionId"] = (
                                        continuation_section_id
                                    )
                            for next_visual in next_structure.get(
                                "visualObjects", []
                            ):
                                if next_visual.get("insertAfterBlockId") == visual.get(
                                    "insertAfterBlockId"
                                ):
                                    next_visual["insertAfterBlockId"] = str(
                                        last_block["blockId"]
                                    )

                recovered_diagnostics.append(
                    {
                        "objectId": object_id,
                        "pageNumber": page_number,
                        "replacementObjectIds": [
                            value["objectId"] for value in recovered_figures
                        ],
                        "captionBlockIds": [
                            str(block["blockId"])
                            for group in caption_groups
                            for block in group
                        ],
                        "movedFootnoteBlockIds": moved_footnote_ids,
                    }
                )
            page_structure["visualObjects"] = replacement_visuals

    positions: dict[str, int] = {}
    position = 0
    for page in sorted(
        structure.get("pages", []), key=lambda value: int(value["pageNumber"])
    ):
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value["readingOrder"]),
        ):
            position += 1
            positions[str(assignment["blockId"])] = position
    structure.get("sections", []).sort(
        key=lambda section: positions.get(str(section["titleBlockId"]), 10**12)
    )

    diagnostics = structure.setdefault("doclingDiagnostics", {})
    all_blocks = [
        block
        for page in evidence.get("pages", [])
        for block in page.get("blocks", [])
    ]
    diagnostics["embeddedVisualTextItems"] = len(
        {
            str(block.get("sourceBlockId") or block.get("blockId"))
            for block in all_blocks
            if block.get("suppressedVisualText")
        }
    )
    diagnostics["suppressedEmbeddedVisualTextBlocks"] = sum(
        bool(block.get("suppressedVisualText")) for block in all_blocks
    )
    suppressed_internal = sorted(detected_caption_ids - attached_caption_ids)
    diagnostics["recoveredOverlargeVisuals"] = recovered_diagnostics
    diagnostics["suppressedInternalCaptionBlockIds"] = suppressed_internal
    diagnostics["overlargeVisualObjectIds"] = sorted(unresolved_visual_ids)
    if suppressed_internal or unresolved_visual_ids:
        warning = (
            "Source-PDF recovery could not safely resolve every overlarge "
            "Docling picture; publication QA must fail closed."
        )
        warnings = structure.setdefault("warnings", [])
        if warning not in warnings:
            warnings.append(warning)


def _looks_like_combined_left_right_crop(
    visual_bbox: list[float], caption_bbox: list[float]
) -> bool:
    """Recognize a single crop that already spans a left/right caption."""

    visual_width = visual_bbox[2] - visual_bbox[0]
    caption_width = caption_bbox[2] - caption_bbox[0]
    horizontal_overlap = max(
        0.0,
        min(visual_bbox[2], caption_bbox[2])
        - max(visual_bbox[0], caption_bbox[0]),
    )
    center_distance = abs(
        (visual_bbox[0] + visual_bbox[2] - caption_bbox[0] - caption_bbox[2])
        / 2
    )
    vertical_gap = caption_bbox[1] - visual_bbox[3]
    return (
        visual_width >= 0.30
        and caption_width > 0
        and visual_width / caption_width >= 0.72
        and horizontal_overlap / min(visual_width, caption_width) >= 0.82
        and center_distance <= 0.08
        and 0 <= vertical_gap <= 0.12
    )


def _relink_interrupted_panel_paragraph(
    *,
    anchor_id: str | None,
    after_block_ids: set[str],
    fallback_section_id: str | None,
    ordered_assignments: list[dict[str, Any]],
    assignment_positions: dict[str, int],
    blocks: dict[str, dict[str, Any]],
) -> list[str]:
    """Join a sentence split across a floated, falsely sectioned panel."""

    if not anchor_id or anchor_id not in assignment_positions or anchor_id not in blocks:
        return []
    anchor_assignment = ordered_assignments[assignment_positions[anchor_id]]
    if (
        anchor_assignment.get("hidden")
        or anchor_assignment.get("role") not in {"abstract", "paragraph"}
        or not anchor_assignment.get("paragraphId")
    ):
        return []
    anchor_text = str(blocks[anchor_id].get("text", "")).rstrip()
    if not anchor_text or re.search(r"[.!?;:)\]}\u2019\u201d]\s*$", anchor_text):
        return []
    boundary_positions = [
        assignment_positions[block_id]
        for block_id in after_block_ids
        if block_id in assignment_positions
    ]
    if not boundary_positions:
        return []
    next_assignment = next(
        (
            assignment
            for assignment in ordered_assignments[max(boundary_positions) + 1 :]
            if not assignment.get("hidden")
            and assignment.get("role") in {"abstract", "paragraph"}
            and str(assignment["blockId"]) in blocks
        ),
        None,
    )
    if next_assignment is None:
        return []
    next_id = str(next_assignment["blockId"])
    next_text = str(blocks[next_id].get("text", "")).lstrip()
    if not next_text[:1].islower():
        return []
    if next_assignment.get("sectionId") not in {
        None,
        fallback_section_id,
    }:
        return []
    old_paragraph_id = next_assignment.get("paragraphId") or next_id
    continuation_group = [
        assignment
        for assignment in ordered_assignments[assignment_positions[next_id] :]
        if (assignment.get("paragraphId") or str(assignment["blockId"]))
        == old_paragraph_id
    ]
    previous_id = anchor_id
    relinked: list[str] = []
    for assignment in continuation_group:
        block_id = str(assignment["blockId"])
        assignment["paragraphId"] = anchor_assignment["paragraphId"]
        assignment["sectionId"] = anchor_assignment.get("sectionId")
        assignment["continuesFrom"] = previous_id
        warning = (
            "A sentence interrupted by a split left/right figure was relinked."
        )
        if warning not in assignment.setdefault("warnings", []):
            assignment["warnings"].append(warning)
        previous_id = block_id
        relinked.append(block_id)
    return relinked


def _merge_split_panel_figures(
    source: Path,
    evidence: dict[str, Any],
    structure: dict[str, Any],
    *,
    raster_budget: PdfRenderBudget | None = None,
) -> None:
    """Merge a nearby unlabeled panel when one shared caption says left/right."""

    diagnostics = structure.setdefault("doclingDiagnostics", {})
    diagnostics.setdefault("mergedMultiPanelVisuals", [])
    diagnostics.setdefault("unmergedMultiPanelVisualObjectIds", [])
    diagnostics.setdefault("danglingParentSectionIds", [])
    try:
        pdf = fitz.open(source)
    except Exception:
        return

    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    structure_pages = {
        int(page["pageNumber"]): page for page in structure.get("pages", [])
    }
    global_blocks = {
        str(block["blockId"]): block
        for page in evidence.get("pages", [])
        for block in page.get("blocks", [])
    }
    ordered_assignments = [
        assignment
        for page in sorted(
            structure.get("pages", []), key=lambda value: int(value["pageNumber"])
        )
        for assignment in sorted(
            page.get("blockAssignments", []),
            key=lambda value: int(value["readingOrder"]),
        )
    ]
    global_assignment_positions = {
        str(assignment["blockId"]): index
        for index, assignment in enumerate(ordered_assignments)
    }
    merged: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    try:
        for page_number, page_structure in structure_pages.items():
            evidence_page = evidence_pages.get(page_number)
            if evidence_page is None or not 1 <= page_number <= len(pdf):
                continue
            blocks = {
                str(block["blockId"]): block
                for block in evidence_page.get("blocks", [])
            }
            assignments = {
                str(assignment["blockId"]): assignment
                for assignment in page_structure.get("blockAssignments", [])
            }
            visuals = list(page_structure.get("visualObjects", []))
            consumed_ids: set[str] = set()
            replacements: dict[str, dict[str, Any]] = {}
            for captioned in visuals:
                caption_ids = [
                    str(value) for value in captioned.get("captionBlockIds", [])
                ]
                if captioned.get("kind") != "figure" or not caption_ids:
                    continue
                caption_text = " ".join(
                    str(blocks[block_id].get("text", ""))
                    for block_id in caption_ids
                    if block_id in blocks
                )
                if not (
                    re.search(r"\(\s*left\s*\)", caption_text, re.IGNORECASE)
                    and re.search(
                        r"\(\s*right\s*\)", caption_text, re.IGNORECASE
                    )
                ):
                    continue
                caption_bbox_values = [
                    [float(value) for value in blocks[block_id]["bboxNormalized"]]
                    for block_id in caption_ids
                    if block_id in blocks
                ]
                if not caption_bbox_values:
                    continue
                caption_bbox = [
                    min(value[0] for value in caption_bbox_values),
                    min(value[1] for value in caption_bbox_values),
                    max(value[2] for value in caption_bbox_values),
                    max(value[3] for value in caption_bbox_values),
                ]
                caption_top = caption_bbox[1]
                captioned_bbox = [
                    float(value) for value in captioned["bboxNormalized"]
                ]
                candidates: list[dict[str, Any]] = []
                for candidate in visuals:
                    if (
                        candidate is captioned
                        or candidate.get("kind") != "figure"
                        or candidate.get("label")
                        or candidate.get("captionBlockIds")
                    ):
                        continue
                    candidate_bbox = [
                        float(value) for value in candidate["bboxNormalized"]
                    ]
                    vertical_overlap = max(
                        0.0,
                        min(captioned_bbox[3], candidate_bbox[3])
                        - max(captioned_bbox[1], candidate_bbox[1]),
                    )
                    minimum_height = max(
                        1e-9,
                        min(
                            captioned_bbox[3] - captioned_bbox[1],
                            candidate_bbox[3] - candidate_bbox[1],
                        ),
                    )
                    horizontal_gap = max(
                        0.0,
                        max(captioned_bbox[0], candidate_bbox[0])
                        - min(captioned_bbox[2], candidate_bbox[2]),
                    )
                    combined_left = min(captioned_bbox[0], candidate_bbox[0])
                    combined_right = max(captioned_bbox[2], candidate_bbox[2])
                    combined_bottom = max(captioned_bbox[3], candidate_bbox[3])
                    if (
                        vertical_overlap / minimum_height >= 0.55
                        and horizontal_gap <= 0.22
                        and combined_right - combined_left <= 0.82
                        and 0 <= caption_top - combined_bottom <= 0.10
                    ):
                        candidates.append(candidate)
                if not candidates:
                    if not _looks_like_combined_left_right_crop(
                        captioned_bbox, caption_bbox
                    ):
                        unresolved.add(str(captioned["objectId"]))
                    continue
                if len(candidates) != 1:
                    unresolved.add(str(captioned["objectId"]))
                    unresolved.update(str(value["objectId"]) for value in candidates)
                    continue

                unlabeled = candidates[0]
                if (
                    str(captioned["objectId"]) in consumed_ids
                    or str(unlabeled["objectId"]) in consumed_ids
                ):
                    unresolved.update(
                        {str(captioned["objectId"]), str(unlabeled["objectId"])}
                    )
                    continue
                combined_bbox = [
                    min(
                        float(captioned["bboxNormalized"][0]),
                        float(unlabeled["bboxNormalized"][0]),
                    ),
                    min(
                        float(captioned["bboxNormalized"][1]),
                        float(unlabeled["bboxNormalized"][1]),
                    ),
                    max(
                        float(captioned["bboxNormalized"][2]),
                        float(unlabeled["bboxNormalized"][2]),
                    ),
                    max(
                        float(captioned["bboxNormalized"][3]),
                        float(unlabeled["bboxNormalized"][3]),
                    ),
                ]
                caption_folded = re.sub(r"\s+", " ", caption_text).casefold()
                panel_headers = []
                for block in evidence_page.get("blocks", []):
                    block_id = str(block["blockId"])
                    text = re.sub(r"\s+", " ", str(block.get("text", ""))).strip()
                    bbox = [float(value) for value in block["bboxNormalized"]]
                    horizontal_overlap = max(
                        0.0,
                        min(combined_bbox[2], bbox[2])
                        - max(combined_bbox[0], bbox[0]),
                    )
                    if (
                        block_id not in caption_ids
                        and 4 <= len(text) <= 80
                        and text.casefold() in caption_folded
                        and horizontal_overlap
                        / max(1e-9, bbox[2] - bbox[0])
                        >= 0.65
                        and bbox[1] >= combined_bbox[1] - 0.03
                        and bbox[3] <= combined_bbox[3] + 0.03
                    ):
                        panel_headers.append(block)
                if len(panel_headers) < 2:
                    unresolved.update(
                        {str(captioned["objectId"]), str(unlabeled["objectId"])}
                    )
                    continue
                for block in panel_headers:
                    bbox = [float(value) for value in block["bboxNormalized"]]
                    combined_bbox = [
                        min(combined_bbox[0], bbox[0]),
                        min(combined_bbox[1], bbox[1]),
                        max(combined_bbox[2], bbox[2]),
                        max(combined_bbox[3], bbox[3]),
                    ]
                tightened = _rendered_ink_bbox(
                    pdf[page_number - 1],
                    combined_bbox,
                    raster_budget=raster_budget,
                )
                if tightened is None:
                    unresolved.update(
                        {str(captioned["objectId"]), str(unlabeled["objectId"])}
                    )
                    continue

                removed_section_ids = {
                    str(section["sectionId"])
                    for section in structure.get("sections", [])
                    if str(section.get("titleBlockId"))
                    in {str(block["blockId"]) for block in panel_headers}
                }
                fallback_section_id = next(
                    (
                        str(assignment.get("sectionId"))
                        for assignment in reversed(
                            ordered_assignments[
                                : min(
                                    global_assignment_positions.get(
                                        str(block["blockId"]), 10**12
                                    )
                                    for block in panel_headers
                                )
                            ]
                        )
                        if assignment.get("sectionId")
                        and str(assignment.get("sectionId"))
                        not in removed_section_ids
                        and not assignment.get("hidden")
                    ),
                    None,
                )
                anchor_id = next(
                    (
                        str(assignment["blockId"])
                        for assignment in reversed(
                            ordered_assignments[
                                : min(
                                    global_assignment_positions.get(
                                        str(block["blockId"]), 10**12
                                    )
                                    for block in panel_headers
                                )
                            ]
                        )
                        if not assignment.get("hidden")
                        and assignment.get("role")
                        not in {"caption", "equation", "algorithm"}
                    ),
                    str(captioned.get("insertAfterBlockId") or "") or None,
                )
                for block in panel_headers:
                    block_id = str(block["blockId"])
                    owner_refs = {
                        str(value)
                        for caption_id in caption_ids
                        if caption_id in blocks
                        for value in blocks[caption_id].get(
                            "embeddedVisualOwnerRefs", []
                        )
                    }
                    block["embeddedVisualOwnerRefs"] = sorted(owner_refs)
                    block["suppressedVisualText"] = True
                    block["visualCaptionCandidate"] = False
                    block["associatedVisualCaption"] = False
                    assignment = assignments[block_id]
                    assignment["role"] = "noise"
                    assignment["sectionId"] = None
                    assignment["paragraphId"] = None
                    assignment["continuesFrom"] = None
                    assignment["hidden"] = True
                    assignment["suppressedVisualText"] = True
                    assignment["visualCaptionCandidate"] = False
                    assignment["associatedVisualCaption"] = False
                    warning = (
                        "A panel header duplicated inside a shared left/right figure was suppressed."
                    )
                    if warning not in assignment.setdefault("warnings", []):
                        assignment["warnings"].append(warning)
                reparented_section_ids: list[str] = []
                if removed_section_ids:
                    for section in structure.get("sections", []):
                        if str(section.get("parentSectionId")) in removed_section_ids:
                            section["parentSectionId"] = fallback_section_id
                            reparented_section_ids.append(str(section["sectionId"]))
                    structure["sections"] = [
                        section
                        for section in structure.get("sections", [])
                        if str(section["sectionId"]) not in removed_section_ids
                    ]
                    for assignment in ordered_assignments:
                        if str(assignment.get("sectionId")) in removed_section_ids:
                            assignment["sectionId"] = fallback_section_id

                relinked_paragraph_ids = _relink_interrupted_panel_paragraph(
                    anchor_id=anchor_id,
                    after_block_ids={
                        *caption_ids,
                        *(str(block["blockId"]) for block in panel_headers),
                    },
                    fallback_section_id=fallback_section_id,
                    ordered_assignments=ordered_assignments,
                    assignment_positions=global_assignment_positions,
                    blocks=global_blocks,
                )

                combined = copy.deepcopy(captioned)
                combined["bboxNormalized"] = tightened
                combined["insertAfterBlockId"] = anchor_id
                warning = (
                    "Adjacent left/right panels split into separate Docling pictures were merged under their shared caption."
                )
                if warning not in combined.setdefault("warnings", []):
                    combined["warnings"].append(warning)
                consumed_ids.update(
                    {str(captioned["objectId"]), str(unlabeled["objectId"])}
                )
                replacements[str(captioned["objectId"])] = combined
                merged.append(
                    {
                        "objectId": str(combined["objectId"]),
                        "pageNumber": page_number,
                        "mergedObjectIds": [
                            str(unlabeled["objectId"]),
                            str(captioned["objectId"]),
                        ],
                        "suppressedPanelHeaderBlockIds": [
                            str(block["blockId"]) for block in panel_headers
                        ],
                        "relinkedParagraphBlockIds": relinked_paragraph_ids,
                        "reparentedSectionIds": sorted(reparented_section_ids),
                    }
                )

            if consumed_ids:
                rebuilt: list[dict[str, Any]] = []
                inserted: set[str] = set()
                for visual in visuals:
                    object_id = str(visual["objectId"])
                    if object_id not in consumed_ids:
                        rebuilt.append(visual)
                        continue
                    replacement = replacements.get(object_id)
                    if replacement is not None and object_id not in inserted:
                        rebuilt.append(replacement)
                        inserted.add(object_id)
                page_structure["visualObjects"] = rebuilt
    finally:
        pdf.close()

    diagnostics["mergedMultiPanelVisuals"] = merged
    diagnostics["unmergedMultiPanelVisualObjectIds"] = sorted(unresolved)
    section_ids = {
        str(section["sectionId"]) for section in structure.get("sections", [])
    }
    dangling_parent_section_ids = sorted(
        str(section["sectionId"])
        for section in structure.get("sections", [])
        if section.get("parentSectionId")
        and str(section["parentSectionId"]) not in section_ids
    )
    diagnostics["danglingParentSectionIds"] = dangling_parent_section_ids
    all_blocks = [
        block
        for page in evidence.get("pages", [])
        for block in page.get("blocks", [])
    ]
    diagnostics["embeddedVisualTextItems"] = len(
        {
            str(block.get("sourceBlockId") or block.get("blockId"))
            for block in all_blocks
            if block.get("suppressedVisualText")
        }
    )
    diagnostics["suppressedEmbeddedVisualTextBlocks"] = sum(
        bool(block.get("suppressedVisualText")) for block in all_blocks
    )
    if unresolved:
        warning = (
            "A caption explicitly described left/right panels, but their split "
            "Docling pictures could not be merged unambiguously."
        )
        if warning not in structure.setdefault("warnings", []):
            structure["warnings"].append(warning)
    if dangling_parent_section_ids:
        warning = (
            "Panel-section cleanup left a dangling parentSectionId; publication "
            "QA must fail closed."
        )
        if warning not in structure.setdefault("warnings", []):
            structure["warnings"].append(warning)


_PARALLEL_TABLE_LABEL = re.compile(
    r"^\s*(Table)\s+(\d+[A-Za-z]?)\s*[:.]\s*\S", re.IGNORECASE
)
_PARALLEL_ALGORITHM_LABEL = re.compile(
    r"^\s*(Algorithm)\s+(\d+[A-Za-z]?)\b(?:\s*[:.]|\s+\S)",
    re.IGNORECASE,
)


def _join_parallel_caption_lines(parts: list[str]) -> str:
    """Join caption lines while distinguishing soft and compound hyphens."""

    result = ""
    for part in parts:
        value = re.sub(r"\s+", " ", part).strip()
        if not value:
            continue
        if not result:
            result = value
            continue
        if result.endswith("-") and value[:1].islower():
            trailing_word = result.rsplit(" ", 1)[-1]
            if trailing_word.count("-") >= 2:
                result += value
            else:
                result = result[:-1] + value
        else:
            result += " " + value
    return result


def _parallel_labeled_visual_split_plan(
    lines: list[dict[str, Any]], visual_bbox: list[float]
) -> dict[str, Any] | None:
    """Plan a safe two-column split from explicit source-PDF object labels."""

    if (
        len(visual_bbox) != 4
        or visual_bbox[2] - visual_bbox[0] < 0.45
        or visual_bbox[3] <= visual_bbox[1]
    ):
        return None
    nearby_lines = [
        line
        for line in lines
        if len(line.get("bboxNormalized", [])) == 4
        and float(line["bboxNormalized"][2]) >= visual_bbox[0] - 0.025
        and float(line["bboxNormalized"][0]) <= visual_bbox[2] + 0.025
        and float(line["bboxNormalized"][3]) >= visual_bbox[1] - 0.032
        and float(line["bboxNormalized"][1]) <= visual_bbox[3] + 0.006
    ]
    labeled: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for line in sorted(
        nearby_lines,
        key=lambda value: (
            float(value["bboxNormalized"][1]),
            float(value["bboxNormalized"][0]),
        ),
    ):
        text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
        match = _PARALLEL_TABLE_LABEL.match(text) or _PARALLEL_ALGORITHM_LABEL.match(
            text
        )
        if match is None:
            continue
        family = match.group(1).title()
        label = f"{family} {match.group(2)}"
        if label.casefold() in seen_labels:
            continue
        seen_labels.add(label.casefold())
        labeled.append(
            {
                "family": family,
                "kind": family.casefold(),
                "label": label,
                "text": text,
                "bbox": [float(value) for value in line["bboxNormalized"]],
            }
        )
    if len(labeled) != 2 or labeled[0]["family"] != labeled[1]["family"]:
        return None
    labeled.sort(key=lambda value: (value["bbox"][0] + value["bbox"][2]) / 2)
    visual_center = (visual_bbox[0] + visual_bbox[2]) / 2
    left_center = (labeled[0]["bbox"][0] + labeled[0]["bbox"][2]) / 2
    right_center = (labeled[1]["bbox"][0] + labeled[1]["bbox"][2]) / 2
    if (
        left_center >= visual_center - 0.035
        or right_center <= visual_center + 0.035
        or right_center - left_center < 0.18
    ):
        return None

    # The explicit right label gives a stable column start. Line centres are
    # unsafe here: a wide final header cell can cross the page midpoint while
    # still belonging wholly to the left-hand table.
    right_column_start = labeled[1]["bbox"][0]
    body_lines = [
        line
        for line in nearby_lines
        if float(line["bboxNormalized"][3]) >= visual_bbox[1] - 0.004
        and float(line["bboxNormalized"][1]) <= visual_bbox[3] + 0.004
    ]
    left_lines = [
        line
        for line in body_lines
        if float(line["bboxNormalized"][0]) < right_column_start - 0.012
    ]
    right_lines = [
        line
        for line in body_lines
        if float(line["bboxNormalized"][0]) >= right_column_start - 0.012
    ]
    if not left_lines or not right_lines:
        return None
    left_ink_right = max(float(line["bboxNormalized"][2]) for line in left_lines)
    right_ink_left = min(float(line["bboxNormalized"][0]) for line in right_lines)
    if right_ink_left - left_ink_right < 0.003:
        return None
    separator = (left_ink_right + right_ink_left) / 2
    if separator - visual_bbox[0] < 0.20 or visual_bbox[2] - separator < 0.20:
        return None

    all_lines = sorted(
        nearby_lines,
        key=lambda value: (
            float(value["bboxNormalized"][1]),
            float(value["bboxNormalized"][0]),
        ),
    )
    for candidate in labeled:
        caption_parts = [candidate["text"]]
        current_bottom = candidate["bbox"][3]
        candidate_left = candidate["bbox"][0]
        candidate_is_left = candidate_left < separator
        while True:
            continuation = next(
                (
                    line
                    for line in all_lines
                    if str(line.get("text", "")).strip()
                    and float(line["bboxNormalized"][1]) >= current_bottom - 0.001
                    and float(line["bboxNormalized"][1])
                    <= current_bottom + 0.0045
                    and abs(float(line["bboxNormalized"][0]) - candidate_left)
                    <= 0.04
                    and (float(line["bboxNormalized"][0]) < separator)
                    == candidate_is_left
                    and re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
                    not in caption_parts
                ),
                None,
            )
            if continuation is None:
                break
            continuation_text = re.sub(
                r"\s+", " ", str(continuation.get("text", ""))
            ).strip()
            if _PARALLEL_TABLE_LABEL.match(
                continuation_text
            ) or _PARALLEL_ALGORITHM_LABEL.match(continuation_text):
                break
            caption_parts.append(continuation_text)
            current_bottom = float(continuation["bboxNormalized"][3])
        candidate["captionText"] = _join_parallel_caption_lines(caption_parts)
    return {"separator": separator, "objects": labeled}


def _split_parallel_labeled_visuals(
    source: Path, evidence: dict[str, Any], structure: dict[str, Any]
) -> list[str]:
    """Split two side-by-side tables/algorithms merged into one Docling table."""

    diagnostics = structure.setdefault("doclingDiagnostics", {})
    diagnostics.setdefault("splitParallelLabeledVisuals", [])
    try:
        pdf = fitz.open(source)
    except Exception:
        return []
    evidence_pages = {
        int(page["pageNumber"]): page for page in evidence.get("pages", [])
    }
    split_ids: list[str] = []
    try:
        for page_structure in structure.get("pages", []):
            page_number = int(page_structure["pageNumber"])
            if not 1 <= page_number <= len(pdf):
                continue
            lines = [
                line
                for region in _pdf_text_regions(pdf[page_number - 1])
                for line in region.get("lines", [])
            ]
            blocks = {
                str(block["blockId"]): block
                for block in evidence_pages.get(page_number, {}).get("blocks", [])
            }
            replacements: list[dict[str, Any]] = []
            for visual in page_structure.get("visualObjects", []):
                if visual.get("kind") != "table":
                    replacements.append(visual)
                    continue
                bbox = [float(value) for value in visual.get("bboxNormalized", [])]
                plan = _parallel_labeled_visual_split_plan(lines, bbox)
                if plan is None:
                    replacements.append(visual)
                    continue
                object_id = str(visual["objectId"])
                separator = float(plan["separator"])
                original_caption_ids = [
                    str(value) for value in visual.get("captionBlockIds", [])
                ]
                original_label = str(visual.get("label") or "").strip()
                created: list[dict[str, Any]] = []
                for index, candidate in enumerate(plan["objects"]):
                    clone = copy.deepcopy(visual)
                    label = str(candidate["label"])
                    suffix = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
                    clone["objectId"] = f"{object_id}-split-{suffix}"
                    clone["kind"] = str(candidate["kind"])
                    clone["label"] = label
                    clone["bboxNormalized"] = [
                        bbox[0] if index == 0 else separator,
                        bbox[1],
                        separator if index == 0 else bbox[2],
                        bbox[3],
                    ]
                    matching_caption_ids = [
                        block_id
                        for block_id in original_caption_ids
                        if block_id in blocks
                        and re.search(
                            rf"\b{re.escape(label)}\b",
                            str(blocks[block_id].get("text", "")),
                            re.IGNORECASE,
                        )
                    ]
                    if (
                        not matching_caption_ids
                        and label.casefold() == original_label.casefold()
                    ):
                        matching_caption_ids = original_caption_ids
                    clone["captionBlockIds"] = matching_caption_ids
                    clone["captionTextOverride"] = str(candidate["captionText"])
                    clone.pop("asset", None)
                    clone.pop("bboxPdf", None)
                    warning = (
                        "A Docling table crop containing two explicitly labeled "
                        "parallel objects was split at the source-PDF column gap."
                    )
                    warnings = clone.setdefault("warnings", [])
                    if warning not in warnings:
                        warnings.append(warning)
                    created.append(clone)
                    split_ids.append(str(clone["objectId"]))
                replacements.extend(created)
                diagnostics["splitParallelLabeledVisuals"].append(
                    {
                        "sourceObjectId": object_id,
                        "objectIds": [str(value["objectId"]) for value in created],
                        "separator": round(separator, 6),
                    }
                )
            page_structure["visualObjects"] = replacements
    finally:
        pdf.close()
    return sorted(split_ids)


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
            spans = _partition_provenance_char_spans(
                provenance_values, raw_text
            )
            if spans is not None:
                for provenance, span in zip(provenance_values, spans, strict=True):
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
            segment = _normalize_extracted_text(segment)
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
    record_positions = {
        str(record["blockId"]): index for index, record in enumerate(records)
    }

    def preserve_unrenderable_algorithm(ref: str, reason: str) -> bool:
        targets = [record for record in records if record["ref"] == ref]
        if not targets:
            return False
        preserved = False
        joinable_roles = {
            "abstract",
            "paragraph",
            "list_item",
            "footnote",
            "reference",
        }
        for record in targets:
            text = str(record.get("text", ""))
            if not text.strip():
                continue
            position = record_positions.get(str(record["blockId"]), 0)
            previous = next(
                (
                    candidate
                    for candidate in reversed(records[:position])
                    if candidate["pageNumber"] == record["pageNumber"]
                    and not candidate["furniture"]
                ),
                None,
            )
            is_adjacent_right_fragment = False
            if previous is not None and previous.get("bboxValid") and record.get("bboxValid"):
                previous_bbox = previous["bboxNormalized"]
                record_bbox = record["bboxNormalized"]
                previous_height = max(0.0, previous_bbox[3] - previous_bbox[1])
                record_height = max(0.0, record_bbox[3] - record_bbox[1])
                maximum_height = max(previous_height, record_height)
                height_ratio = (
                    min(previous_height, record_height) / maximum_height
                    if maximum_height > 0
                    else 0.0
                )
                rightward_gap = record_bbox[0] - previous_bbox[2]
                is_adjacent_right_fragment = (
                    -0.002 <= rightward_gap <= 0.012 and height_ratio >= 0.35
                )
            if (
                previous is not None
                and previous["role"] in joinable_roles
                and previous.get("paragraphId")
                and previous.get("sectionId") == record.get("sectionId")
                and _caption_same_line_vertical(previous, record)
                and is_adjacent_right_fragment
            ):
                record["role"] = previous["role"]
                record["sectionId"] = previous.get("sectionId")
                record["paragraphId"] = previous["paragraphId"]
                record["continuesFrom"] = previous["blockId"]
                warning = (
                    "An exact nonempty code fragment with an unrenderable crop "
                    "was joined to its adjacent same-line text block."
                )
            else:
                record["role"] = "verbatim"
                record["paragraphId"] = f"verbatim-{record['sourceBlockId']}"
                record["continuesFrom"] = None
                warning = (
                    "An exact nonempty code fragment with an unrenderable crop "
                    "was preserved as standalone verbatim text."
                )
            record["warnings"].append(f"{warning} Source crop issue: {reason}")
            preserved = True
        return preserved

    for order, ref, collection, item, _furniture_item in traversal:
        kind = _visual_kind(collection, item)
        if kind is None:
            continue
        provenance = next(iter(_provenance(item)), None)
        page_number = _page_number(provenance or {}) or 1
        if provenance is None or page_number not in page_sizes:
            if (
                kind == "algorithm"
                and _raw_text(item).strip()
                and preserve_unrenderable_algorithm(ref, "missing page provenance")
            ):
                document_warnings.append(
                    f"Preserved nonempty {kind} {ref} as exact text because its crop lacked page provenance."
                )
                continue
            document_warnings.append(f"Skipped {kind} {ref}: missing page provenance.")
            continue
        _pdf_bbox, normalized_bbox, bbox_warnings, valid_bbox = _bbox(
            provenance, page_sizes[page_number]
        )
        area = max(0.0, normalized_bbox[2] - normalized_bbox[0]) * max(
            0.0, normalized_bbox[3] - normalized_bbox[1]
        )
        if not valid_bbox or area < 0.0005:
            if (
                kind == "algorithm"
                and _raw_text(item).strip()
                and preserve_unrenderable_algorithm(
                    ref, "invalid or implausibly small crop"
                )
            ):
                document_warnings.append(
                    f"Preserved nonempty {kind} {ref} as exact text because its crop was invalid or implausibly small."
                )
                continue
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

    for record, visual_entry in _align_visual_captions(
        caption_records, visual_entries, explicit_caption_owners
    ):
        assign_caption(record, visual_entry, primary=True)

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

    # A primary label may be outside the visual graph while later caption lines
    # are explicit children.  Attach only caption-labeled, same-owner fragments
    # that form a short geometric chain from the primary caption.  This retains
    # multiline captions without pulling in unrelated text elsewhere in a large
    # picture (for example an internal title at the opposite visual boundary).
    for visual_entry in visual_entries:
        visual_ref = visual_entry["ref"]
        line_records = list(assigned_caption_records[visual_ref])
        if not line_records:
            continue
        explicit_records = list(
            dict.fromkeys(
                record["blockId"]
                for caption_ref in visual_entry["explicitCaptionRefs"]
                for block_id in ref_to_block_ids.get(caption_ref, [])
                if (record := block_by_id[block_id])["pageNumber"]
                == visual_entry["pageNumber"]
                and record["collection"] == "texts"
                and _label(record["item"]) == "caption"
                and _object_label_kind(record["text"]) is None
                and not re.match(r"^\s*\([a-z]\)\s+", record["text"], re.IGNORECASE)
            )
        )
        candidates = [block_by_id[block_id] for block_id in explicit_records]
        changed = True
        while changed:
            changed = False
            for record in candidates:
                if (
                    record["blockId"] in claimed_caption_blocks
                    or record["furniture"]
                    or not record["bboxValid"]
                    or not any(
                        _caption_fragment_connected(record, value)
                        for value in line_records
                    )
                ):
                    continue
                if any(
                    _rectangle_intersection_area(
                        record["bboxNormalized"], existing["bboxNormalized"]
                    )
                    > 0
                    and not _caption_same_line_vertical(record, existing)
                    and len(record["text"]) >= 40
                    and len(existing["text"]) >= 40
                    for existing in line_records
                ):
                    visual_entry["warnings"].append(
                        "Overlapping multiline caption fragments require source-PDF "
                        "line refinement to recover exact interleaving."
                    )
                record["visualCaptionCandidate"] = True
                assign_caption(record, visual_entry, primary=False)
                line_records.append(record)
                changed = True

    def shares_caption_line(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_bbox = left["bboxNormalized"]
        right_bbox = right["bboxNormalized"]
        horizontal_gap = max(
            0.0,
            max(left_bbox[0], right_bbox[0]) - min(left_bbox[2], right_bbox[2]),
        )
        return _caption_same_line_vertical(left, right) and horizontal_gap <= 0.02

    for visual_entry in visual_entries:
        visual_ref = visual_entry["ref"]
        line_records = list(assigned_caption_records[visual_ref])
        if not line_records:
            continue
        changed = True
        while changed:
            changed = False
            for record in page_records[visual_entry["pageNumber"]]:
                reference_height = max(
                    value["bboxNormalized"][3] - value["bboxNormalized"][1]
                    for value in line_records
                )
                record_height = (
                    record["bboxNormalized"][3] - record["bboxNormalized"][1]
                )
                maximum_text_length = max(
                    320, sum(len(value["text"]) for value in line_records) * 2
                )
                if (
                    record["blockId"] in claimed_caption_blocks
                    or record["furniture"]
                    or record["collection"] != "texts"
                    or not record["bboxValid"]
                    or _object_label_kind(record["text"]) is not None
                    or abs(int(record["order"]) - int(line_records[0]["order"])) > 12
                    or record_height > max(0.04, reference_height * 1.35)
                    or len(record["text"]) > maximum_text_length
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

        caption_lines = _caption_line_groups(
            assigned_caption_records[visual_ref]
        )
        for line in caption_lines:
            _normalize_caption_superscripts(line)
        caption_values = [record for line in caption_lines for record in line]

        # If Docling's visual boundary only nicks an external caption, clip the
        # crop back to that boundary.  Requiring horizontal support, a <= 0.02
        # overlap, and extension to the outside edge avoids cutting labels that
        # genuinely belong inside a diagram.
        for caption_record in caption_values:
            caption_bbox = caption_record["bboxNormalized"]
            horizontal_overlap = max(
                0.0,
                min(visual_bbox[2], caption_bbox[2])
                - max(visual_bbox[0], caption_bbox[0]),
            )
            minimum_width = max(
                1e-9,
                min(
                    visual_bbox[2] - visual_bbox[0],
                    caption_bbox[2] - caption_bbox[0],
                ),
            )
            if horizontal_overlap / minimum_width < 0.25:
                continue
            visual_midpoint = (visual_bbox[1] + visual_bbox[3]) / 2
            caption_midpoint = (caption_bbox[1] + caption_bbox[3]) / 2
            bottom_overlap = visual_bbox[3] - caption_bbox[1]
            top_overlap = caption_bbox[3] - visual_bbox[1]
            clipped = False
            if (
                caption_midpoint >= visual_midpoint
                and 0 < bottom_overlap <= 0.02
                and caption_bbox[3] >= visual_bbox[3] - 0.002
                and caption_bbox[1] > visual_bbox[1] + 0.005
            ):
                visual_bbox[3] = caption_bbox[1]
                clipped = True
            elif (
                caption_midpoint <= visual_midpoint
                and 0 < top_overlap <= 0.02
                and caption_bbox[1] <= visual_bbox[1] + 0.002
                and caption_bbox[3] < visual_bbox[3] - 0.005
            ):
                visual_bbox[1] = caption_bbox[3]
                clipped = True
            if clipped:
                warning = (
                    "Visual crop was clipped at a minimally overlapping external caption boundary."
                )
                if warning not in visual_entry["warnings"]:
                    visual_entry["warnings"].append(warning)

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


def _extract_docling_semantics_bounded(
    source: Path,
    work_dir: Path,
    evidence_path: Path,
    structure_path: Path,
    visuals_path: Path,
    *,
    worker_timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Convert one pre-bounded PDF and persist semantic_document inputs."""

    source = Path(source).resolve()
    work_dir = Path(work_dir)
    evidence_path = Path(evidence_path)
    structure_path = Path(structure_path)
    visuals_path = Path(visuals_path)
    source_pages = _validate_docling_source_pdf(source)
    raster_budget = _new_docling_raster_budget()
    raw_document = _run_docling_worker(
        source,
        work_dir,
        timeout_seconds=worker_timeout_seconds,
    )
    _validate_docling_document_limits(raw_document, source_pages=source_pages)
    evidence, structure = docling_document_to_ir(raw_document, source_file=source.name)
    _recover_page_one_front_matter_from_pdf(source, evidence, structure)
    _restore_lexical_linebreak_hyphens_from_pdf(source, evidence, structure)
    _suppress_blank_docling_headings(
        source,
        evidence,
        structure,
        raster_budget=raster_budget,
    )
    _absorb_aligned_figure_panel_headings(source, evidence, structure)
    _demote_mislabeled_docling_headings(evidence, structure)
    _repair_lowercase_body_continuations_from_pdf(source, evidence, structure)
    _absorb_adjacent_equation_signs(evidence, structure)
    _suppress_overlapping_equation_fragments(evidence, structure)
    _reanchor_equations_by_geometry(evidence, structure)
    _recover_overlarge_docling_pictures(
        source,
        evidence,
        structure,
        raster_budget=raster_budget,
    )
    _merge_split_panel_figures(
        source,
        evidence,
        structure,
        raster_budget=raster_budget,
    )
    _split_parallel_labeled_visuals(source, evidence, structure)
    # Visibility recovery can restore prose that Docling originally buried
    # inside a picture node, so canonicalize links only after that pass.
    _recover_pdf_link_annotations(source, evidence, structure)
    caption_overrides = refine_pdf_caption_texts(source, evidence, structure)
    recovered_links_by_page: dict[int, list[str]] = {}
    for value in structure.get("doclingDiagnostics", {}).get(
        "recoveredExternalLinks", []
    ):
        try:
            page_number = int(value.get("pageNumber", 0))
        except (TypeError, ValueError):
            continue
        uri = str(value.get("uri") or "").strip()
        if page_number > 0 and uri:
            recovered_links_by_page.setdefault(page_number, []).append(uri)
    missing_required_overrides: list[str] = []
    for page in structure.get("pages", []):
        page_number = int(page.get("pageNumber", 0))
        for visual in page.get("visualObjects", []):
            object_id = str(visual.get("objectId") or "")
            if override := caption_overrides.get(object_id):
                visual["captionTextOverride"] = _normalize_url_text_with_annotations(
                    override, recovered_links_by_page.get(page_number, [])
                )
                continue
            if override := visual.get("captionTextOverride"):
                visual["captionTextOverride"] = _normalize_url_text_with_annotations(
                    str(override), recovered_links_by_page.get(page_number, [])
                )
            if any(
                "exact interleaving" in str(warning).lower()
                for warning in visual.get("warnings", [])
            ):
                missing_required_overrides.append(object_id)
    structure.setdefault("doclingDiagnostics", {})[
        "missingCaptionTextOverrideObjectIds"
    ] = sorted(set(missing_required_overrides))
    if missing_required_overrides:
        structure.setdefault("warnings", []).append(
            "Source-PDF caption refinement was required for an interleaved caption but did not produce an unambiguous override."
        )
    validate_structure_batch(evidence["pages"], structure)
    visuals = render_visual_objects(
        source,
        structure,
        work_dir / "assets",
        budget=raster_budget,
    )
    for path, value in (
        (evidence_path, evidence),
        (structure_path, structure),
        (visuals_path, visuals),
    ):
        _write_json_atomic(
            path,
            value,
            max_bytes=DOCLING_MAX_DOCUMENT_JSON_BYTES,
        )
    return evidence, structure, visuals


def extract_docling_semantics(
    source: Path,
    work_dir: Path,
    evidence_path: Path,
    structure_path: Path,
    visuals_path: Path,
    *,
    worker_timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Convert a PDF inside the supported per-job resource envelope."""

    timeout_seconds = _resolve_docling_worker_timeout(worker_timeout_seconds)
    with _temporary_docling_resource_limits(timeout_seconds):
        return _extract_docling_semantics_bounded(
            source,
            work_dir,
            evidence_path,
            structure_path,
            visuals_path,
            worker_timeout_seconds=timeout_seconds,
        )
