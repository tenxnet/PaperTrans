from pathlib import Path

import pytest

from papertrans.markdown_render import (
    MarkdownBlock,
    document_ir_to_markdown,
    escape_markdown_text,
    normalize_asset_path,
    normalize_link_destination,
    semantic_v3_to_markdown,
    serialize_markdown,
)
from papertrans.models import DocumentIR, DocumentItem, PageIR


def test_serializer_supports_core_blocks_and_is_deterministic():
    blocks = [
        MarkdownBlock(
            kind="heading",
            text="A *literal* [title] <tag>",
            level=1,
            anchor="paper title",
        ),
        MarkdownBlock(kind="list_item", text="first _item_", anchor="item"),
        MarkdownBlock(kind="list_item", text="second item", anchor="item"),
        MarkdownBlock(
            kind="figure",
            asset="assets/figure one(1).png",
            label="Figure [1]",
            caption="図 *1* の説明",
            anchor="figure-1",
        ),
        MarkdownBlock(
            kind="table",
            caption="結果表",
            rows=(("Model", "A|B"), ("PaperTrans", "1")),
            anchor="table-1",
        ),
        MarkdownBlock(kind="equation", text=r"E = mc^2", anchor="eq-1"),
        MarkdownBlock(kind="reference", text="[1] Example *reference*.", anchor="ref-1"),
        MarkdownBlock(
            kind="code",
            text="before\n```\nafter",
            language="python unsafe",
            anchor="code-1",
        ),
    ]

    first = serialize_markdown(blocks)
    second = serialize_markdown(tuple(blocks))

    assert first == second
    assert first.endswith("\n") and not first.endswith("\n\n")
    assert "# A \\*literal\\* \\[title\\] \\<tag\\>" in first
    assert '<a id="paper-title"></a>' in first
    assert '<a id="item-2"></a>' in first
    assert '- <a id="item"></a>first \\_item\\_\n' in first
    assert '- <a id="item-2"></a>second item' in first
    assert "assets/figure%20one%281%29.png" in first
    assert "| Model | A\\|B |" in first
    assert "$$\nE = mc^2\n$$" in first
    assert "\\[1\\] Example \\*reference\\*." in first
    assert "````python-unsafe\nbefore\n```\nafter\n````" in first


def test_markdown_escaping_neutralizes_block_and_inline_syntax():
    source = (
        "# heading\n- list\n1. ordered\n[x](url) &amp; <b>raw</b> $x$\n---"
        "\n    indented\n\ttabbed"
    )
    escaped = escape_markdown_text(source)

    assert escaped.splitlines() == [
        r"\# heading",
        r"\- list",
        r"1\. ordered",
        r"\[x\](url) \&amp; \<b\>raw\</b\> \$x\$",
        r"\---",
        "&#32;&#32;&#32;&#32;indented",
        "&#32;&#32;&#32;&#32;tabbed",
    ]


def test_validated_inline_markdown_requires_explicit_opt_in():
    inline = "*emphasis* $x$ [1](#ref-1)"
    literal = serialize_markdown([MarkdownBlock(kind="paragraph", text=inline)])
    trusted = serialize_markdown(
        [
            MarkdownBlock(
                kind="paragraph",
                text=inline,
                text_is_markdown=True,
            ),
            MarkdownBlock(
                kind="figure",
                asset="assets/figure.png",
                caption="Figure with $x$ and [1](#ref-1)",
                text_is_markdown=True,
            ),
            MarkdownBlock(
                kind="table",
                rows=(("Metric", "Value"), ("linked", "$x$ [1](#ref-1)\nnext")),
                text_is_markdown=True,
            ),
        ]
    )

    assert r"\*emphasis\* \$x\$ \[1\](#ref-1)" in literal
    assert inline in trusted
    assert "Figure with $x$ and [1](#ref-1)" in trusted
    assert "*Figure with" not in trusted
    assert "| linked | $x$ [1](#ref-1)<br>next |" in trusted


def test_asset_paths_are_relative_and_url_safe(tmp_path: Path):
    root = tmp_path / "work"
    asset = root / "assets" / "plot (final).png"

    assert normalize_asset_path(asset, relative_to=root) == "assets/plot%20%28final%29.png"
    assert normalize_asset_path("./assets/../assets/plot 1.png") == "assets/plot%201.png"
    with pytest.raises(ValueError, match="relative_to"):
        normalize_asset_path(asset)


def test_asset_and_link_destinations_cannot_escape_markdown_or_the_artifact_root(
    tmp_path: Path,
):
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "outside.png"

    with pytest.raises(ValueError, match="relative file"):
        normalize_asset_path("../../secret.png")
    with pytest.raises(ValueError, match="stay below"):
        normalize_asset_path(outside, relative_to=root)
    with pytest.raises(ValueError, match="unsupported asset URL scheme"):
        normalize_asset_path("data:image/svg+xml,<svg></svg>")

    hostile = "https://safe.example/?q=) ![track](https://evil.example/pixel"
    normalized = normalize_link_destination(hostile)
    assert ") ![track](" not in normalized
    assert "%29%20!%5Btrack%5D%28https://evil.example/pixel" in normalized


def test_unsafe_tex_falls_back_to_a_fenced_code_block():
    malicious = "x\n$$\n![track](https://evil.example/pixel)\n$$\ny"

    rendered = serialize_markdown([MarkdownBlock(kind="equation", text=malicious)])

    assert rendered.startswith("```tex\n")
    assert rendered.endswith("\n```\n")


def test_display_tex_allows_balanced_single_dollars_but_not_delimiter_escape():
    nested_text_math = r"\text{sample $x$}"
    unbalanced = r"\text{sample $x}"
    closes_display = "x\n$$\ny"

    rendered = serialize_markdown(
        [MarkdownBlock(kind="equation", text=nested_text_math)]
    )
    unbalanced_rendered = serialize_markdown(
        [MarkdownBlock(kind="equation", text=unbalanced)]
    )
    escaped_rendered = serialize_markdown(
        [MarkdownBlock(kind="equation", text=closes_display)]
    )

    assert rendered == "$$\n\\text{sample $x$}\n$$\n"
    assert unbalanced_rendered.startswith("```tex\n")
    assert escaped_rendered.startswith("```tex\n")


def test_empty_semantic_algorithm_visual_falls_back_to_its_asset():
    document = {
        "version": 3,
        "title": {"original": "Paper"},
        "sections": [
            {
                "id": "sec-1",
                "title": {"original": "Methods"},
                "content": [
                    {
                        "type": "visual",
                        "position": 1,
                        "value": {
                            "objectId": "algorithm-texts-404",
                            "kind": "algorithm",
                            "original": "",
                            "asset": "assets/page-013-algorithm-texts-404.png",
                            "label": "Algorithm",
                        },
                    },
                    {
                        "type": "visual",
                        "position": 2,
                        "value": {
                            "objectId": "algorithm-with-caption",
                            "kind": "algorithm",
                            "asset": "assets/algorithm.png",
                            "label": "Algorithm 1",
                            "caption": "Algorithm 1: Example procedure.",
                        },
                    },
                    {
                        "type": "visual",
                        "position": 3,
                        "value": {
                            "objectId": "empty-algorithm",
                            "kind": "algorithm",
                            "original": " \n\t",
                        },
                    },
                    {
                        "type": "visual",
                        "position": 4,
                        "value": {
                            "objectId": "algorithm-with-text",
                            "kind": "algorithm",
                            "original": "return result",
                            "language": "text",
                            "asset": "assets/unused-fallback.png",
                            "caption": "Unused fallback caption.",
                        },
                    },
                ],
            }
        ],
    }

    rendered = semantic_v3_to_markdown(document)

    assert "![Algorithm](assets/page-013-algorithm-texts-404.png)" in rendered
    assert "![Algorithm 1](assets/algorithm.png)" in rendered
    assert "*Algorithm 1: Example procedure.*" in rendered
    assert '<a id="visual-empty-algorithm"></a>' in rendered
    assert "```\n\n```" not in rendered
    assert "```text\nreturn result\n```" in rendered
    assert "unused-fallback.png" not in rendered
    assert "Unused fallback caption" not in rendered


def test_empty_code_without_asset_never_emits_a_fence():
    assert serialize_markdown([MarkdownBlock(kind="code", text=" \n\t")]) == ""
    assert (
        serialize_markdown(
            [MarkdownBlock(kind="code", text="", caption="Caption only")]
        )
        == "*Caption only*\n"
    )


def test_legacy_document_ir_adapter_prefers_japanese_and_explicit_order():
    document = DocumentIR(
        version=1,
        source_file="paper.pdf",
        source_sha256="abc",
        title="Original title",
        authors="Ada Lovelace",
        page_count=1,
        pages=[
            PageIR(
                number=1,
                width=100,
                height=100,
                items=[
                    DocumentItem(
                        id="p-last",
                        kind="paragraph",
                        page=1,
                        order=2,
                        original="Original paragraph",
                        japanese="日本語の段落",
                    ),
                    DocumentItem(
                        id="heading",
                        kind="heading",
                        page=1,
                        order=1,
                        original="Introduction",
                        japanese="   ",
                        level=2,
                    ),
                    DocumentItem(
                        id="figure",
                        kind="figure",
                        page=1,
                        order=3,
                        asset="assets/figure.png",
                        caption="Original caption",
                    ),
                ],
            )
        ],
    )

    rendered = document_ir_to_markdown(document)

    assert rendered.index("## Introduction") < rendered.index("日本語の段落")
    assert "Original paragraph" not in rendered
    assert "![Original caption](assets/figure.png)" in rendered
    assert '<a id="heading"></a>' in rendered


def test_semantic_v3_adapter_preserves_position_and_language_fallback():
    document = {
        "version": 3,
        "title": {"id": "title-unit", "original": "Paper", "japanese": "論文"},
        "frontMatter": {
            "authors": [{"original": "Ada"}, {"original": "Grace"}],
            "affiliations": [],
            "metadata": [],
        },
        "sections": [
            {
                "id": "sec-1",
                "number": "1",
                "level": 1,
                "title": {"original": "Methods", "japanese": "手法"},
                "content": [
                    {
                        "type": "unit",
                        "position": 3,
                        "value": {
                            "id": "ref-one",
                            "kind": "reference",
                            "referenceLabel": "1",
                            "original": "[1] Source",
                            "japanese": "",
                        },
                    },
                    {
                        "type": "visual",
                        "position": 2,
                        "value": {
                            "objectId": "figure-1",
                            "kind": "figure",
                            "label": "Figure 1",
                            "caption": "Original caption",
                            "asset": "assets/figure-1.png",
                        },
                    },
                    {
                        "type": "unit",
                        "position": 1,
                        "value": {
                            "id": "list-one",
                            "kind": "list_item",
                            "original": "fallback item",
                            "japanese": " ",
                        },
                    },
                ],
            }
        ],
    }

    rendered = semantic_v3_to_markdown(document)

    assert rendered.startswith('<a id="paper-title"></a>\n\n# 論文')
    assert "Ada · Grace" in rendered
    assert "## 1 手法" in rendered
    assert rendered.index("fallback item") < rendered.index("figure-1.png") < rendered.index("Source")
    assert '<a id="visual-figure-1"></a>' in rendered
    assert '<a id="ref-1"></a>' in rendered
    assert "\\[1\\] Source" in rendered


def test_semantic_adapter_rejects_non_v3_documents():
    with pytest.raises(ValueError, match="version 3"):
        semantic_v3_to_markdown({"version": 2, "title": {}, "sections": []})


def test_semantic_markdown_links_urls_in_body_and_visual_captions():
    document = {
        "version": 3,
        "title": {"original": "Paper"},
        "frontMatter": {
            "authors": [],
            "affiliations": [],
            "metadata": [{"original": "https://example.org/meta"}],
        },
        "sections": [
            {
                "id": "supplemental",
                "level": 1,
                "syntheticUnheaded": True,
                "title": {"original": "Supplemental Material"},
                "content": [
                    {
                        "type": "unit",
                        "value": {
                            "id": "body",
                            "kind": "paragraph",
                            "original": (
                                'See "https://example.org/a?x=1&y=2" now. '
                                "日本語（https://example.org/japanese）"
                            ),
                        },
                    },
                    {
                        "type": "visual",
                        "value": {
                            "objectId": "table-1",
                            "kind": "table",
                            "asset": "assets/table.png",
                            "caption": "Data at https://example.org/table.",
                        },
                    },
                ],
            }
        ],
    }

    rendered = semantic_v3_to_markdown(document)

    assert "Supplemental Material" not in rendered
    assert "[https://example.org/meta](https://example.org/meta)" in rendered
    assert (
        "[https://example.org/a?x=1\\&y=2]"
        "(https://example.org/a?x=1&y=2)"
    ) in rendered
    assert "[https://example.org/table](https://example.org/table)" in rendered
    assert (
        "日本語（[https://example.org/japanese]"
        "(https://example.org/japanese)）"
    ) in rendered
