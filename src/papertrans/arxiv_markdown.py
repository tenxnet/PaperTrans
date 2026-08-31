from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from .markdown_render import (
    MarkdownBlock,
    escape_markdown_text,
    is_safe_display_math_source,
    is_safe_math_source,
    normalize_anchor_id,
    normalize_asset_path,
    normalize_link_destination,
    serialize_markdown,
)


PLACEHOLDER_RE = re.compile(r"\[\[PTX_\d{4}\]\]")
_BLOCK_TAGS = {
    "article",
    "section",
    "div",
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "figure",
    "figcaption",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "td",
    "th",
    "pre",
    "blockquote",
    "dl",
    "dt",
    "dd",
}
_EQUATION_CLASSES = {"ltx_equation", "ltx_equationgroup", "ltx_eqn_table"}
_BIBLIOGRAPHY_CLASSES = {"ltx_bibliography", "ltx_biblist"}
_AUTHOR_CLASSES = {"ltx_authors", "ltx_date", "ltx_role_affiliation"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_RAW_TABLE_TAGS = {
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "caption",
    "colgroup",
    "col",
    "br",
    "span",
    "strong",
    "em",
    "sup",
    "sub",
    "a",
    "img",
    "code",
    "pre",
    "kbd",
    "samp",
}
_MATHML_TAGS = {
    "math",
    "semantics",
    "annotation",
    "annotation-xml",
    "mi",
    "mn",
    "mo",
    "ms",
    "mtext",
    "mspace",
    "mrow",
    "mfrac",
    "msqrt",
    "mroot",
    "mstyle",
    "merror",
    "mpadded",
    "mphantom",
    "mfenced",
    "menclose",
    "msub",
    "msup",
    "msubsup",
    "munder",
    "mover",
    "munderover",
    "mmultiscripts",
    "mprescripts",
    "none",
    "mtable",
    "mlabeledtr",
    "mtr",
    "mtd",
    "maligngroup",
    "malignmark",
    "maction",
}
_RAW_SVG_TAGS = {
    "svg",
    "g",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "tspan",
    "defs",
    "clipPath",
    "mask",
    "linearGradient",
    "radialGradient",
    "stop",
    "use",
    "image",
    "title",
    "desc",
    "foreignobject",
    "body",
    "div",
    "p",
    "span",
    "br",
    "strong",
    "em",
    "sup",
    "sub",
    "a",
    "img",
    "ul",
    "ol",
    "li",
}
_RAW_TABLE_TAGS |= _MATHML_TAGS | _RAW_SVG_TAGS
_SAFE_RAW_ATTRIBUTE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9:._-]*$")
_MARKDOWN_ANCHOR_RE = re.compile(r'<[A-Za-z][^>]*\bid="([^"]+)"')
_EMPTY_ANCHOR_RE = re.compile(r'<a id="([^"]+)"></a>')
_RAW_CONTAINER_TAG_RE = re.compile(r"</?(?:svg|table)\b[^>]*>", re.IGNORECASE)
_INTERNAL_LINK_RE = re.compile(r"(?<!!)\[(?:\\.|[^\]\\])*\]\(#([^\s)]+)\)")
_HTML_INTERNAL_LINK_RE = re.compile(r'<a\b[^>]*\bhref="#([^"]+)"', re.IGNORECASE)
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_MARKDOWN_IMAGE_DESTINATION_RE = re.compile(
    r"!\[(?:\\.|[^\]\\])*\]\(\s*(?P<destination><[^>\r\n]+>|(?:\\.|[^)\s])+)",
)


class ArxivMarkdownError(ValueError):
    """Raised when an arXiv DocumentIR cannot produce a complete Markdown artifact."""


def _element(node: Any) -> bool:
    return isinstance(node, Mapping) and node.get("type") == "element"


def _text_node(node: Any) -> bool:
    return isinstance(node, Mapping) and node.get("type") == "text"


def _tag(node: Mapping[str, Any]) -> str:
    return str(node.get("tag", "")).lower()


def _attributes(node: Mapping[str, Any]) -> Mapping[str, Any]:
    value = node.get("attributes", {})
    return value if isinstance(value, Mapping) else {}


def _attribute(node: Mapping[str, Any], name: str) -> str:
    value = _attributes(node).get(name)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _classes(node: Mapping[str, Any]) -> set[str]:
    return set(_attribute(node, "class").split())


def _children(node: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = node.get("children", [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ArxivMarkdownError("DocumentIR element children must be an array")
    return [child for child in value if isinstance(child, Mapping)]


def _descendants(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for child in _children(node):
        yield child
        if _element(child):
            yield from _descendants(child)


def _own_figure_descendants(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Walk a figure's own content without entering nested subfigures."""

    for child in _children(node):
        if not _element(child):
            continue
        if _tag(child) == "figure":
            continue
        yield child
        yield from _own_figure_descendants(child)


def _nested_figures(node: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return immediate semantic subfigures, including those inside layout wrappers."""

    nested: list[Mapping[str, Any]] = []
    for child in _children(node):
        if not _element(child):
            continue
        if _tag(child) == "figure":
            nested.append(child)
        else:
            nested.extend(_nested_figures(child))
    return nested


def _first_descendant(
    node: Mapping[str, Any],
    *,
    tag: str | None = None,
    classes: set[str] | None = None,
) -> Mapping[str, Any] | None:
    for candidate in _descendants(node):
        if not _element(candidate):
            continue
        if tag is not None and _tag(candidate) != tag:
            continue
        if classes is not None and not (_classes(candidate) & classes):
            continue
        return candidate
    return None


def _plain_text(node: Mapping[str, Any]) -> str:
    if _text_node(node):
        return str(node.get("text", ""))
    if not _element(node):
        return ""
    return "".join(_plain_text(child) for child in _children(node))


def _normalized_plain_text(node: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", " ", _plain_text(node)).strip()


def _plain_text_excluding(node: Mapping[str, Any], excluded_tags: set[str]) -> str:
    if _text_node(node):
        return str(node.get("text", ""))
    if not _element(node) or _tag(node) in excluded_tags:
        return ""
    return "".join(_plain_text_excluding(child, excluded_tags) for child in _children(node))


def _markdown_destination(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if not parsed.scheme and not parsed.netloc and not parsed.path and not parsed.query:
        return f"#{normalize_anchor_id(unquote(parsed.fragment))}" if parsed.fragment else None
    try:
        return normalize_link_destination(raw)
    except ValueError:
        return None


def _localized_media_destination(value: str) -> str | None:
    """Return a safe relative media path for the official-arXiv adapter.

    The shared Markdown serializer intentionally supports remote image URLs for
    other adapters. Official arXiv media, however, must have been downloaded
    into the artifact before projection, so only traversal-free relative paths
    are valid here.
    """

    raw = str(value).strip()
    if not raw:
        return None
    decoded = raw
    for _ in range(3):
        candidate = unquote(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    decoded = decoded.replace("\\", "/")
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = parsed.path
    if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        return None
    try:
        normalized = normalize_asset_path(raw)
    except ValueError:
        return None
    localized_parts = PurePosixPath(unquote(normalized)).parts
    if len(localized_parts) < 2 or localized_parts[0] != "assets":
        return None
    return normalized


def _inline_code(value: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * (longest + 1)
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{padding}{value}{padding}{fence}"


def _inline_anchor(node: Mapping[str, Any], rendered: str) -> str:
    raw = _attribute(node, "id").strip()
    if not raw:
        return rendered
    anchor = normalize_anchor_id(raw)
    return f'<a id="{anchor}"></a>{rendered}' if anchor else rendered


def _math_tex(node: Mapping[str, Any]) -> str:
    for candidate in _descendants(node):
        if not _element(candidate) or _tag(candidate) != "annotation":
            continue
        if _attribute(candidate, "encoding").lower() == "application/x-tex":
            value = _plain_text(candidate).strip()
            if value:
                return value
    alttext = _attribute(node, "alttext").strip()
    if alttext:
        return alttext
    presentation = _plain_text_excluding(node, {"annotation", "annotation-xml"})
    return re.sub(r"\s+", " ", presentation).strip()


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


def _neutralize_nested_math_dollars(value: str) -> str:
    """Replace balanced TeX math shifts that would terminate Markdown math."""

    positions = _unescaped_character_positions(value, "$")
    if not positions or len(positions) % 2:
        return value
    replacements = {
        position: r"\(" if index % 2 == 0 else r"\)"
        for index, position in enumerate(positions)
    }
    return "".join(replacements.get(index, character) for index, character in enumerate(value))


def _markdown_safe_tex(value: str, *, inline: bool) -> str:
    """Make official-arXiv TeX neutral to Markdown without weakening its safety gate.

    LaTeXML annotations occasionally contain notation that resembles Markdown
    structure (for example ``<X,Y>`` or ``[f](x)``), as well as nested ``$``
    shifts inside text.  Rewriting those tokens as equivalent TeX keeps the
    source-specific adapter lossless while the generic serializer can retain
    its deliberately strict injection checks.
    """

    prepared = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    prepared = prepared.replace(r"\lx@sectionsign", r"\S")
    # LaTeXML sometimes leaves page-layout commands in X-TeX annotations.
    # Keep their grouped content, but remove constructs unsupported by common
    # Markdown math renderers such as MathJax and KaTeX.
    prepared = re.sub(r"\\parbox\{[^{}\n]*\}\{", "{", prepared)
    prepared = prepared.replace(r"\begin{flushleft}", "")
    prepared = prepared.replace(r"\end{flushleft}", "")
    if inline:
        prepared = _neutralize_nested_math_dollars(prepared)
    # Do not guess whether a raw angle token denotes an inner product or an
    # inequality.  Pairing arbitrary ``<...>`` strings corrupts comparison
    # chains such as ``a<1,a>1``.  Explicit relation commands preserve the
    # source meaning and the empty group terminates the TeX control word.
    prepared = prepared.replace("<", r"\lt{}").replace(">", r"\gt{}")
    # A mathematical closing bracket followed by an argument can look exactly
    # like a Markdown link.  An empty TeX group has no visual effect.
    prepared = prepared.replace("](", "]{}(")
    if inline:
        prepared = re.sub(r"\s+", " ", prepared)
    return prepared.strip()


def _math_markdown(node: Mapping[str, Any], *, force_display: bool = False) -> str:
    display = bool(force_display)
    value = _markdown_safe_tex(_math_tex(node), inline=not display)
    if not value:
        return ""
    if not is_safe_math_source(value):
        return _inline_code(value)
    return f"$$\n{value}\n$$" if display else f"${value}$"


def _safe_raw_node(node: Mapping[str, Any], allowed_tags: set[str]) -> str:
    if _text_node(node):
        return html.escape(str(node.get("text", "")), quote=False)
    if not _element(node):
        return ""
    tag = _tag(node)
    if tag not in {value.lower() for value in allowed_tags}:
        return "".join(_safe_raw_node(child, allowed_tags) for child in _children(node))
    attributes: list[str] = []
    for raw_name, raw_value in sorted(_attributes(node).items()):
        name = str(raw_name)
        lowered_name = name.lower()
        if (
            not _SAFE_RAW_ATTRIBUTE_RE.fullmatch(name)
            or lowered_name.startswith("on")
            or lowered_name in {"srcset", "xml:base"}
        ):
            continue
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        value = " ".join(str(item) for item in values)
        css_urls = [match.group(2).strip() for match in _CSS_URL_RE.finditer(value)]
        if any(not target.startswith("#") for target in css_urls):
            continue
        if lowered_name in {"href", "src", "data", "xlink:href"}:
            if tag == "use":
                if not value.strip().startswith("#"):
                    continue
            elif tag in {"img", "image"} and lowered_name in {"src", "href", "xlink:href"}:
                if tag == "image" and value.strip().startswith("#"):
                    value = value.strip()
                elif localized := _localized_media_destination(value):
                    value = localized
                else:
                    continue
            elif tag != "a":
                if not value.strip().startswith("#"):
                    continue
            else:
                destination = _markdown_destination(value)
                if destination is None:
                    continue
                value = destination
        attributes.append(f' {name}="{html.escape(value, quote=True)}"')
    start = f"<{tag}{''.join(attributes)}>"
    if tag in _VOID_TAGS:
        return start
    body = "".join(_safe_raw_node(child, allowed_tags) for child in _children(node))
    return f"{start}{body}</{tag}>"


def _protected_nodes(node: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    protected: list[Mapping[str, Any]] = []

    def walk(current: Mapping[str, Any]) -> None:
        if not _element(current):
            return
        tag = _tag(current)
        classes = _classes(current)
        if (
            tag in {"math", "cite", "code", "pre", "svg", "img", "object", "a"}
            or "ltx_tag" in classes
            or "ltx_note_mark" in classes
            or "ltx_font_typewriter" in classes
            or "ltx_role_footnote" in classes
        ):
            protected.append(current)
            return
        for child in _children(current):
            walk(child)

    walk(node)
    return protected


def _join_inline(parts: Iterable[str]) -> str:
    value = "".join(parts)
    value = re.sub(r"[\t\r\n ]+", " ", value)
    return value.strip()


def _inline_markdown(
    node: Mapping[str, Any],
    units: Mapping[str, Mapping[str, Any]],
) -> str:
    if _text_node(node):
        return escape_markdown_text(str(node.get("text", "")))
    if not _element(node):
        return ""
    unit_id = _attribute(node, "data-papertrans-id")
    if unit_id and unit_id in units:
        return _inline_anchor(node, _unit_markdown(node, units[unit_id], units))

    tag = _tag(node)
    classes = _classes(node)
    if tag in {"script", "style", "iframe", "form", "input", "button", "textarea"}:
        return ""
    if tag == "math":
        return _inline_anchor(node, _math_markdown(node))
    if tag == "br":
        return "  \n"
    if tag == "img":
        src = _localized_media_destination(_attribute(node, "src"))
        alt = escape_markdown_text(_attribute(node, "alt") or "Figure").replace("\n", " ")
        return _inline_anchor(node, f"![{alt}]({src})" if src else alt)
    if tag == "svg":
        return _inline_anchor(node, _safe_raw_node(node, _RAW_SVG_TAGS))
    if tag == "a":
        label = _join_inline(_inline_markdown(child, units) for child in _children(node))
        destination = _markdown_destination(_attribute(node, "href"))
        return _inline_anchor(
            node,
            f"[{label}]({destination})" if label and destination else label,
        )
    if tag in {"code", "kbd", "samp"} or "ltx_font_typewriter" in classes:
        # MathML carries both presentation text and a TeX annotation.  Plain
        # code spans should include the visible token once, not concatenate
        # both representations (for example ``LRG3++ELG1``).
        code = _plain_text_excluding(node, {"svg", "annotation", "annotation-xml"})
        return _inline_anchor(node, _inline_code(code)) if code else ""
    inner = _join_inline(_inline_markdown(child, units) for child in _children(node))
    if not inner:
        return ""
    if tag in {"strong", "b"} or "ltx_font_bold" in classes:
        return _inline_anchor(node, f"**{inner}**")
    if tag in {"em", "i"} or "ltx_font_italic" in classes:
        return _inline_anchor(node, f"*{inner}*")
    if tag == "del":
        return _inline_anchor(node, f"~~{inner}~~")
    return _inline_anchor(node, inner)


def _unit_markdown(
    node: Mapping[str, Any],
    unit: Mapping[str, Any],
    units: Mapping[str, Mapping[str, Any]],
) -> str:
    translated = str(unit.get("japanese", "")).strip()
    source = str(unit.get("translationSource", unit.get("sourceText", ""))).strip()
    value = translated or source
    protected_nodes = _protected_nodes(node)
    raw_placeholders = unit.get("placeholders", {})
    placeholder_ids = (
        [
            str(token)
            for token in raw_placeholders
            if isinstance(token, str) and PLACEHOLDER_RE.fullmatch(token)
        ]
        if isinstance(raw_placeholders, Mapping)
        else []
    )
    if len(placeholder_ids) != len(protected_nodes):
        placeholder_ids = [
            f"[[PTX_{index:04d}]]" for index in range(1, len(protected_nodes) + 1)
        ]
    replacements = dict(zip(placeholder_ids, protected_nodes, strict=True))
    tokens = list(PLACEHOLDER_RE.finditer(value))
    token_ids = [match.group(0) for match in tokens]
    if any(token_ids.count(token) != 1 for token in replacements):
        raise ArxivMarkdownError(
            f"protected-node mismatch for {unit.get('id', '<unknown>')}: "
            f"tokens={token_ids!r}, expected={list(replacements)!r}"
        )
    pieces: list[str] = []
    position = 0
    for match in tokens:
        pieces.append(escape_markdown_text(value[position : match.start()]))
        token = match.group(0)
        pieces.append(
            _inline_markdown(replacements[token], units)
            if token in replacements
            else escape_markdown_text(token)
        )
        position = match.end()
    pieces.append(escape_markdown_text(value[position:]))
    return _join_inline(pieces)


def _caption(node: Mapping[str, Any], units: Mapping[str, Mapping[str, Any]]) -> str | None:
    caption = next(
        (
            candidate
            for candidate in _own_figure_descendants(node)
            if _tag(candidate) == "figcaption"
        ),
        None,
    )
    if caption is None:
        return None
    value = _join_inline(_inline_markdown(child, units) for child in _children(caption))
    return _inline_anchor(caption, value) or None


def _media_fallback_markdown(
    node: Mapping[str, Any],
    units: Mapping[str, Mapping[str, Any]],
    *,
    default: str,
) -> str:
    """Preserve visible/accessible text when a media reference is rejected."""

    parts: list[str] = []
    label = _attribute(node, "alt") or _attribute(node, "aria-label")
    if label:
        parts.append(escape_markdown_text(label).replace("\n", " "))
    child_text = _join_inline(_inline_markdown(child, units) for child in _children(node))
    if child_text and child_text not in parts:
        parts.append(child_text)
    return "\n\n".join(parts) if parts else escape_markdown_text(default)


def _table_rows(
    table: Mapping[str, Any],
    units: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for row in _descendants(table):
        if not _element(row) or _tag(row) != "tr":
            continue
        cells = [child for child in _children(row) if _element(child) and _tag(child) in {"th", "td"}]
        if not cells:
            continue
        rendered: list[str] = []
        for cell in cells:
            value = _join_inline(_inline_markdown(child, units) for child in _children(cell))
            value = re.sub(r"(?<!\\)\|", r"\\|", value)
            span = _attribute(cell, "colspan")
            width = int(span) if span.isdigit() else 1
            rendered.extend([value, *([""] * (max(1, width) - 1))])
        rows.append(tuple(rendered))
    return tuple(rows)


def _has_complex_table_span(table: Mapping[str, Any]) -> bool:
    for node in _descendants(table):
        if not _element(node) or _tag(node) not in {"th", "td"}:
            continue
        if _attribute(node, "rowspan") not in {"", "1"}:
            return True
    return False


def _equation_rows(
    node: Mapping[str, Any],
    inherited_anchor: str | None = None,
) -> list[tuple[Mapping[str, Any], str | None, tuple[str, ...]]]:
    """Return equation rows with their closest anchor and ancestor targets."""

    rows: list[tuple[Mapping[str, Any], str | None, tuple[str, ...]]] = []

    def walk(current: Mapping[str, Any], parent_targets: tuple[str, ...]) -> None:
        if not _element(current):
            return
        own_anchor = _attribute(current, "id")
        current_targets = parent_targets
        if own_anchor and own_anchor not in current_targets:
            current_targets = (*current_targets, own_anchor)
        classes = _classes(current)
        if _tag(current) == "tr" and (
            "ltx_eqn_row" in classes or bool(classes & _EQUATION_CLASSES)
        ):
            closest_anchor = current_targets[-1] if current_targets else None
            rows.append((current, closest_anchor, current_targets))
            return
        for child in _children(current):
            if _element(child):
                walk(child, current_targets)

    initial_targets = (inherited_anchor,) if inherited_anchor else ()
    walk(node, initial_targets)
    return rows


def _equation_labels(node: Mapping[str, Any]) -> tuple[str, ...]:
    labels: list[str] = []
    for item in [node, *_descendants(node)]:
        if not _element(item) or "ltx_tag_equation" not in _classes(item):
            continue
        label = _normalized_plain_text(item)
        if label and label not in labels:
            labels.append(label)
    return tuple(labels)


def _listing_source(node: Mapping[str, Any]) -> str:
    if _text_node(node):
        return str(node.get("text", ""))
    if not _element(node):
        return ""
    if _tag(node) == "math":
        tex = _math_tex(node)
        return f"${tex}$" if tex else _normalized_plain_text(node)
    return "".join(_listing_source(child) for child in _children(node))


class _Projector:
    def __init__(self, document: Mapping[str, Any]) -> None:
        if document.get("schema") != "papertrans.document-ir":
            raise ArxivMarkdownError("arXiv Markdown requires papertrans.document-ir")
        if document.get("profile") != "official_arxiv_html":
            raise ArxivMarkdownError("arXiv Markdown requires the official_arxiv_html profile")
        content = document.get("content")
        if not isinstance(content, Mapping) or content.get("schemaVersion") != "1.0":
            raise ArxivMarkdownError(
                "this job predates complete DocumentIR content; prepare it again before exporting Markdown"
            )
        root = content.get("root")
        if not isinstance(root, Mapping) or not _element(root) or _tag(root) != "article":
            raise ArxivMarkdownError("DocumentIR content must have an article root")
        raw_units = document.get("units", [])
        if not isinstance(raw_units, Sequence):
            raise ArxivMarkdownError("DocumentIR units must be an array")
        self.document = document
        self.root = root
        self.units = {
            str(unit["id"]): unit
            for unit in raw_units
            if isinstance(unit, Mapping) and unit.get("id")
        }
        self.blocks: list[MarkdownBlock] = []

    def project(self) -> list[MarkdownBlock]:
        self._walk_children(self.root, section_anchor=None, bibliography=False)
        readable = _human_readable_blocks(
            self.blocks,
            _referenced_anchor_ids(self.blocks),
        )
        if not any(block.kind == "heading" and block.level == 1 for block in readable):
            raise ArxivMarkdownError("DocumentIR does not contain a paper title")
        return readable

    def _walk_children(
        self,
        node: Mapping[str, Any],
        *,
        section_anchor: str | None,
        bibliography: bool,
    ) -> None:
        for child in _children(node):
            if _element(child):
                self._walk_element(
                    child,
                    section_anchor=section_anchor,
                    bibliography=bibliography,
                )

    def _walk_element(
        self,
        node: Mapping[str, Any],
        *,
        section_anchor: str | None,
        bibliography: bool,
    ) -> None:
        tag = _tag(node)
        classes = _classes(node)
        anchor = _attribute(node, "id") or None
        unit_id = _attribute(node, "data-papertrans-id")
        current_bibliography = bibliography or bool(classes & _BIBLIOGRAPHY_CLASSES)

        if tag == "section":
            self._walk_children(
                node,
                section_anchor=anchor or section_anchor,
                bibliography=current_bibliography,
            )
            return
        if re.fullmatch(r"h[1-6]", tag):
            text = (
                _unit_markdown(node, self.units[unit_id], self.units)
                if unit_id in self.units
                else _join_inline(_inline_markdown(child, self.units) for child in _children(node))
            )
            if text:
                self.blocks.append(
                    MarkdownBlock(
                        kind="heading",
                        text=text,
                        level=int(tag[1]),
                        anchor=anchor or section_anchor,
                        text_is_markdown=True,
                    )
                )
            return
        if tag == "p":
            text = (
                _unit_markdown(node, self.units[unit_id], self.units)
                if unit_id in self.units
                else _join_inline(_inline_markdown(child, self.units) for child in _children(node))
            )
            if text:
                self.blocks.append(
                    MarkdownBlock(
                        kind="paragraph",
                        text=text,
                        anchor=anchor,
                        text_is_markdown=True,
                    )
                )
            return
        if unit_id in self.units:
            text = _unit_markdown(node, self.units[unit_id], self.units)
            if text:
                self.blocks.append(
                    MarkdownBlock(
                        kind="paragraph",
                        text=text,
                        anchor=anchor,
                        text_is_markdown=True,
                    )
                )
            return
        if tag == "figure":
            self._project_figure(node, anchor)
            return
        if tag == "table":
            if classes & _EQUATION_CLASSES:
                self._project_equation(node, anchor)
            else:
                self._project_table(node, anchor, caption=None)
            return
        if tag in {"ul", "ol"}:
            if anchor:
                self.blocks.append(MarkdownBlock(kind="paragraph", anchor=anchor))
            self._project_list(node, depth=1, ordered=tag == "ol", bibliography=current_bibliography)
            return
        if tag == "pre" or (tag == "code" and "ltx_verbatim" in classes):
            code_text = _plain_text(node)
            if code_text.strip():
                self.blocks.append(MarkdownBlock(kind="code", text=code_text, anchor=anchor))
            elif anchor:
                self.blocks.append(MarkdownBlock(kind="paragraph", anchor=anchor))
            for embedded_svg in (
                item for item in _descendants(node) if _element(item) and _tag(item) == "svg"
            ):
                raw = _safe_raw_node(embedded_svg, _RAW_SVG_TAGS)
                if raw:
                    self.blocks.append(
                        MarkdownBlock(kind="figure", text=raw, text_is_markdown=True)
                    )
            return
        if "ltx_listing" in classes:
            self._project_listing(node, anchor)
            return
        if classes & _EQUATION_CLASSES:
            self._project_equation(node, anchor)
            return
        if tag == "math":
            value = _math_tex(node)
            if value:
                if _attribute(node, "display").lower() == "block":
                    self.blocks.append(
                        MarkdownBlock(
                            kind="equation",
                            text=_markdown_safe_tex(value, inline=False),
                            anchor=anchor,
                        )
                    )
                else:
                    self.blocks.append(
                        MarkdownBlock(
                            kind="paragraph",
                            text=_math_markdown(node),
                            anchor=anchor,
                            text_is_markdown=True,
                        )
                    )
            return
        if tag == "svg":
            raw = _safe_raw_node(node, _RAW_SVG_TAGS)
            if raw:
                self.blocks.append(
                    MarkdownBlock(
                        kind="figure",
                        text=raw,
                        anchor=anchor,
                        text_is_markdown=True,
                    )
                )
            return
        if classes & _AUTHOR_CLASSES:
            text = _join_inline(_inline_markdown(child, self.units) for child in _children(node))
            if text:
                self.blocks.append(
                    MarkdownBlock(kind="paragraph", text=text, anchor=anchor, text_is_markdown=True)
                )
            return
        if anchor:
            self.blocks.append(MarkdownBlock(kind="paragraph", anchor=anchor))
        self._walk_children(
            node,
            section_anchor=section_anchor,
            bibliography=current_bibliography,
        )

    def _project_figure(self, node: Mapping[str, Any], anchor: str | None) -> None:
        caption = _caption(node, self.units)
        own_nodes = list(_own_figure_descendants(node))
        table = next((item for item in own_nodes if _tag(item) == "table"), None)
        nested_figures = _nested_figures(node)
        classes = _classes(node)
        visual_kind = "table" if "ltx_table" in classes else "figure"
        own_images = [
            item
            for item in own_nodes
            if _tag(item) in {"img", "object"}
            and (
                _attribute(item, "src" if _tag(item) == "img" else "data")
                or _attribute(item, "alt")
                or _attribute(item, "aria-label")
                or _normalized_plain_text(item)
            )
        ]
        listing = next(
            (item for item in own_nodes if "ltx_listing" in _classes(item)),
            None,
        )
        if listing is not None:
            if anchor or caption:
                self.blocks.append(
                    MarkdownBlock(
                        kind="figure",
                        anchor=anchor,
                        caption=caption,
                        text_is_markdown=True,
                    )
                )
            self._project_listing(listing, _attribute(listing, "id") or None)
            return
        if table is not None and ("ltx_table" in classes or not own_images):
            if "ltx_figure" in classes:
                self.blocks.append(
                    MarkdownBlock(
                        kind="figure",
                        anchor=anchor,
                        caption=caption,
                        text_is_markdown=True,
                    )
                )
                self._project_table(table, None, caption=None)
            else:
                self._project_table(table, anchor, caption=caption)
            return
        if own_images:
            for index, image in enumerate(own_images):
                asset_attribute = "src" if _tag(image) == "img" else "data"
                raw_asset = _attribute(image, asset_attribute)
                asset = _localized_media_destination(raw_asset)
                label = _attribute(image, "alt") or _attribute(image, "aria-label") or None
                if asset is None:
                    fallback_parts = [
                        _media_fallback_markdown(
                            image,
                            self.units,
                            default=visual_kind.capitalize(),
                        )
                    ]
                    if index == 0 and caption and caption not in fallback_parts:
                        fallback_parts.append(caption)
                    self.blocks.append(
                        MarkdownBlock(
                            kind=visual_kind,
                            anchor=anchor if index == 0 else None,
                            text="\n\n".join(fallback_parts),
                            label=label,
                            text_is_markdown=True,
                        )
                    )
                    continue
                self.blocks.append(
                    MarkdownBlock(
                        kind=visual_kind,
                        anchor=anchor if index == 0 else None,
                        asset=asset,
                        label=label,
                        caption=caption if index == 0 else None,
                        text_is_markdown=True,
                    )
                )
            for nested in nested_figures:
                self._project_figure(nested, _attribute(nested, "id") or None)
            return
        svg = next((item for item in own_nodes if _tag(item) == "svg"), None)
        if svg is not None:
            raw = _safe_raw_node(svg, _RAW_SVG_TAGS)
            if caption:
                raw = f"{raw}\n\n*{caption}*"
            self.blocks.append(
                MarkdownBlock(
                    kind=visual_kind,
                    text=raw,
                    anchor=anchor,
                    text_is_markdown=True,
                )
            )
            for nested in nested_figures:
                self._project_figure(nested, _attribute(nested, "id") or None)
            return
        if nested_figures:
            for nested in nested_figures:
                self._project_figure(nested, _attribute(nested, "id") or None)
            self.blocks.append(
                MarkdownBlock(
                    kind="figure",
                    anchor=anchor,
                    caption=caption,
                    text_is_markdown=True,
                )
            )
            return
        text = _normalized_plain_text(node)
        self.blocks.append(MarkdownBlock(kind=visual_kind, text=text, anchor=anchor))

    def _project_listing(self, node: Mapping[str, Any], anchor: str | None) -> None:
        lines = [
            item
            for item in _descendants(node)
            if _element(item) and "ltx_listingline" in _classes(item)
        ]
        source = "\n".join(
            re.sub(r"[ \t\r\n]+", " ", _listing_source(line)).strip()
            for line in lines
        ).strip()
        if not source:
            source = _listing_source(node).strip()
        self.blocks.append(
            MarkdownBlock(
                kind="code",
                text=source,
                anchor=anchor,
                language="text",
            )
        )
        seen = {anchor} if anchor else set()
        for line in lines:
            line_anchor = _attribute(line, "id") or None
            if not line_anchor or line_anchor in seen:
                continue
            seen.add(line_anchor)
            self.blocks.append(MarkdownBlock(kind="paragraph", anchor=line_anchor))

    def _append_table_structure_anchors(
        self,
        table: Mapping[str, Any],
        primary_anchor: str | None,
    ) -> None:
        seen = {primary_anchor} if primary_anchor else set()
        structural_tags = {"table", "thead", "tbody", "tfoot", "tr", "th", "td"}
        for item in [table, *_descendants(table)]:
            if not _element(item) or _tag(item) not in structural_tags:
                continue
            anchor = _attribute(item, "id") or None
            if not anchor or anchor in seen:
                continue
            seen.add(anchor)
            self.blocks.append(MarkdownBlock(kind="paragraph", anchor=anchor))

    def _project_table(
        self,
        table: Mapping[str, Any],
        anchor: str | None,
        *,
        caption: str | None,
    ) -> None:
        if _has_complex_table_span(table):
            raw = _safe_raw_node(table, _RAW_TABLE_TAGS)
            if caption:
                raw = f"*{caption}*\n\n{raw}"
            self.blocks.append(
                MarkdownBlock(
                    kind="table",
                    text=raw,
                    anchor=anchor,
                    text_is_markdown=True,
                )
            )
            return
        self._append_table_structure_anchors(table, anchor)
        rows = _table_rows(table, self.units)
        self.blocks.append(
            MarkdownBlock(
                kind="table",
                anchor=anchor,
                rows=rows,
                caption=caption,
                text_is_markdown=True,
            )
        )

    def _project_equation(self, node: Mapping[str, Any], anchor: str | None) -> None:
        rows = _equation_rows(node, anchor)
        if rows:
            for row, row_anchor, ancestor_targets in rows:
                maths = [
                    item
                    for item in _descendants(row)
                    if _element(item) and _tag(item) == "math"
                ]
                values: list[str] = []
                for item in maths:
                    value = _math_tex(item)
                    if value:
                        values.append(_markdown_safe_tex(value, inline=False))
                if not values:
                    continue
                # Keep every referenced row target next to its own formula.
                # Emitting all table targets up front makes a link to row 2
                # incorrectly land before row 1.
                local_targets = [
                    target for target in ancestor_targets if target != row_anchor
                ]
                for item in [row, *_descendants(row)]:
                    target = _attribute(item, "id") if _element(item) else ""
                    if target and target != row_anchor and target not in local_targets:
                        local_targets.append(target)
                for target in local_targets:
                    self.blocks.append(MarkdownBlock(kind="paragraph", anchor=target))
                labels = _equation_labels(row)
                caption = " ".join(escape_markdown_text(label) for label in labels) or None
                self.blocks.append(
                    MarkdownBlock(
                        kind="equation",
                        text="\n\\qquad\n".join(values),
                        anchor=row_anchor,
                        caption=caption,
                        text_is_markdown=caption is not None,
                    )
                )
            return
        maths = [
            item for item in _descendants(node) if _element(item) and _tag(item) == "math"
        ]
        values: list[str] = []
        for item in maths:
            value = _math_tex(item)
            if value:
                values.append(_markdown_safe_tex(value, inline=False))
        if not values:
            return
        labels = _equation_labels(node)
        caption = " ".join(escape_markdown_text(label) for label in labels) or None
        self.blocks.append(
            MarkdownBlock(
                kind="equation",
                text="\n\\qquad\n".join(values),
                anchor=anchor,
                caption=caption,
                text_is_markdown=caption is not None,
            )
        )

    def _project_list(
        self,
        node: Mapping[str, Any],
        *,
        depth: int,
        ordered: bool,
        bibliography: bool,
    ) -> None:
        start_value = _attribute(node, "start")
        start = int(start_value) if start_value.isdigit() else 1
        item_index = 0
        for item in _children(node):
            if not _element(item) or _tag(item) != "li":
                continue
            item_index += 1
            nested = [
                child
                for child in _children(item)
                if _element(child) and _tag(child) in {"ul", "ol"}
            ]
            content = [
                child
                for child in _children(item)
                if child not in nested
                and not (_element(child) and "ltx_tag_item" in _classes(child))
            ]
            text = _join_inline(_inline_markdown(child, self.units) for child in content)
            item_anchor = _attribute(item, "id") or None
            is_reference = bibliography or "ltx_bibitem" in _classes(item)
            if text:
                self.blocks.append(
                    MarkdownBlock(
                        kind="reference" if is_reference else "list_item",
                        text=text,
                        level=depth,
                        anchor=item_anchor,
                        ordered=ordered,
                        start=start + item_index - 1,
                        text_is_markdown=True,
                    )
                )
            elif item_anchor:
                self.blocks.append(MarkdownBlock(kind="paragraph", anchor=item_anchor))
            embedded: list[Mapping[str, Any]] = []
            for content_node in content:
                if not _element(content_node):
                    continue
                embedded.extend([content_node, *_descendants(content_node)])
            for listing in (
                candidate
                for candidate in embedded
                if _element(candidate) and "ltx_listing" in _classes(candidate)
            ):
                self._project_listing(listing, _attribute(listing, "id") or None)
            for embedded_svg in (
                candidate
                for candidate in embedded
                if _element(candidate) and _tag(candidate) == "svg"
            ):
                raw = _safe_raw_node(embedded_svg, _RAW_SVG_TAGS)
                if raw:
                    self.blocks.append(
                        MarkdownBlock(kind="figure", text=raw, text_is_markdown=True)
                    )
            for nested_list in nested:
                self._project_list(
                    nested_list,
                    depth=depth + 1,
                    ordered=_tag(nested_list) == "ol",
                    bibliography=bibliography,
                )


def arxiv_document_to_markdown_blocks(document: Mapping[str, Any]) -> list[MarkdownBlock]:
    """Project complete official-arXiv DocumentIR directly into Markdown blocks."""

    return _Projector(document).project()


def arxiv_document_to_markdown(document: Mapping[str, Any]) -> str:
    """Serialize complete official-arXiv DocumentIR without reading or rendering HTML."""

    return serialize_markdown(arxiv_document_to_markdown_blocks(document))


def _without_fenced_code(markdown: str) -> str:
    lines = markdown.splitlines()
    visible: list[str] = []
    fence: str | None = None
    for line in lines:
        candidate = re.match(r"^(`{3,})(?:[^`]*)$", line)
        if fence is None:
            if candidate:
                fence = candidate.group(1)
            else:
                visible.append(line)
        elif line.strip() == fence:
            fence = None
    without_fences = "\n".join(visible)
    return re.sub(r"(?P<fence>`+)[^\n]*?(?P=fence)", "", without_fences)


def _count_non_svg_listings(node: Mapping[str, Any], *, inside_svg: bool = False) -> int:
    if not _element(node):
        return 0
    current_inside_svg = inside_svg or _tag(node) == "svg"
    count = int("ltx_listing" in _classes(node) and not current_inside_svg)
    return count + sum(
        _count_non_svg_listings(child, inside_svg=current_inside_svg)
        for child in _children(node)
    )


def _math_source_roles(
    root: Mapping[str, Any],
) -> tuple[
    list[Mapping[str, Any]],
    list[Mapping[str, Any]],
    list[Mapping[str, Any]],
    list[Mapping[str, Any]],
]:
    """Return inline, row, standalone-display, and code-literal math roles."""

    rows = [row for row, _anchor, _targets in _equation_rows(root)]
    row_math_ids = {
        id(item)
        for row in rows
        for item in _descendants(row)
        if _element(item) and _tag(item) == "math"
    }
    inline: list[Mapping[str, Any]] = []
    standalone_display: list[Mapping[str, Any]] = []
    literal: list[Mapping[str, Any]] = []
    inline_tags = {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "dt",
        "dd",
        "figcaption",
        "td",
        "th",
        "a",
        "span",
    }

    def walk(node: Mapping[str, Any], inline_context: bool = False) -> None:
        if not _element(node):
            return
        tag = _tag(node)
        classes = _classes(node)
        if (
            tag in {"pre", "code"}
            or "ltx_listing" in classes
            or "ltx_font_typewriter" in classes
        ):
            literal.extend(
                item
                for item in [node, *_descendants(node)]
                if _element(item)
                and _tag(item) == "math"
                and id(item) not in row_math_ids
            )
            return
        if tag == "math":
            if id(node) in row_math_ids:
                return
            if inline_context or _attribute(node, "display").lower() != "block":
                inline.append(node)
            else:
                standalone_display.append(node)
            return
        next_inline_context = inline_context or tag in inline_tags
        for child in _children(node):
            if _element(child):
                walk(child, next_inline_context)

    walk(root)
    return inline, rows, standalone_display, literal


def _count_inline_markdown_math(markdown: str) -> int:
    visible = _without_fenced_code(markdown)
    raw_math = len(re.findall(r"<math(?:\s|>)", visible, re.IGNORECASE))
    without_raw_math = re.sub(
        r"<math\b[^>]*>.*?</math>",
        "",
        visible,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_display = re.sub(
        r"(?<!\\)\$\$.*?(?<!\\)\$\$",
        "",
        without_raw_math,
        flags=re.DOTALL,
    )
    delimiters = _unescaped_character_positions(without_display, "$")
    return raw_math + len(delimiters) // 2


def _referenced_anchor_ids(blocks: Sequence[MarkdownBlock]) -> set[str]:
    values: list[str] = []
    for block in blocks:
        if not block.text_is_markdown or block.kind == "code":
            continue
        values.extend((block.text, block.caption or ""))
        values.extend(cell for row in block.rows for cell in row)
    visible = _without_fenced_code("\n".join(values))
    return {
        normalize_anchor_id(unquote(target))
        for target in [
            *_INTERNAL_LINK_RE.findall(visible),
            *_HTML_INTERNAL_LINK_RE.findall(visible),
        ]
    }


def _raw_container_segments(value: str) -> list[tuple[bool, str]]:
    """Split generated Markdown while keeping raw SVG/table regions opaque."""

    segments: list[tuple[bool, str]] = []
    position = 0
    raw_start: int | None = None
    depth = 0
    for match in _RAW_CONTAINER_TAG_RE.finditer(value):
        closing = value[match.start() + 1 :].lstrip().startswith("/")
        if not closing:
            if depth == 0:
                if match.start() > position:
                    segments.append((False, value[position : match.start()]))
                raw_start = match.start()
            depth += 1
            continue
        if depth == 0:
            continue
        depth -= 1
        if depth == 0 and raw_start is not None:
            segments.append((True, value[raw_start : match.end()]))
            position = match.end()
            raw_start = None
    if raw_start is not None:
        segments.append((True, value[raw_start:]))
        position = len(value)
    if position < len(value):
        segments.append((False, value[position:]))
    return segments


def _prune_empty_anchors(value: str | None, keep: set[str]) -> str | None:
    if value is None:
        return None
    return "".join(
        segment
        if raw
        else _EMPTY_ANCHOR_RE.sub(
            lambda match: match.group(0) if match.group(1) in keep else "",
            segment,
        )
        for raw, segment in _raw_container_segments(value)
    )


def _human_readable_blocks(
    blocks: Sequence[MarkdownBlock],
    referenced_anchors: set[str],
) -> list[MarkdownBlock]:
    readable: list[MarkdownBlock] = []
    emitted_block_anchors: set[str] = set()
    for block in blocks:
        anchor = block.anchor
        if anchor:
            normalized_anchor = normalize_anchor_id(anchor)
            if (
                normalized_anchor not in referenced_anchors
                or normalized_anchor in emitted_block_anchors
            ):
                anchor = None
            else:
                emitted_block_anchors.add(normalized_anchor)
        rows = tuple(
            tuple(_prune_empty_anchors(cell, referenced_anchors) or "" for cell in row)
            for row in block.rows
        )
        updated = replace(
            block,
            anchor=anchor,
            text=_prune_empty_anchors(block.text, referenced_anchors) or "",
            caption=_prune_empty_anchors(block.caption, referenced_anchors),
            rows=rows,
        )
        if not (
            updated.anchor
            or updated.asset is not None
            or updated.rows
            or updated.text.strip()
            or (updated.caption and updated.caption.strip())
        ):
            continue
        readable.append(updated)
    return readable


def _unlocalized_media_destinations(
    blocks: Sequence[MarkdownBlock],
    markdown: str,
) -> list[str]:
    """Find media references that are not safe artifact-relative paths."""

    candidates = [str(block.asset) for block in blocks if block.asset is not None]
    visible = _without_fenced_code(markdown)
    for match in _MARKDOWN_IMAGE_DESTINATION_RE.finditer(visible):
        destination = match.group("destination").strip()
        if destination.startswith("<") and destination.endswith(">"):
            destination = destination[1:-1].strip()
        candidates.append(destination)
    raw_html = BeautifulSoup(visible, "html.parser")
    for tag, attribute in [
        *((value, "src") for value in raw_html.find_all("img", src=True)),
        *((value, "data") for value in raw_html.find_all("object", data=True)),
        *((value, "href") for value in raw_html.find_all("image", href=True)),
        *(
            (value, "xlink:href")
            for value in raw_html.find_all("image", attrs={"xlink:href": True})
        ),
    ]:
        destination = str(tag.get(attribute, "")).strip()
        if tag.name == "image" and destination.startswith("#"):
            continue
        candidates.append(destination)
    return sorted(
        {
            candidate
            for candidate in candidates
            if _localized_media_destination(candidate) is None
        }
    )


def validate_arxiv_markdown(
    document: Mapping[str, Any],
    blocks: Sequence[MarkdownBlock],
    markdown: str,
) -> dict[str, Any]:
    """Run deterministic completeness checks on a projected Markdown artifact."""

    content = document.get("content", {})
    root = content.get("root") if isinstance(content, Mapping) else None
    nodes = [root, *_descendants(root)] if isinstance(root, Mapping) else []
    elements = [node for node in nodes if isinstance(node, Mapping) and _element(node)]
    inline_math, equation_rows, standalone_display_math, literal_math = (
        _math_source_roles(root) if isinstance(root, Mapping) else ([], [], [], [])
    )
    display_math = sum(
        sum(
            _element(item) and _tag(item) == "math"
            for item in _descendants(row)
        )
        for row in equation_rows
    ) + len(standalone_display_math)
    display_rows = sum(
        any(_element(item) and _tag(item) == "math" for item in _descendants(row))
        for row in equation_rows
    ) + len(standalone_display_math)
    source_counts = {
        "sections": sum(_tag(node) == "section" for node in elements),
        "figures": sum(_tag(node) == "figure" and "ltx_figure" in _classes(node) for node in elements),
        "tables": sum(_tag(node) == "figure" and "ltx_table" in _classes(node) for node in elements),
        "math": sum(_tag(node) == "math" for node in elements),
        "inlineMath": len(inline_math),
        "displayMath": display_math,
        "displayRows": display_rows,
        "literalMath": len(literal_math),
        "equationLabels": sum("ltx_tag_equation" in _classes(node) for node in elements),
        "bibliographyEntries": sum("ltx_bibitem" in _classes(node) for node in elements),
        "svg": sum(_tag(node) == "svg" for node in elements),
        "listings": _count_non_svg_listings(root) if isinstance(root, Mapping) else 0,
    }
    equation_blocks = [block for block in blocks if block.kind == "equation"]
    projected_display_math = sum(
        1 + block.text.count("\n\\qquad\n")
        for block in equation_blocks
        if block.text.strip()
    )
    inline_fallbacks = sum(
        not is_safe_math_source(prepared) and _inline_code(prepared) in markdown
        for node in inline_math
        if (prepared := _markdown_safe_tex(_math_tex(node), inline=True))
    )
    output_counts = {
        "headings": sum(block.kind == "heading" for block in blocks),
        "paragraphs": sum(block.kind == "paragraph" for block in blocks),
        "figures": sum(block.kind == "figure" for block in blocks),
        "tables": sum(block.kind == "table" for block in blocks),
        "equations": len(equation_blocks),
        "inlineMath": _count_inline_markdown_math(markdown),
        "displayMath": projected_display_math,
        "displayRows": len(equation_blocks),
        "equationLabels": sum(bool(block.caption) for block in equation_blocks),
        "mathFallbacks": inline_fallbacks
        + sum(
            not is_safe_display_math_source(block.text.strip())
            for block in equation_blocks
        ),
        "references": sum(block.kind == "reference" for block in blocks),
        "svg": len(re.findall(r"<svg(?:\s|>)", markdown, re.IGNORECASE)),
        "codeBlocks": sum(block.kind == "code" for block in blocks),
    }
    visible_markdown = _without_fenced_code(markdown)
    anchors = set(_MARKDOWN_ANCHOR_RE.findall(visible_markdown))
    link_targets = {
        unquote(value)
        for value in [
            *_INTERNAL_LINK_RE.findall(visible_markdown),
            *_HTML_INTERNAL_LINK_RE.findall(visible_markdown),
        ]
    }
    empty_anchors = [
        anchor
        for raw, segment in _raw_container_segments(visible_markdown)
        if not raw
        for anchor in _EMPTY_ANCHOR_RE.findall(segment)
    ]
    unreferenced_empty_anchors = sorted(set(empty_anchors) - link_targets)
    unresolved_links = sorted(link_targets - anchors)
    unresolved_tokens = sorted(set(PLACEHOLDER_RE.findall(markdown)))
    unlocalized_media = _unlocalized_media_destinations(blocks, markdown)
    failures: list[str] = []
    if not markdown.strip():
        failures.append("Markdown output is empty")
    if (
        source_counts["inlineMath"]
        + source_counts["displayMath"]
        + source_counts["literalMath"]
        != source_counts["math"]
    ):
        failures.append("source math roles could not be classified completely")
    if unresolved_tokens:
        failures.append("protected PTX placeholders remain unresolved")
    if unlocalized_media:
        failures.append("Markdown output contains remote or unlocalized media")
    if unresolved_links:
        failures.append("internal Markdown links have no explicit target anchor")
    if unreferenced_empty_anchors:
        failures.append("Markdown output contains unreferenced empty anchors")
    if output_counts["headings"] == 0:
        failures.append("Markdown output has no headings")
    if output_counts["figures"] < source_counts["figures"]:
        failures.append("Markdown output dropped one or more figures")
    if output_counts["tables"] < source_counts["tables"]:
        failures.append("Markdown output dropped one or more tables")
    if output_counts["inlineMath"] < source_counts["inlineMath"]:
        failures.append("Markdown output dropped one or more inline math expressions")
    if output_counts["displayMath"] < source_counts["displayMath"]:
        failures.append("Markdown output dropped one or more display math expressions")
    if output_counts["displayRows"] < source_counts["displayRows"]:
        failures.append("Markdown output dropped one or more display equation rows")
    if output_counts["equationLabels"] < source_counts["equationLabels"]:
        failures.append("Markdown output dropped one or more equation labels")
    if output_counts["mathFallbacks"]:
        failures.append("one or more math expressions fell back to code")
    if output_counts["references"] < source_counts["bibliographyEntries"]:
        failures.append("Markdown output dropped one or more bibliography entries")
    if output_counts["svg"] < source_counts["svg"]:
        failures.append("Markdown output dropped one or more inline SVG diagrams")
    if output_counts["codeBlocks"] < source_counts["listings"]:
        failures.append("Markdown output dropped one or more source listings")
    return {
        "status": "failed" if failures else "passed",
        "source": source_counts,
        "output": output_counts,
        "anchors": len(anchors),
        "emptyAnchors": len(empty_anchors),
        "internalLinks": len(link_targets),
        "unreferencedEmptyAnchors": unreferenced_empty_anchors,
        "unresolvedInternalLinks": unresolved_links,
        "unresolvedPlaceholders": unresolved_tokens,
        "unlocalizedMedia": unlocalized_media,
        "failures": failures,
    }


def render_arxiv_markdown_document(
    document: Mapping[str, Any],
    output_dir: Path,
) -> Path:
    """Write the Markdown sibling artifact for an official-arXiv DocumentIR."""

    output_dir.mkdir(parents=True, exist_ok=True)
    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = serialize_markdown(blocks)
    qa = validate_arxiv_markdown(document, blocks, markdown)
    if qa["status"] != "passed":
        raise ArxivMarkdownError(f"rendered Markdown QA failed: {qa}")
    markdown_path = output_dir / "index.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    (output_dir / "markdown-qa.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return markdown_path


__all__ = [
    "ArxivMarkdownError",
    "arxiv_document_to_markdown",
    "arxiv_document_to_markdown_blocks",
    "render_arxiv_markdown_document",
    "validate_arxiv_markdown",
]
