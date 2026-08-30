"""Deterministic Markdown serialization for PaperTrans document models.

The serializer in this module deliberately knows nothing about PDF extraction,
semantic reconstruction, or arXiv HTML.  Source-specific adapters only have to
materialize a sequence of :class:`MarkdownBlock` values.  This keeps the
Markdown rules in one place and leaves room for additional adapters without
adding source-format branches to the serializer.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Mapping, Sequence
from urllib.parse import quote, urlsplit, urlunsplit

from .models import DocumentIR


MarkdownBlockKind = Literal[
    "heading",
    "paragraph",
    "list_item",
    "figure",
    "table",
    "equation",
    "reference",
    "code",
]


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    """A small, source-neutral block accepted by the Markdown serializer.

    ``text`` is already materialized content (for example, Japanese when a
    translation is present).  ``rows`` represents a GitHub-flavoured Markdown
    table whose first row is the header.  Figure, table, and equation blocks
    may instead point at a localized ``asset``.  ``level`` is a heading level
    for headings and a one-based nesting depth for list items.  The default
    treats text and captions as untrusted literals.  ``text_is_markdown`` is an
    explicit adapter contract for already-validated inline Markdown; it never
    relaxes escaping for image alt text, anchors, or asset paths.  It does
    apply to table cells so source adapters can preserve validated inline math
    and links there as well.
    """

    kind: MarkdownBlockKind
    text: str = ""
    level: int | None = None
    anchor: str | None = None
    asset: str | Path | None = None
    caption: str | None = None
    label: str | None = None
    ordered: bool = False
    start: int | None = None
    language: str | None = None
    rows: tuple[tuple[str, ...], ...] = ()
    text_is_markdown: bool = False


_INLINE_ESCAPE_RE = re.compile(r"([\\`*_[\]{}<>|~&$])")
_BLOCK_MARKER_RE = re.compile(r"^(?P<indent>[ \t]{0,3})(?P<marker>#{1,6}|>|[+-])(?=\s)")
_ORDERED_MARKER_RE = re.compile(r"^(?P<indent>[ \t]{0,3})(?P<number>\d+)(?P<marker>[.)])(?=\s)")
_SETEXT_OR_RULE_RE = re.compile(r"^[ \t]*(?P<marker>=+|-+)[ \t]*$")
_LEADING_WHITESPACE_RE = re.compile(r"^[ \t]+")
_ANCHOR_UNSAFE_RE = re.compile(r"[^\w.:-]+", re.UNICODE)
_LANGUAGE_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_+.-]+")
_UNSAFE_MATH_MARKDOWN_RE = re.compile(
    r"(?:\$|`|!\[|\[[^\]\n]*\]\(|<\s*/?\s*[A-Za-z][^>\n]*>|"
    r"^[ \t]{0,3}(?:#{1,6}(?=\s)|>|[-+*](?=\s)|\d+[.)](?=\s)))",
    re.MULTILINE,
)
_UNSAFE_DISPLAY_MATH_MARKDOWN_RE = re.compile(
    r"(?:\$\$|`|!\[|\[[^\]\n]*\]\(|<\s*/?\s*[A-Za-z][^>\n]*>|"
    r"^[ \t]{0,3}(?:#{1,6}(?=\s)|>|[-+*](?=\s)|\d+[.)](?=\s)))",
    re.MULTILINE,
)
_EXTERNAL_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def preferred_text(value: Any) -> str:
    """Return non-blank Japanese text, falling back to the original text.

    Both mapping-based semantic units and attribute-based legacy items are
    accepted so adapters share exactly the same selection rule.
    """

    if isinstance(value, Mapping):
        japanese = value.get("japanese", "")
        original = value.get("original", value.get("text", ""))
    else:
        japanese = getattr(value, "japanese", "")
        original = getattr(value, "original", getattr(value, "text", ""))
    japanese_text = "" if japanese is None else str(japanese).strip()
    if japanese_text:
        return japanese_text
    return "" if original is None else str(original).strip()


def escape_markdown_text(text: str) -> str:
    """Escape source text so it cannot introduce Markdown structure.

    The punctuation that is meaningful inline is escaped everywhere.  Block
    markers are escaped only at line starts, which keeps ordinary prose such as
    ``state-of-the-art`` readable while preventing accidental headings, lists,
    block quotes, thematic rules, links, and raw HTML.
    """

    escaped_lines: list[str] = []
    for raw_line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        generated_indent = ""
        prefix_match = _LEADING_WHITESPACE_RE.match(raw_line)
        if prefix_match:
            column = 0
            for character in prefix_match.group(0):
                column += 4 - (column % 4) if character == "\t" else 1
            if column >= 4:
                # Four leading spaces (or a tab) would turn a literal line into
                # an indented code block.  Character references retain the
                # visible indentation without introducing block structure.
                generated_indent = "&#32;" * column
                raw_line = raw_line[prefix_match.end() :]

        line = _INLINE_ESCAPE_RE.sub(r"\\\1", raw_line)
        line = _BLOCK_MARKER_RE.sub(
            lambda match: f"{match.group('indent')}\\{match.group('marker')}", line
        )
        line = _ORDERED_MARKER_RE.sub(
            lambda match: (
                f"{match.group('indent')}{match.group('number')}\\{match.group('marker')}"
            ),
            line,
        )
        if _SETEXT_OR_RULE_RE.fullmatch(line):
            marker_start = len(line) - len(line.lstrip(" \t"))
            line = f"{line[:marker_start]}\\{line[marker_start:]}"
        escaped_lines.append(generated_indent + line)
    return "\n".join(escaped_lines)


def normalize_asset_path(
    asset: str | Path,
    *,
    relative_to: str | Path | None = None,
) -> str:
    """Return a deterministic URL-safe asset reference.

    Local assets must be relative in the emitted Markdown.  An absolute path
    can be supplied only together with ``relative_to``; this makes accidental
    leakage of workstation paths impossible.  HTTP(S) and data URLs are kept
    as URLs for future adapters, while localized PaperTrans assets remain
    relative paths such as ``assets/figure-1.png``.
    """

    raw = str(asset).strip().replace("\\", "/")
    if not raw:
        raise ValueError("asset path must not be blank")

    try:
        parsed = urlsplit(raw)
    except ValueError as error:
        raise ValueError(f"invalid asset URL: {raw}") from error
    if parsed.scheme:
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError(f"unsupported asset URL scheme: {parsed.scheme}")
        return normalize_link_destination(raw, allowed_schemes={"http", "https"})

    candidate = Path(raw)
    if candidate.is_absolute():
        if relative_to is None:
            raise ValueError("absolute asset paths require relative_to")
        base = Path(relative_to).resolve()
        resolved = candidate.resolve()
        try:
            resolved.relative_to(base)
        except ValueError as error:
            raise ValueError("asset path must stay below relative_to") from error
        raw = os.path.relpath(resolved, start=base).replace(os.sep, "/")

    normalized = posixpath.normpath(raw)
    normalized_path = PurePosixPath(normalized)
    if (
        normalized in {"", ".", ".."}
        or normalized_path.is_absolute()
        or (normalized_path.parts and normalized_path.parts[0] == "..")
    ):
        raise ValueError("asset path must resolve to a relative file")
    return quote(normalized, safe="/%:@!$&'*+,;=-._~")


def normalize_link_destination(
    value: str,
    *,
    allowed_schemes: set[str] | None = None,
) -> str:
    """Encode a URL so it cannot terminate a Markdown link destination."""

    raw = str(value).strip()
    if not raw:
        raise ValueError("link destination must not be blank")
    schemes = {scheme.lower() for scheme in (allowed_schemes or {"http", "https", "mailto"})}
    try:
        parsed = urlsplit(raw)
    except ValueError as error:
        raise ValueError(f"invalid link destination: {raw}") from error
    scheme = parsed.scheme.lower()
    if scheme and scheme not in schemes:
        raise ValueError(f"unsupported link URL scheme: {parsed.scheme}")
    if scheme in {"http", "https"} and not parsed.netloc:
        raise ValueError(f"{scheme} link destination requires a host")
    netloc = quote(parsed.netloc, safe="%:@[]")
    path = quote(parsed.path, safe="/%:@!$&'*+,;=-._~")
    query = quote(parsed.query, safe="/%?:@!$&'*+,;=-._~")
    fragment = quote(parsed.fragment, safe="/%?:@!$&'*+,;=-._~")
    return urlunsplit((parsed.scheme, netloc, path, query, fragment))


def markdown_with_external_links(text: str) -> str:
    """Escape untrusted inline text and materialize canonical HTTP links."""

    source = str(text)
    rendered: list[str] = []
    cursor = 0
    for match in _EXTERNAL_URL_RE.finditer(source):
        rendered.append(escape_markdown_text(source[cursor : match.start()]))
        value = match.group(0)
        trailing = ""
        while value and value[-1] in ".,;:":
            trailing = value[-1] + trailing
            value = value[:-1]
        while value.endswith(")") and value.count("(") < value.count(")"):
            trailing = ")" + trailing
            value = value[:-1]
        try:
            destination = normalize_link_destination(value)
        except ValueError:
            rendered.append(escape_markdown_text(match.group(0)))
        else:
            rendered.append(
                f"[{escape_markdown_text(value)}]({destination})"
                + escape_markdown_text(trailing)
            )
        cursor = match.end()
    rendered.append(escape_markdown_text(source[cursor:]))
    return "".join(rendered)


def is_safe_math_source(value: str) -> bool:
    """Return whether TeX can be placed inside Markdown math delimiters safely."""

    return not _UNSAFE_MATH_MARKDOWN_RE.search(str(value))


def _unescaped_character_positions(value: str, character: str) -> list[int]:
    positions: list[int] = []
    for index, candidate in enumerate(value):
        if candidate != character:
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and value[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            positions.append(index)
    return positions


def is_safe_display_math_source(value: str) -> bool:
    """Return whether TeX is safe between line-delimited ``$$`` markers.

    A balanced pair of single dollar signs is valid inside TeX text commands
    and cannot close the surrounding display block.  Double dollars and all
    other Markdown-active constructs remain forbidden.
    """

    source = str(value)
    if _UNSAFE_DISPLAY_MATH_MARKDOWN_RE.search(source):
        return False
    dollars = _unescaped_character_positions(source, "$")
    return len(dollars) % 2 == 0


def normalize_anchor_id(value: str) -> str:
    """Map a source identifier to the explicit anchor form emitted by this module."""

    normalized = _ANCHOR_UNSAFE_RE.sub("-", str(value).strip()).strip("-")
    return normalized or "block"


def _unique_anchor(value: str, used: dict[str, int]) -> str:
    base = normalize_anchor_id(value)
    count = used.get(base, 0) + 1
    used[base] = count
    return base if count == 1 else f"{base}-{count}"


def _image_markdown(block: MarkdownBlock) -> str:
    if block.asset is None:
        return ""
    alt = block.label or block.caption or block.text or block.kind.capitalize()
    alt_text = escape_markdown_text(str(alt)).replace("\n", " ")
    asset = normalize_asset_path(block.asset)
    return f"![{alt_text}]({asset})"


def _block_text(block: MarkdownBlock, value: str | None = None) -> str:
    text = block.text if value is None else value
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return normalized if block.text_is_markdown else escape_markdown_text(normalized)


def _caption_markdown(block: MarkdownBlock) -> str:
    if not block.caption:
        return ""
    caption = _block_text(block, str(block.caption).strip())
    # A trusted Markdown caption may already contain emphasis, math, links, or
    # citations.  Adding a generated emphasis wrapper could invalidate nesting.
    return caption if block.text_is_markdown else f"*{caption}*"


def _serialize_table(block: MarkdownBlock) -> str:
    if block.rows:
        width = max(len(row) for row in block.rows)
        if width == 0:
            return _caption_markdown(block)

        def table_row(row: Sequence[str]) -> str:
            cells = list(row) + [""] * (width - len(row))
            rendered = [_block_text(block, str(cell)).replace("\n", "<br>") for cell in cells]
            return f"| {' | '.join(rendered)} |"

        lines = [table_row(block.rows[0]), f"| {' | '.join(['---'] * width)} |"]
        lines.extend(table_row(row) for row in block.rows[1:])
        table = "\n".join(lines)
        caption = _caption_markdown(block)
        return f"{caption}\n\n{table}" if caption else table

    image = _image_markdown(block)
    caption = _caption_markdown(block)
    if image and caption:
        return f"{image}\n\n{caption}"
    if image or caption:
        return image or caption
    return _block_text(block)


def _serialize_equation(block: MarkdownBlock) -> str:
    image = _image_markdown(block)
    caption = _caption_markdown(block)
    if image:
        return f"{image}\n\n{caption}" if caption else image
    equation = block.text.strip()
    if not equation:
        return caption
    if is_safe_display_math_source(equation):
        rendered = f"$$\n{equation}\n$$"
    else:
        rendered = _serialize_code(MarkdownBlock(kind="code", text=equation, language="tex"))
    return f"{rendered}\n\n{caption}" if caption else rendered


def _serialize_code(block: MarkdownBlock) -> str:
    code = str(block.text).replace("\r\n", "\n").replace("\r", "\n")
    if not code.strip():
        # Algorithm visuals can carry only a rendered asset when no textual
        # transcription was recovered.  Preserve that visual instead of
        # emitting an empty code fence; a caption remains useful on its own.
        image = _image_markdown(block)
        caption = _caption_markdown(block)
        if image and caption:
            return f"{image}\n\n{caption}"
        return image or caption

    longest_run = max((len(match.group(0)) for match in re.finditer(r"`+", code)), default=0)
    fence = "`" * max(3, longest_run + 1)
    language = _LANGUAGE_UNSAFE_RE.sub("-", (block.language or "").strip()).strip("-")
    body = code.rstrip("\n")
    return f"{fence}{language}\n{body}\n{fence}"


def _serialize_list_item(block: MarkdownBlock) -> str:
    depth = max(1, int(block.level or 1))
    indent = "    " * (depth - 1)
    number = max(1, int(block.start or 1))
    marker = f"{number}." if block.ordered else "-"
    text = _block_text(block)
    continuation = f"\n{indent}   "
    return f"{indent}{marker} {text.replace(chr(10), continuation)}".rstrip()


def _serialize_block(block: MarkdownBlock) -> str:
    if block.kind == "heading":
        level = min(6, max(1, int(block.level or 2)))
        return f"{'#' * level} {_block_text(block)}".rstrip()
    if block.kind in {"paragraph", "reference"}:
        return _block_text(block)
    if block.kind == "list_item":
        return _serialize_list_item(block)
    if block.kind == "figure":
        image = _image_markdown(block)
        caption = _caption_markdown(block)
        if image and caption:
            return f"{image}\n\n{caption}"
        return image or caption or _block_text(block)
    if block.kind == "table":
        return _serialize_table(block)
    if block.kind == "equation":
        return _serialize_equation(block)
    if block.kind == "code":
        return _serialize_code(block)
    raise ValueError(f"unsupported Markdown block kind: {block.kind}")


def serialize_markdown(blocks: Iterable[MarkdownBlock]) -> str:
    """Serialize blocks into deterministic UTF-8 Markdown text.

    A non-empty result always has exactly one trailing newline.  Consecutive
    list items stay in one Markdown list; all other blocks are separated by one
    blank line.  Duplicate source anchors receive stable ``-2``, ``-3``, ...
    suffixes.
    """

    rendered: list[tuple[MarkdownBlockKind, str]] = []
    used_anchors: dict[str, int] = {}
    for block in blocks:
        body = _serialize_block(block).strip("\n")
        if block.anchor:
            anchor = _unique_anchor(block.anchor, used_anchors)
            anchor_markup = f'<a id="{anchor}"></a>'
            if body and block.kind == "list_item":
                first_line, separator, remainder = body.partition("\n")
                marker = re.match(r"^(?P<prefix>\s*(?:-|\d+\.)\s+)(?P<text>.*)$", first_line)
                if marker:
                    first_line = (
                        f"{marker.group('prefix')}{anchor_markup}{marker.group('text')}"
                    )
                    body = first_line + (separator + remainder if separator else "")
                else:
                    body = f"{anchor_markup}\n{body}"
            else:
                body = f"{anchor_markup}\n\n{body}" if body else anchor_markup
        if body:
            rendered.append((block.kind, body))

    if not rendered:
        return ""
    pieces = [rendered[0][1]]
    for previous, current in zip(rendered, rendered[1:]):
        separator = "\n" if previous[0] == current[0] == "list_item" else "\n\n"
        pieces.append(separator + current[1])
    return "".join(pieces).rstrip() + "\n"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mapping_rows(value: Any) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    rows: list[tuple[str, ...]] = []
    for row in value:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
            return ()
        rows.append(tuple("" if cell is None else str(cell) for cell in row))
    return tuple(rows)


def document_ir_to_markdown_blocks(
    document: DocumentIR,
    *,
    asset_base: str | Path | None = None,
) -> list[MarkdownBlock]:
    """Adapt the legacy :class:`DocumentIR` model to Markdown blocks."""

    blocks: list[MarkdownBlock] = [
        MarkdownBlock(kind="heading", text=str(document.title).strip(), level=1, anchor="paper-title")
    ]
    if str(document.authors).strip():
        blocks.append(MarkdownBlock(kind="paragraph", text=str(document.authors).strip()))

    pages = sorted(enumerate(document.pages), key=lambda pair: (pair[1].number, pair[0]))
    for _, page in pages:
        items = sorted(
            enumerate(page.items),
            key=lambda pair: (pair[1].order, str(pair[1].id), pair[0]),
        )
        for _, item in items:
            raw_kind = str(item.kind)
            kind: MarkdownBlockKind
            if raw_kind in {"abstract", "footnote"}:
                kind = "paragraph"
            elif raw_kind in {"list", "list_item"}:
                kind = "list_item"
            elif raw_kind in {"algorithm", "verbatim"}:
                kind = "code"
            elif raw_kind in {
                "heading",
                "paragraph",
                "figure",
                "table",
                "equation",
                "reference",
                "code",
            }:
                kind = raw_kind  # type: ignore[assignment]
            else:
                # A future legacy item should remain readable rather than
                # disappearing merely because its precise kind is unknown.
                kind = "paragraph"

            asset: str | Path | None = item.asset
            if asset is not None:
                asset = normalize_asset_path(asset, relative_to=asset_base)
            blocks.append(
                MarkdownBlock(
                    kind=kind,
                    text=preferred_text(item),
                    level=item.level,
                    anchor=_optional_text(item.id),
                    asset=asset,
                    caption=_optional_text(item.caption),
                    label=_optional_text(getattr(item, "label", None)),
                    ordered=bool(getattr(item, "ordered", False)),
                    start=_optional_int(getattr(item, "start", None)),
                    language=_optional_text(getattr(item, "language", None)),
                    rows=_mapping_rows(getattr(item, "rows", ())),
                )
            )
    return blocks


def _semantic_kind(value: Mapping[str, Any]) -> MarkdownBlockKind:
    raw = str(value.get("kind", "paragraph"))
    if raw in {"abstract", "footnote", "metadata", "author", "affiliation"}:
        return "paragraph"
    if raw in {"list", "list_item"}:
        return "list_item"
    if raw in {"algorithm", "verbatim"}:
        return "code"
    if raw in {
        "heading",
        "paragraph",
        "figure",
        "table",
        "equation",
        "reference",
        "code",
    }:
        return raw  # type: ignore[return-value]
    return "paragraph"


def _semantic_block(
    value: Mapping[str, Any],
    *,
    item_type: str | None = None,
    asset_base: str | Path | None = None,
) -> MarkdownBlock:
    kind = _semantic_kind(value)
    asset_value = value.get("asset")
    asset: str | Path | None = None
    if asset_value:
        asset = normalize_asset_path(str(asset_value), relative_to=asset_base)

    anchor = value.get("anchorId") or value.get("id")
    if item_type == "visual" and value.get("objectId"):
        anchor = f"visual-{value['objectId']}"
    elif kind == "reference":
        reference_id = value.get("referenceLabel") or value.get("id")
        if reference_id:
            anchor = f"ref-{reference_id}"

    list_type = str(value.get("listType", value.get("marker", ""))).lower()
    ordered = bool(value.get("ordered")) or list_type in {"ordered", "ol", "number", "numbered"}
    rows = _mapping_rows(value.get("rows", value.get("table", ())))
    raw_text = preferred_text(value)
    raw_caption = _optional_text(value.get("caption"))
    contains_external_url = bool(
        _EXTERNAL_URL_RE.search(raw_text)
        or (raw_caption and _EXTERNAL_URL_RE.search(raw_caption))
        or any(_EXTERNAL_URL_RE.search(cell) for row in rows for cell in row)
    )
    if contains_external_url:
        rendered_text = (
            raw_text
            if kind in {"code", "equation"}
            else markdown_with_external_links(raw_text)
        )
        rendered_caption = (
            markdown_with_external_links(raw_caption) if raw_caption else None
        )
        rows = tuple(
            tuple(markdown_with_external_links(cell) for cell in row)
            for row in rows
        )
    else:
        rendered_text = raw_text
        rendered_caption = raw_caption
    return MarkdownBlock(
        kind=kind,
        text=rendered_text,
        level=_optional_int(value.get("level", value.get("listLevel", value.get("depth")))),
        anchor=_optional_text(anchor),
        asset=asset,
        caption=rendered_caption,
        label=_optional_text(value.get("label")),
        ordered=ordered,
        start=_optional_int(value.get("start")),
        language=_optional_text(value.get("language", value.get("lang"))),
        rows=rows,
        text_is_markdown=contains_external_url,
    )


def semantic_v3_to_markdown_blocks(
    document: Mapping[str, Any],
    *,
    asset_base: str | Path | None = None,
) -> list[MarkdownBlock]:
    """Adapt a semantic v3 document dictionary to Markdown blocks."""

    version = document.get("version")
    if version is not None and int(version) != 3:
        raise ValueError(f"semantic Markdown adapter requires version 3, got {version!r}")

    blocks: list[MarkdownBlock] = []
    title = document.get("title", {})
    if isinstance(title, Mapping):
        blocks.append(
            MarkdownBlock(
                kind="heading",
                text=preferred_text(title),
                level=1,
                anchor=_optional_text(title.get("anchorId")) or "paper-title",
            )
        )
    elif title:
        blocks.append(MarkdownBlock(kind="heading", text=str(title).strip(), level=1, anchor="paper-title"))

    front_matter = document.get("frontMatter", {})
    if isinstance(front_matter, Mapping):
        for field in ("authors", "affiliations", "metadata"):
            entries = front_matter.get(field, [])
            if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
                entries = [entries]
            texts = [preferred_text(entry) for entry in entries]
            texts = [text for text in texts if text]
            if texts:
                front_text = " · ".join(texts)
                contains_external_url = bool(_EXTERNAL_URL_RE.search(front_text))
                blocks.append(
                    MarkdownBlock(
                        kind="paragraph",
                        text=(
                            markdown_with_external_links(front_text)
                            if contains_external_url
                            else front_text
                        ),
                        text_is_markdown=contains_external_url,
                    )
                )

    sections = document.get("sections", [])
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes, bytearray)):
        raise ValueError("semantic document sections must be a sequence")
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        section_title = section.get("title", {})
        title_text = preferred_text(section_title) if isinstance(section_title, Mapping) else str(section_title or "").strip()
        number = _optional_text(section.get("number"))
        if number and title_text:
            title_text = f"{number} {title_text}"
        if title_text and not section.get("syntheticUnheaded"):
            section_level = _optional_int(section.get("level")) or 1
            blocks.append(
                MarkdownBlock(
                    kind="heading",
                    text=title_text,
                    level=min(6, section_level + 1),
                    anchor=_optional_text(section.get("id")),
                )
            )

        content = section.get("content", [])
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes, bytearray)):
            continue
        indexed_content = list(enumerate(content))

        def content_key(pair: tuple[int, Any]) -> tuple[int, float, int, int]:
            index, item = pair
            if not isinstance(item, Mapping):
                return (1, float(index), 1, index)
            position = item.get("position")
            if isinstance(position, (int, float)) and not isinstance(position, bool):
                visual_first = 0 if item.get("type") == "visual" else 1
                return (0, float(position), visual_first, index)
            return (1, float(index), 1, index)

        for _, item in sorted(indexed_content, key=content_key):
            if not isinstance(item, Mapping):
                continue
            raw_value = item.get("value", item)
            if not isinstance(raw_value, Mapping):
                continue
            blocks.append(
                _semantic_block(
                    raw_value,
                    item_type=_optional_text(item.get("type")),
                    asset_base=asset_base,
                )
            )
    return blocks


def document_ir_to_markdown(
    document: DocumentIR,
    *,
    asset_base: str | Path | None = None,
) -> str:
    """Render a legacy ``DocumentIR`` as Markdown text."""

    return serialize_markdown(document_ir_to_markdown_blocks(document, asset_base=asset_base))


def semantic_v3_to_markdown(
    document: Mapping[str, Any],
    *,
    asset_base: str | Path | None = None,
) -> str:
    """Render a semantic v3 dictionary as Markdown text."""

    return serialize_markdown(semantic_v3_to_markdown_blocks(document, asset_base=asset_base))


# Source-neutral aliases make adapter integration discoverable without hiding
# the schema version that the concrete implementation supports.
semantic_document_to_markdown_blocks = semantic_v3_to_markdown_blocks
semantic_document_to_markdown = semantic_v3_to_markdown


__all__ = [
    "MarkdownBlock",
    "MarkdownBlockKind",
    "document_ir_to_markdown",
    "document_ir_to_markdown_blocks",
    "escape_markdown_text",
    "is_safe_display_math_source",
    "is_safe_math_source",
    "markdown_with_external_links",
    "normalize_asset_path",
    "normalize_anchor_id",
    "normalize_link_destination",
    "preferred_text",
    "semantic_document_to_markdown",
    "semantic_document_to_markdown_blocks",
    "semantic_v3_to_markdown",
    "semantic_v3_to_markdown_blocks",
    "serialize_markdown",
]
