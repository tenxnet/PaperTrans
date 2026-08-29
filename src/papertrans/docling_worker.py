from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .docling_adapter import convert_pdf_with_docling


def write_json_atomic(value: dict[str, Any], output_path: Path) -> None:
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
            json.dump(value, handle, ensure_ascii=False, indent=2)
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
    document = convert_pdf_with_docling(args.source)
    write_json_atomic(document, args.output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
