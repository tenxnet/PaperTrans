from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .constants import BACKEND_ID
from .constants import BABELDOC_VERSION
from .constants import ENGINE_VERSION
from .constants import EXIT_INTERNAL
from .constants import EXIT_PDF_FAILURE
from .constants import EXIT_PROVIDER_FAILURE
from .constants import EXIT_RESOURCE_LIMIT
from .constants import PROTOCOL_VERSION
from .constants import PYMUPDF_VERSION
from .constants import ROLE_FILENAMES
from .constants import UPSTREAM_REVISION
from .contract import ProviderProfile
from .contract import Request
from .errors import PdfFailure
from .errors import ProviderFailure
from .errors import ResourceLimit
from .errors import WorkerError
from .events import EventEmitter
from .stdio import silence_process_stdio


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _regular_file_inside(path_value: str | Path | None, root: Path) -> Path:
    if path_value is None:
        raise PdfFailure("missing_engine_artifact", "translation engine omitted a requested artifact", EXIT_PDF_FAILURE)
    candidate_raw = Path(path_value)
    if candidate_raw.is_symlink():
        raise PdfFailure("unsafe_engine_artifact", "translation engine returned an unsafe artifact", EXIT_PDF_FAILURE)
    try:
        candidate = candidate_raw.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise PdfFailure("missing_engine_artifact", "translation engine artifact is missing", EXIT_PDF_FAILURE) from exc
    if resolved_root not in candidate.parents or not candidate.is_file():
        raise PdfFailure("unsafe_engine_artifact", "translation engine artifact escaped its staging directory", EXIT_PDF_FAILURE)
    return candidate


def _inspect_pdf(path: Path, *, max_pages: int) -> int:
    try:
        with silence_process_stdio():
            import pymupdf

            with pymupdf.open(path) as document:
                if document.needs_pass:
                    raise PdfFailure("encrypted_pdf", "encrypted PDFs are not supported", EXIT_PDF_FAILURE)
                page_count = document.page_count
    except PdfFailure:
        raise
    except Exception as exc:
        raise PdfFailure("invalid_pdf", "PDF structural inspection failed", EXIT_PDF_FAILURE) from exc
    if page_count < 1 or page_count > max_pages:
        raise PdfFailure("page_limit", "PDF page count exceeds the request limit", EXIT_PDF_FAILURE)
    return page_count


def validate_source(path: Path, request: Request) -> int:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PdfFailure("source_unreadable", "source PDF is not readable", EXIT_PDF_FAILURE) from exc
    if path.is_symlink() or not path.is_file():
        raise PdfFailure("source_not_regular", "source PDF must be a regular non-symlink file", EXIT_PDF_FAILURE)
    if metadata.st_size != request.source.bytes:
        raise PdfFailure("source_size_mismatch", "source PDF byte count does not match the request", EXIT_PDF_FAILURE)
    if _sha256(path) != request.source.sha256:
        raise PdfFailure("source_digest_mismatch", "source PDF digest does not match the request", EXIT_PDF_FAILURE)
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise PdfFailure("invalid_pdf_magic", "source does not have a PDF header", EXIT_PDF_FAILURE)
    return _inspect_pdf(path, max_pages=request.limits.max_pages)


def _build_settings(request: Request, profile: ProviderProfile, engine_output: Path) -> Any:
    # These are public pdf2zh-next configuration classes.  BabelDOC internals are
    # deliberately not imported by this adapter.
    from pdf2zh_next.config import OpenAISettings
    from pdf2zh_next.config import SettingsModel

    return SettingsModel(
        report_interval=0.25,
        basic={"debug": False, "gui": False, "warmup": False, "input_files": set()},
        translation={
            "lang_in": request.translation.source_language,
            "lang_out": request.translation.target_language,
            "output": str(engine_output),
            "qps": profile.qps,
            "ignore_cache": True,
            "no_auto_extract_glossary": True,
            "min_text_length": 5,
        },
        pdf={
            "no_mono": "translated_mono_pdf" not in request.outputs,
            "no_dual": "translated_dual_pdf" not in request.outputs,
            "watermark_output_mode": "no_watermark",
            "dual_translate_first": False,
            "translate_table_text": False,
            "skip_scanned_detection": False,
            "auto_enable_ocr_workaround": False,
            "ocr_workaround": False,
            # The common dual-PDF contract maps each source page to the
            # adjacent original/translated pair emitted by BabelDOC's
            # alternating-page mode. The default side-by-side mode keeps one
            # output page per source page and cannot satisfy that contract.
            "use_alternating_pages_dual": True,
        },
        translate_engine_settings=OpenAISettings(
            openai_model=profile.model_id,
            openai_base_url=profile.base_url,
            openai_api_key=profile.api_key,
            openai_timeout=str(request.limits.deadline_seconds),
        ),
    )


def _stage_id(value: Any) -> str:
    stage = value.lower() if isinstance(value, str) else ""
    if "parse" in stage or "intermediate" in stage:
        return "parse_pdf"
    if "translat" in stage:
        return "translate_text"
    if "save" in stage or "render" in stage:
        return "write_pdf"
    return "engine"


def _progress_units(value: Any) -> int | None:
    if type(value) not in {int, float} or not math.isfinite(value):
        return None
    return max(0, min(100_000, round(float(value) * 1000)))


async def _consume_engine(
    request: Request,
    source: Path,
    engine_output: Path,
    profile: ProviderProfile,
    emitter: EventEmitter,
) -> tuple[Any, dict[str, int]]:
    counters = {"upstreamEvents": 0, "progressEvents": 0}
    finish_result: Any = None
    last_progress: tuple[str, int] | None = None
    try:
        with silence_process_stdio():
            # Import and initialize the public engine boundary only after fd
            # 1/2 have been redirected. Some native dependencies write
            # directly to process stdio during import.
            from pdf2zh_next.high_level import do_translate_async_stream

            settings = _build_settings(request, profile, engine_output)
            async with asyncio.timeout(request.limits.deadline_seconds):
                async for event in do_translate_async_stream(settings, source):
                    counters["upstreamEvents"] += 1
                    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                        raise WorkerError("invalid_engine_event", "translation engine emitted an invalid event", EXIT_INTERNAL)
                    event_type = event["type"]
                    if event_type in {"progress_start", "progress_update", "progress_end"}:
                        completed = _progress_units(event.get("overall_progress"))
                        if completed is not None:
                            normalized = (_stage_id(event.get("stage")), completed)
                            if normalized != last_progress:
                                emitter.emit(
                                    "progress",
                                    stage=normalized[0],
                                    completed=completed,
                                    total=100_000,
                                )
                                counters["progressEvents"] += 1
                                last_progress = normalized
                    elif event_type == "error":
                        raise ProviderFailure(
                            "translation_engine_failure",
                            "translation provider or engine reported a failure",
                            EXIT_PROVIDER_FAILURE,
                        )
                    elif event_type == "finish":
                        finish_result = event.get("translate_result")
                        break
    except TimeoutError as exc:
        raise ResourceLimit("deadline_exceeded", "translation deadline exceeded", EXIT_RESOURCE_LIMIT) from exc
    except asyncio.CancelledError as exc:
        raise ResourceLimit("cancelled", "translation was cancelled", EXIT_RESOURCE_LIMIT) from exc
    except WorkerError:
        raise
    except Exception as exc:
        raise ProviderFailure(
            "translation_engine_failure",
            "translation provider or engine failed",
            EXIT_PROVIDER_FAILURE,
        ) from exc
    if finish_result is None:
        raise WorkerError("missing_finish_event", "translation engine ended without a finish event", EXIT_INTERNAL)
    return finish_result, counters


def _copy_pdf(
    source: Path,
    destination: Path,
    *,
    role: str,
    source_pages: int,
    max_pages: int,
    max_output_bytes: int,
) -> dict[str, Any]:
    if source.stat().st_size > max_output_bytes:
        raise ResourceLimit("output_size_limit", "translated PDF exceeds the output limit", EXIT_RESOURCE_LIMIT)
    temporary = destination.with_name(f".{destination.name}.tmp")
    with source.open("rb") as input_file, temporary.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        output_file.flush()
        os.fsync(output_file.fileno())
    os.chmod(temporary, 0o600)
    with temporary.open("rb") as translated_pdf:
        pdf_magic = translated_pdf.read(5)
    if pdf_magic != b"%PDF-":
        temporary.unlink(missing_ok=True)
        raise PdfFailure("invalid_output_pdf", "translated artifact does not have a PDF header", EXIT_PDF_FAILURE)
    expected_pages = source_pages if role == "translated_mono_pdf" else source_pages * 2
    if expected_pages > max_pages:
        temporary.unlink(missing_ok=True)
        raise ResourceLimit(
            "output_page_limit",
            "translated PDF exceeds the request page limit",
            EXIT_RESOURCE_LIMIT,
        )
    output_pages = _inspect_pdf(temporary, max_pages=max_pages)
    if output_pages != expected_pages:
        temporary.unlink(missing_ok=True)
        raise PdfFailure("output_page_mismatch", "translated PDF page count changed unexpectedly", EXIT_PDF_FAILURE)
    os.replace(temporary, destination)
    return {
        "path": destination,
        "sha256": _sha256(destination),
        "bytes": destination.stat().st_size,
        "pageMap": [
            {
                "sourcePage": page,
                "outputPages": [page]
                if role == "translated_mono_pdf"
                else [page * 2 - 1, page * 2],
            }
            for page in range(1, source_pages + 1)
        ],
    }


def _worker_result(
    request: Request,
    artifacts: list[dict[str, Any]],
    page_maps: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build the backend-neutral, exact five-field worker result contract."""
    return {
        "schemaVersion": PROTOCOL_VERSION,
        "runId": request.run_id,
        "sourceSha256": request.source.sha256,
        "artifacts": artifacts,
        "pageMaps": page_maps,
    }


async def execute(
    request: Request,
    source: Path,
    output: Path,
    profile: ProviderProfile,
    emitter: EventEmitter,
) -> None:
    started = time.monotonic()
    emitter.emit("stage", stage="validate_input")
    source_pages = validate_source(source, request)
    (output / "artifacts").mkdir(mode=0o700)
    engine_output = output / ".engine"
    engine_output.mkdir(mode=0o700)
    emitter.emit("stage", stage="translate")
    try:
        finish, counters = await _consume_engine(
            request, source, engine_output, profile, emitter
        )
        emitter.emit("stage", stage="collect_artifacts")
        upstream_paths = {
            "translated_mono_pdf": getattr(finish, "no_watermark_mono_pdf_path", None)
            or getattr(finish, "mono_pdf_path", None),
            "translated_dual_pdf": getattr(finish, "no_watermark_dual_pdf_path", None)
            or getattr(finish, "dual_pdf_path", None),
        }
        artifacts: list[dict[str, Any]] = []
        page_maps: dict[str, list[dict[str, Any]]] = {}
        aggregate_bytes = 0
        for role in request.outputs:
            engine_path = _regular_file_inside(upstream_paths[role], engine_output)
            collected = _copy_pdf(
                engine_path,
                output / ROLE_FILENAMES[role],
                role=role,
                source_pages=source_pages,
                max_pages=(
                    request.limits.max_pages * 2
                    if role == "translated_dual_pdf"
                    else request.limits.max_pages
                ),
                max_output_bytes=min(
                    request.limits.max_output_bytes, request.source.bytes * 5
                ),
            )
            aggregate_bytes += collected["bytes"]
            if aggregate_bytes > request.limits.max_output_bytes:
                raise ResourceLimit(
                    "aggregate_output_size_limit",
                    "translated artifacts exceed the aggregate output limit",
                    EXIT_RESOURCE_LIMIT,
                )
            artifact = {
                "role": role,
                "path": ROLE_FILENAMES[role],
                "mediaType": "application/pdf",
                "sha256": collected["sha256"],
                "bytes": collected["bytes"],
            }
            artifacts.append(artifact)
            page_maps[role] = collected["pageMap"]

        report_path = output / ROLE_FILENAMES["backend_report"]
        total_seconds = getattr(finish, "total_seconds", None)
        peak_memory = getattr(finish, "peak_memory_usage", None)
        backend_report = {
            "schemaVersion": PROTOCOL_VERSION,
            "runId": request.run_id,
            "backendId": BACKEND_ID,
            "backend": {
                "engineVersion": ENGINE_VERSION,
                "babeldocVersion": BABELDOC_VERSION,
                "pymupdfVersion": PYMUPDF_VERSION,
                "upstreamRevision": UPSTREAM_REVISION,
            },
            "sourcePages": source_pages,
            "engineEventCounts": counters,
            "engineTotalSeconds": total_seconds
            if type(total_seconds) in {int, float} and math.isfinite(total_seconds)
            else None,
            "enginePeakMemory": peak_memory
            if type(peak_memory) in {int, float} and math.isfinite(peak_memory)
            else None,
            "durationMilliseconds": round((time.monotonic() - started) * 1000),
        }
        _atomic_json(report_path, backend_report)
        report_artifact = {
            "role": "backend_report",
            "path": ROLE_FILENAMES["backend_report"],
            "mediaType": "application/json",
            "sha256": _sha256(report_path),
            "bytes": report_path.stat().st_size,
        }
        aggregate_bytes += report_artifact["bytes"]
        if aggregate_bytes > request.limits.max_output_bytes:
            raise ResourceLimit(
                "aggregate_output_size_limit",
                "translated artifacts exceed the aggregate output limit",
                EXIT_RESOURCE_LIMIT,
            )
        artifacts.append(report_artifact)
        shutil.rmtree(engine_output)

        result = _worker_result(request, artifacts, page_maps)
        _atomic_json(output / "worker-result.json", result)
        for artifact in artifacts:
            emitter.emit("artifact", **artifact)
        emitter.emit("completed")
    except BaseException:
        if engine_output.exists():
            shutil.rmtree(engine_output, ignore_errors=True)
        raise
