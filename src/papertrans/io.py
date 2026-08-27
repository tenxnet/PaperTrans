from __future__ import annotations

import json
from pathlib import Path

from .models import DocumentIR


def load_document(path: Path) -> DocumentIR:
    return DocumentIR.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_document(document: DocumentIR, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)

