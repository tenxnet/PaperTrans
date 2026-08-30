"""Shared exceptions and security envelope for the Docling integration.

This module deliberately has no PaperTrans adapter imports.  The isolated worker,
resource guards, and the public adapter can therefore share one error hierarchy
without creating an import cycle.
"""

from __future__ import annotations


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


class DoclingResourceLimitError(DoclingAdapterError):
    """Raised before a Docling job exceeds an application resource ceiling."""


DOCLING_WORKER_LOG_LIMIT_BYTES = 256 * 1024
DOCLING_PARTIAL_EXIT_CODE = 75
DOCLING_RESOURCE_LIMIT_EXIT_CODE = 76
DOCLING_DOCUMENT_TIMEOUT_SECONDS = 10 * 60.0
DOCLING_WORKER_TIMEOUT_SECONDS = DOCLING_DOCUMENT_TIMEOUT_SECONDS + 30.0
DOCLING_MAX_SOURCE_BYTES = 50 * 1024 * 1024
DOCLING_MAX_PAGES = 300
DOCLING_MAX_PDF_OBJECTS = 250_000
DOCLING_MAX_PAGE_DIMENSION_POINTS = 14_400.0
DOCLING_MAX_DOCUMENT_JSON_BYTES = 64 * 1024 * 1024
DOCLING_MAX_CONTENT_ITEMS = 50_000
DOCLING_MAX_TEXT_CHARACTERS = 32 * 1024 * 1024
DOCLING_MAX_VISUAL_OBJECTS = 2_000
DOCLING_MAX_WORKER_TIMEOUT_SECONDS = 15 * 60.0
DOCLING_MAX_PARSER_THREADS = 4
DOCLING_MAX_MEMORY_BYTES = 6 * 1024 * 1024 * 1024
DOCLING_MAX_CPU_SECONDS = 15 * 60
DOCLING_MAX_OUTPUT_FILE_BYTES = 256 * 1024 * 1024
DOCLING_MAX_RASTER_RENDERS = 4_000
DOCLING_MAX_SINGLE_RASTER_PIXELS = 25_000_000
DOCLING_MAX_TOTAL_RASTER_PIXELS = 150_000_000
DOCLING_MAX_RASTER_OUTPUT_BYTES = 256 * 1024 * 1024
DOCLING_MEMORY_POLL_SECONDS = 0.1
DOCLING_CONTENT_COLLECTIONS = (
    "texts",
    "pictures",
    "tables",
    "key_value_items",
    "form_items",
    "field_regions",
    "field_items",
)
