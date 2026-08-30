from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
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


def write_json_atomic(
    value: dict[str, Any],
    output_path: Path,
    *,
    max_bytes: int = DOCLING_MAX_DOCUMENT_JSON_BYTES,
) -> None:
    """Write a completed Docling export without exposing a partial JSON file."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            encoder = json.JSONEncoder(
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            written = 0
            for chunk in encoder.iterencode(value):
                encoded_size = len(chunk.encode("utf-8"))
                if written + encoded_size + 1 > max_bytes:
                    raise DoclingResourceLimitError(
                        f"Docling JSON exceeds {max_bytes} bytes"
                    )
                handle.write(chunk)
                written += encoded_size
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


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
