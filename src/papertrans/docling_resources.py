"""Resource admission and OS-limit helpers for Docling PDF conversion."""

from __future__ import annotations

import math
import os
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pymupdf as fitz

from .docling_contract import (
    DoclingAdapterError,
    DoclingResourceLimitError,
)
from .structure import PdfRenderBudget


def resolve_worker_timeout(
    value: float | None,
    *,
    default_seconds: float,
    max_seconds: float,
) -> float:
    """Resolve the worker timeout while enforcing the application maximum."""

    if value is None:
        raw_timeout = os.environ.get("PAPERTRANS_DOCLING_WORKER_TIMEOUT")
        try:
            value = (
                float(raw_timeout)
                if raw_timeout is not None
                else default_seconds
            )
        except ValueError as error:
            raise DoclingAdapterError(
                "PAPERTRANS_DOCLING_WORKER_TIMEOUT must be a positive number"
            ) from error
    if not math.isfinite(value) or value <= 0:
        raise DoclingAdapterError(
            "PAPERTRANS_DOCLING_WORKER_TIMEOUT must be a positive number"
        )
    if value > max_seconds:
        raise DoclingResourceLimitError(
            "PAPERTRANS_DOCLING_WORKER_TIMEOUT must not exceed "
            f"{max_seconds:g} seconds"
        )
    return value


def load_resource_module() -> Any | None:
    """Load the POSIX resource module on supported runtimes."""

    try:
        import resource
    except ImportError:
        return None
    return resource


@contextmanager
def temporary_process_resource_limits(
    timeout_seconds: float,
    *,
    load_resource: Callable[[], Any | None],
    max_memory_bytes: int,
    max_cpu_seconds: int,
    max_output_file_bytes: int,
) -> Iterator[None]:
    """Bound the dedicated parsing process and restore its prior soft limits."""

    resource = load_resource()
    if resource is None:
        raise DoclingResourceLimitError(
            "Docling resource limits require the supported macOS or Linux runtime"
        )
    infinity = resource.RLIM_INFINITY
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu_target = (
        math.ceil(float(usage.ru_utime) + float(usage.ru_stime))
        + min(max_cpu_seconds, math.ceil(timeout_seconds) + 120)
    )
    previous: list[tuple[int, tuple[int, int]]] = []

    def apply_limit(name: str, requested_soft: int, *, required: bool) -> bool:
        limit_name = getattr(resource, name, None)
        if limit_name is None:
            if required:
                raise DoclingResourceLimitError(
                    f"The supported runtime does not expose {name}"
                )
            return False
        soft, hard = resource.getrlimit(limit_name)
        target = int(requested_soft)
        if soft != infinity:
            target = min(target, int(soft))
        if hard != infinity:
            target = min(target, int(hard))
        if target <= 0:
            if required:
                raise DoclingResourceLimitError(
                    f"The current process {name} limit cannot run Docling safely"
                )
            return False
        try:
            resource.setrlimit(limit_name, (target, hard))
        except (OSError, ValueError):
            if required:
                raise
            return False
        previous.append((limit_name, (soft, hard)))
        return True

    try:
        # macOS exposes RLIMIT_AS but rejects finite values. Prefer it where it
        # works, then fall back to the narrower data-segment limit. Structural,
        # JSON, and raster budgets remain the primary cross-platform memory
        # boundary when neither primitive is enforceable.
        if not apply_limit("RLIMIT_AS", max_memory_bytes, required=False):
            apply_limit("RLIMIT_DATA", max_memory_bytes, required=False)
        apply_limit("RLIMIT_CPU", cpu_target, required=True)
        apply_limit("RLIMIT_FSIZE", max_output_file_bytes, required=True)
    except DoclingResourceLimitError:
        for limit_name, limits in reversed(previous):
            with suppress(OSError, ValueError):
                resource.setrlimit(limit_name, limits)
        raise
    except (OSError, ValueError) as error:
        for limit_name, limits in reversed(previous):
            with suppress(OSError, ValueError):
                resource.setrlimit(limit_name, limits)
        raise DoclingResourceLimitError(
            "PaperTrans could not apply the Docling process resource limits"
        ) from error
    try:
        yield
    finally:
        for limit_name, limits in reversed(previous):
            resource.setrlimit(limit_name, limits)


def validate_source_pdf(
    source: Path,
    *,
    max_source_bytes: int,
    max_pages: int,
    max_pdf_objects: int,
    max_page_dimension_points: float,
) -> int:
    """Reject oversized or structurally extreme PDFs before conversion."""

    source = Path(source)
    try:
        source_stat = source.stat()
    except OSError as error:
        raise DoclingAdapterError(f"PDF source is unavailable: {source}") from error
    if not stat.S_ISREG(source_stat.st_mode):
        raise DoclingAdapterError("PDF source must be a regular file")
    if source_stat.st_size > max_source_bytes:
        raise DoclingResourceLimitError(
            f"PDF source exceeds {max_source_bytes} bytes"
        )
    try:
        with source.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise DoclingAdapterError("PDF source does not begin with %PDF-")
    except OSError as error:
        raise DoclingAdapterError(f"PDF source cannot be read: {source}") from error
    try:
        pdf = fitz.open(source)
    except (RuntimeError, ValueError, OSError) as error:
        raise DoclingAdapterError("PyMuPDF could not inspect the PDF source") from error
    try:
        if pdf.needs_pass or pdf.is_encrypted:
            raise DoclingAdapterError("Encrypted PDFs are unsupported")
        if not 1 <= pdf.page_count <= max_pages:
            raise DoclingResourceLimitError(
                f"PDF page count must be between 1 and {max_pages}"
            )
        xref_count = int(pdf.xref_length())
        if xref_count > max_pdf_objects:
            raise DoclingResourceLimitError(
                "PDF object count exceeds the Docling limit "
                f"({xref_count} > {max_pdf_objects})"
            )
        for page_number, page in enumerate(pdf, start=1):
            width = float(page.rect.width)
            height = float(page.rect.height)
            if (
                width <= 0
                or height <= 0
                or width > max_page_dimension_points
                or height > max_page_dimension_points
            ):
                raise DoclingResourceLimitError(
                    f"PDF page {page_number} exceeds the supported dimensions"
                )
        return pdf.page_count
    finally:
        pdf.close()


def validate_document_limits(
    document: Mapping[str, Any],
    *,
    source_pages: int,
    content_collections: Sequence[str],
    collection_values: Callable[[Any], list[tuple[str, Mapping[str, Any]]]],
    max_content_items: int,
    max_text_characters: int,
    max_visual_objects: int,
) -> None:
    """Validate the exported Docling shape before constructing PaperTrans IR."""

    pages = collection_values(document.get("pages", {}))
    if len(pages) != source_pages:
        raise DoclingResourceLimitError(
            "Docling page metadata does not match the bounded source PDF "
            f"({len(pages)} != {source_pages})"
        )
    page_numbers: set[int] = set()
    for key, page in pages:
        try:
            page_number = int(page.get("page_no", page.get("page_number", key)))
        except (TypeError, ValueError) as error:
            raise DoclingResourceLimitError(
                "Docling page metadata contains a non-numeric page number"
            ) from error
        if not 1 <= page_number <= source_pages or page_number in page_numbers:
            raise DoclingResourceLimitError(
                f"Docling page metadata contains invalid page {page_number}"
            )
        page_numbers.add(page_number)

    total_items = len(pages)
    text_characters = 0
    visual_items = 0
    for collection_name in (*content_collections, "groups"):
        items = collection_values(document.get(collection_name, []))
        total_items += len(items)
        if collection_name in {"pictures", "tables"}:
            visual_items += len(items)
        for _key, item in items:
            text = item.get("text", item.get("orig", ""))
            if text is not None:
                text_characters += len(str(text))
    if total_items > max_content_items:
        raise DoclingResourceLimitError(
            "Docling document item count exceeds the job limit "
            f"({total_items} > {max_content_items})"
        )
    if text_characters > max_text_characters:
        raise DoclingResourceLimitError(
            "Docling extracted text exceeds the job limit "
            f"({text_characters} > {max_text_characters})"
        )
    if visual_items > max_visual_objects:
        raise DoclingResourceLimitError(
            "Docling visual count exceeds the job limit "
            f"({visual_items} > {max_visual_objects})"
        )


def new_raster_budget(
    *,
    max_renders: int,
    max_single_pixels: int,
    max_total_pixels: int,
    max_output_bytes: int,
) -> PdfRenderBudget:
    """Build one shared raster budget for an entire Docling extraction."""

    return PdfRenderBudget(
        max_renders=max_renders,
        max_single_pixels=max_single_pixels,
        max_total_pixels=max_total_pixels,
        max_output_bytes=max_output_bytes,
    )
