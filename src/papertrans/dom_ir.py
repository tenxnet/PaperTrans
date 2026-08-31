from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


DOM_IR_SCHEMA_VERSION = "1.0"
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9:._-]*$")
_DISALLOWED_TAGS = {"script", "iframe", "form", "input", "button", "textarea"}
_URL_ATTRIBUTES = {"href", "src", "data", "xlink:href"}


class DomIRError(ValueError):
    """Raised when persisted semantic DOM IR is malformed or unsafe."""


def _serialize_attribute(value: Any) -> str | list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return str(value)


def _serialize_node(node: Tag | NavigableString) -> dict[str, Any] | None:
    if isinstance(node, Comment):
        return None
    if isinstance(node, NavigableString):
        return {"type": "text", "text": str(node)}
    children = [
        serialized
        for child in node.children
        if isinstance(child, (Tag, NavigableString))
        and (serialized := _serialize_node(child)) is not None
    ]
    return {
        "type": "element",
        "tag": str(node.name).lower(),
        "attributes": {
            str(name): _serialize_attribute(value)
            for name, value in sorted(node.attrs.items())
        },
        "children": children,
    }


def serialize_article(article: Tag) -> dict[str, Any]:
    """Serialize a sanitized semantic article into lossless, JSON-safe DocumentIR content."""

    if article.name != "article":
        raise DomIRError("semantic DOM IR must have an article root")
    root = _serialize_node(article)
    assert root is not None
    return {"schemaVersion": DOM_IR_SCHEMA_VERSION, "root": root}


def _validated_attributes(raw: Any) -> dict[str, str | list[str]]:
    if not isinstance(raw, dict):
        raise DomIRError("element attributes must be an object")
    attributes: dict[str, str | list[str]] = {}
    for raw_name, raw_value in raw.items():
        name = str(raw_name)
        if not _NAME_RE.fullmatch(name) or name.lower().startswith("on"):
            continue
        if isinstance(raw_value, list):
            value: str | list[str] = [str(item) for item in raw_value]
        elif isinstance(raw_value, (str, int, float, bool)):
            value = str(raw_value)
        else:
            raise DomIRError(f"invalid attribute value for {name}")
        if name.lower() in _URL_ATTRIBUTES:
            url = " ".join(value) if isinstance(value, list) else value
            if url.strip().lower().startswith("javascript:"):
                continue
        attributes[name] = value
    return attributes


def _deserialize_node(
    soup: BeautifulSoup,
    raw: Any,
) -> Tag | NavigableString | None:
    if not isinstance(raw, dict):
        raise DomIRError("semantic DOM IR nodes must be objects")
    node_type = raw.get("type")
    if node_type == "text":
        text = raw.get("text")
        if not isinstance(text, str):
            raise DomIRError("text nodes must contain a string")
        return NavigableString(text)
    if node_type != "element":
        raise DomIRError(f"unsupported semantic DOM IR node type: {node_type!r}")
    name = raw.get("tag")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise DomIRError("element nodes must contain a valid tag name")
    if name.lower() in _DISALLOWED_TAGS:
        return None
    children = raw.get("children")
    if not isinstance(children, list):
        raise DomIRError("element children must be an array")
    tag = soup.new_tag(name.lower(), attrs=_validated_attributes(raw.get("attributes", {})))
    for child in children:
        restored = _deserialize_node(soup, child)
        if restored is not None:
            tag.append(restored)
    return tag


def deserialize_article(content: Any) -> Tag:
    """Restore the semantic article from persisted DocumentIR without reading HTML sidecars."""

    if not isinstance(content, dict) or content.get("schemaVersion") != DOM_IR_SCHEMA_VERSION:
        raise DomIRError("unsupported or missing semantic DOM IR schemaVersion")
    soup = BeautifulSoup("", "html.parser")
    root = _deserialize_node(soup, content.get("root"))
    if not isinstance(root, Tag) or root.name != "article":
        raise DomIRError("semantic DOM IR must restore an article root")
    soup.append(root)
    return root
