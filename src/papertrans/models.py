from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


ItemKind = Literal["heading", "paragraph", "figure", "table", "equation", "reference"]


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_value(cls, value: Any) -> "BBox":
        return cls(*[round(float(part), 2) for part in value])


@dataclass
class DocumentItem:
    id: str
    kind: ItemKind
    page: int
    order: int
    original: str = ""
    japanese: str = ""
    level: int | None = None
    asset: str | None = None
    caption: str | None = None
    bbox: BBox | None = None
    preserved_terms: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def translatable(self) -> bool:
        return self.kind in {"heading", "paragraph"} and bool(self.original.strip())


@dataclass
class PageIR:
    number: int
    width: float
    height: float
    source_image: str | None = None
    items: list[DocumentItem] = field(default_factory=list)


@dataclass
class DocumentIR:
    version: int
    source_file: str
    source_sha256: str
    title: str
    authors: str
    page_count: int
    pages: list[PageIR]
    glossary: list[dict[str, str]] = field(default_factory=list)
    status: str = "extracted"
    extraction_engine: str = "pymupdf"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DocumentIR":
        pages: list[PageIR] = []
        for page_raw in raw.get("pages", []):
            items: list[DocumentItem] = []
            for item_raw in page_raw.get("items", []):
                value = dict(item_raw)
                if value.get("bbox"):
                    value["bbox"] = BBox(**value["bbox"])
                items.append(DocumentItem(**value))
            pages.append(
                PageIR(
                    number=page_raw["number"],
                    width=page_raw["width"],
                    height=page_raw["height"],
                    source_image=page_raw.get("source_image"),
                    items=items,
                )
            )
        values = dict(raw)
        values["pages"] = pages
        return cls(**values)

    def iter_items(self):
        for page in self.pages:
            yield from page.items

    def translated_count(self) -> int:
        return sum(1 for item in self.iter_items() if item.translatable and item.japanese.strip())

    def translatable_count(self) -> int:
        return sum(1 for item in self.iter_items() if item.translatable)


def relative_asset(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()
