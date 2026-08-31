from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .docling_adapter import (
    _DOCLING_PARTIAL_EXIT_CODE,
    _DOCLING_RESOURCE_LIMIT_EXIT_CODE,
    DOCLING_MAX_DOCUMENT_JSON_BYTES,
    DoclingPartialConversionError,
    DoclingResourceLimitError,
    convert_pdf_with_docling,
)
from .docling_worker_runtime import write_bounded_json_atomic


def write_json_atomic(
    value: dict[str, Any],
    output_path: Path,
    *,
    max_bytes: int = DOCLING_MAX_DOCUMENT_JSON_BYTES,
) -> None:
    """Write a completed Docling export without exposing a partial JSON file."""

    write_bounded_json_atomic(
        output_path,
        value,
        max_bytes=max_bytes,
        limit_message=f"Docling JSON exceeds {max_bytes} bytes",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m papertrans.docling_worker",
        description="Crash-isolated Docling PDF conversion worker.",
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_json", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        document = convert_pdf_with_docling(args.source)
        write_json_atomic(document, args.output_json)
    except DoclingPartialConversionError as error:
        print(str(error), file=sys.stderr)
        return _DOCLING_PARTIAL_EXIT_CODE
    except DoclingResourceLimitError as error:
        print(str(error), file=sys.stderr)
        return _DOCLING_RESOURCE_LIMIT_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
