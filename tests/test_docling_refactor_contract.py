from __future__ import annotations

import papertrans.docling_adapter as adapter
import papertrans.docling_contract as contract


def test_adapter_reexports_shared_docling_error_contract() -> None:
    """Existing callers keep the same adapter-level exception API after extraction."""

    assert adapter.DoclingAdapterError is contract.DoclingAdapterError
    assert adapter.DoclingUnavailableError is contract.DoclingUnavailableError
    assert adapter.DoclingWorkerError is contract.DoclingWorkerError
    assert adapter.DoclingWorkerTimeoutError is contract.DoclingWorkerTimeoutError
    assert adapter.DoclingPartialConversionError is contract.DoclingPartialConversionError
    assert adapter.DoclingResourceLimitError is contract.DoclingResourceLimitError


def test_adapter_reexports_shared_docling_resource_envelope() -> None:
    """Worker and adapter continue to enforce one canonical default envelope."""

    assert adapter.DOCLING_MAX_SOURCE_BYTES == contract.DOCLING_MAX_SOURCE_BYTES
    assert adapter.DOCLING_MAX_PAGES == contract.DOCLING_MAX_PAGES
    assert (
        adapter.DOCLING_MAX_DOCUMENT_JSON_BYTES
        == contract.DOCLING_MAX_DOCUMENT_JSON_BYTES
    )
    assert adapter.DOCLING_MAX_MEMORY_BYTES == contract.DOCLING_MAX_MEMORY_BYTES
    assert (
        adapter.DOCLING_MAX_WORKER_TIMEOUT_SECONDS
        == contract.DOCLING_MAX_WORKER_TIMEOUT_SECONDS
    )

