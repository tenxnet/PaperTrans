from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup

from papertrans.arxiv_html import (
    _parse_codex_jsonl,
    _section_chunks,
    _tokenize_node,
    _validate_rendered_html,
    normalize_arxiv_id,
    normalize_article_document,
    render_arxiv_html_document,
)


FIXTURE = """
<article class="ltx_document">
  <h1 class="ltx_title ltx_title_document">A Study of Model X</h1>
  <div id="abstract1" class="ltx_abstract">
    <h6 class="ltx_title ltx_title_abstract">Abstract</h6>
    <p id="abstract1.1" class="ltx_p">We study <math id="m1"><mi>x</mi></math> with <cite>[<a href="#bib.b1">1</a>]</cite>.</p>
  </div>
  <section id="S1" class="ltx_section">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">I </span>Introduction</h2>
    <p id="S1.p1" class="ltx_p">See <a class="ltx_ref" href="#S1.F1">Fig. 1</a> for details.</p>
    <figure id="S1.F1" class="ltx_figure"><svg><path d="M0 0"></path></svg><figcaption>Fig. 1: Original caption.</figcaption></figure>
  </section>
  <section id="bib" class="ltx_bibliography"><h2 class="ltx_title ltx_title_bibliography">References</h2><ul><li id="bib.b1" class="ltx_bibitem">[1] Source.</li></ul></section>
</article>
"""


def test_normalizes_arxiv_urls_and_versions():
    assert normalize_arxiv_id("https://arxiv.org/abs/2508.19843") == "2508.19843"
    assert normalize_arxiv_id("arXiv:2508.19843v3") == "2508.19843v3"


def test_tokenizer_protects_math_citations_and_cross_references():
    soup = BeautifulSoup(FIXTURE, "html.parser")
    paragraph = soup.find("p", id="abstract1.1")
    assert paragraph is not None
    text, placeholders = _tokenize_node(paragraph)
    assert text == "We study [[PTX_0001]] with [[PTX_0002]]."
    assert "<math" in placeholders["[[PTX_0001]]"]
    assert "<cite" in placeholders["[[PTX_0002]]"]
    assert 'id="m1"' not in placeholders["[[PTX_0001]]"]


def test_normalizer_excludes_captions_and_bibliography(tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "article-source.html").write_text(FIXTURE, encoding="utf-8")
    acquisition = {
        "requestedArxivId": "2508.19843",
        "resolvedArxivId": "2508.19843v3",
        "sourceUrl": "https://arxiv.org/html/2508.19843v3",
        "sourceSha256": "0" * 64,
        "license": "CC BY-NC-SA 4.0",
        "validation": {"math": 1, "figures": 1, "tables": 0},
    }
    document = normalize_article_document(acquisition, work, work / "document.json")
    source_text = " ".join(unit["sourceText"] for unit in document["units"])
    assert "Original caption" not in source_text
    assert "References" not in source_text
    assert len(document["units"]) == 5


def test_normalizer_leaves_failed_table_environment_opaque(tmp_path: Path):
    fixture = FIXTURE.replace(
        "</section>\n  <section id=\"bib\"",
        '<div class="ltx_para"><span class="ltx_ERROR undefined">{longtblr}</span><p class="ltx_p">raw table options</p></div><div class="ltx_para"><p class="ltx_p">model/id PT FT</p></div></section>\n  <section id="bib"',
    )
    work = tmp_path / "work"
    work.mkdir()
    (work / "article-source.html").write_text(fixture, encoding="utf-8")
    acquisition = {
        "requestedArxivId": "2508.19843",
        "resolvedArxivId": "2508.19843v3",
        "sourceUrl": "https://arxiv.org/html/2508.19843v3",
        "sourceSha256": "0" * 64,
        "license": "CC",
        "validation": {"math": 1, "figures": 1, "tables": 0},
    }
    document = normalize_article_document(acquisition, work, work / "document.json")
    source_text = " ".join(unit["sourceText"] for unit in document["units"])
    assert "raw table options" not in source_text
    assert "model/id PT FT" not in source_text
    normalized = (work / "article-normalized.html").read_text(encoding="utf-8")
    assert "raw table options" in normalized
    assert 'data-papertrans-opaque="true"' in normalized


def test_chunks_pack_complete_small_sections_without_splitting_them():
    units = [
        {"sectionId": "S1", "translationSource": "a" * 5, "japanese": ""},
        {"sectionId": "S1", "translationSource": "b" * 5, "japanese": ""},
        {"sectionId": "S2", "translationSource": "c" * 5, "japanese": ""},
    ]
    chunks = _section_chunks(units, 20)
    assert [[unit["sectionId"] for unit in chunk] for chunk in chunks] == [["S1", "S1", "S2"]]


def test_chunks_split_only_sections_that_exceed_the_budget():
    units = [
        {"sectionId": "S1", "translationSource": "a" * 12, "japanese": ""},
        {"sectionId": "S1", "translationSource": "b" * 12, "japanese": ""},
        {"sectionId": "S2", "translationSource": "c" * 5, "japanese": ""},
    ]
    chunks = _section_chunks(units, 20)
    assert [[unit["sectionId"] for unit in chunk] for chunk in chunks] == [["S1"], ["S1", "S2"]]


def test_parses_codex_jsonl_result_and_token_usage():
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": '{"translations": []}',
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 25,
                        "cache_write_input_tokens": 5,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 7,
                    },
                }
            ),
        ]
    )
    result, usage = _parse_codex_jsonl(stdout)
    assert result == {"translations": []}
    assert usage == {
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "cache_write_input_tokens": 5,
        "output_tokens": 20,
        "reasoning_output_tokens": 7,
        "total_tokens": 120,
    }


def test_renderer_preserves_visible_math_figures_and_links(tmp_path: Path):
    work = tmp_path / "work"
    output = tmp_path / "html"
    (work / "assets").mkdir(parents=True)
    (work / "article-normalized.html").write_text(FIXTURE, encoding="utf-8")
    (work / "acquisition.json").write_text("{}", encoding="utf-8")
    (work / "source-route.json").write_text("{}", encoding="utf-8")
    soup = BeautifulSoup(FIXTURE, "html.parser")
    units = []
    for index, node in enumerate(
        [soup.h1, soup.find("h6"), soup.find("p", id="abstract1.1"), soup.find("h2", class_="ltx_title_section"), soup.find("p", id="S1.p1")],
        start=1,
    ):
        assert node is not None
        unit_id = f"html-{index:04d}"
        target = BeautifulSoup((work / "article-normalized.html").read_text(), "html.parser")
        node["data-papertrans-id"] = unit_id
        text, placeholders = _tokenize_node(node)
        units.append(
            {
                "id": unit_id,
                "kind": "title" if index == 1 else "heading" if node.name.startswith("h") else "paragraph",
                "tag": node.name,
                "sectionId": "S1" if index >= 4 else "front",
                "sectionTitle": "Introduction",
                "anchorId": "S1" if index >= 4 else "front",
                "sourceText": node.get_text(" ", strip=True),
                "sourceHtml": str(node),
                "translationSource": text,
                "placeholders": placeholders,
                "japanese": text.replace("We study", "本研究では").replace("with", "を用いる").replace("Introduction", "はじめに").replace("Abstract", "概要").replace("A Study of Model X", "Model Xの研究").replace("See", "参照せよ").replace("for details", "詳細について"),
                "preservedTerms": [],
                "warnings": [],
            }
        )
    normalized = BeautifulSoup(FIXTURE, "html.parser")
    targets = [normalized.h1, normalized.find("h6"), normalized.find("p", id="abstract1.1"), normalized.find("h2", class_="ltx_title_section"), normalized.find("p", id="S1.p1")]
    for unit, target in zip(units, targets, strict=True):
        assert target is not None
        target["data-papertrans-id"] = unit["id"]
        unit["sourceHtml"] = str(target)
    (work / "article-normalized.html").write_text(str(normalized.article), encoding="utf-8")
    document = {
        "source": {"resolvedArxivId": "2508.19843v3", "url": "https://arxiv.org/html/2508.19843v3", "license": "CC"},
        "status": "translated",
        "model": {"translation": "test"},
        "validation": {"math": 1, "figures": 1, "tables": 0},
        "units": units,
    }
    index = render_arxiv_html_document(document, work, output)
    rendered = BeautifulSoup(index.read_text(encoding="utf-8"), "html.parser")
    source_article = normalized.article
    assert source_article is not None
    qa = _validate_rendered_html(source_article, rendered)
    assert qa["status"] == "passed"
    assert len([math for math in rendered.find_all("math") if not math.find_parent("details")]) == 1
    assert rendered.find("a", href="#bib.b1") is not None
    layout_css = rendered.style.get_text() if rendered.style is not None else ""
    assert "grid-template-columns:minmax(0,1fr)" in layout_css
    assert ".ptx-main .ltx_table{max-width:100%;overflow-x:auto" in layout_css
    assert ".ptx-main .ltx_equationgroup" in layout_css
    assert "@media(max-width:1200px)" in layout_css
    assert "width:max-content;min-width:100%" not in layout_css
    assert json.loads((output / "qa.json").read_text())["status"] == "passed"
