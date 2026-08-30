from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup

from papertrans.arxiv_html import (
    _asset_url_candidates,
    _citation_metadata,
    _decode_local_images,
    _download_assets,
    _parse_codex_jsonl,
    _repair_section_hierarchy,
    _section_chunks,
    _tokenize_node,
    _validate_rendered_html,
    normalize_arxiv_id,
    normalize_article_document,
    render_arxiv_html_document,
)
from papertrans.arxiv_markdown import (
    arxiv_document_to_markdown,
    arxiv_document_to_markdown_blocks,
    validate_arxiv_markdown,
)
from papertrans.dom_ir import serialize_article
from papertrans.render import arxiv_html_artifact_version


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
    assert normalize_arxiv_id("https://arxiv.org/abs/math/0211159") == "math/0211159"
    assert normalize_arxiv_id("arXiv:HEP-TH/9901001V2") == "hep-th/9901001v2"


def test_reads_authors_and_publication_date_from_arxiv_html_fallbacks():
    soup = BeautifulSoup(
        """
        <div id="watermark-tr">arXiv:2508.19843v3 [cs.CR] 17 Nov 2025</div>
        <div class="ltx_authors">
          <span class="ltx_personname">Ada Lovelace, Alan Turing</span>
          <span class="ltx_personname"><span class="ltx_text">Grace Hopper</span><span class="ltx_text">Edsger Dijkstra</span></span>
          <span class="ltx_personname">Andreas TerzisFlorian Tramèr<span class="ltx_note">1 footnotemark</span></span>
          <span class="ltx_personname">[0.5em] OpenAI</span>
        </div>
        """,
        "html.parser",
    )
    assert _citation_metadata(soup) == {
        "authors": [
            "Ada Lovelace",
            "Alan Turing",
            "Grace Hopper",
            "Edsger Dijkstra",
            "Andreas Terzis",
            "Florian Tramèr",
        ],
        "publishedAt": "17 Nov 2025",
    }


def test_arxiv_asset_urls_support_native_and_document_directory_forms():
    base = "https://arxiv.org/html/2405.20947v5"
    assert _asset_url_candidates(base, "2405.20947v5/x1.png") == [
        "https://arxiv.org/html/2405.20947v5/x1.png"
    ]
    assert _asset_url_candidates(base, "x1.png") == [
        "https://arxiv.org/html/x1.png",
        "https://arxiv.org/html/2405.20947v5/x1.png",
    ]


def test_asset_download_falls_back_to_arxiv_document_directory(monkeypatch, tmp_path: Path):
    requested: list[str] = []

    def fake_request(url: str, timeout: int = 60):
        requested.append(url)
        if url == "https://arxiv.org/html/x1.png":
            raise OSError("404")
        return b"png", url, "image/png"

    monkeypatch.setattr("papertrans.arxiv_html._request_bytes", fake_request)
    soup = BeautifulSoup('<article><img src="x1.png"></article>', "html.parser")
    article = soup.article
    assert article is not None
    result = _download_assets(
        soup,
        article,
        "https://arxiv.org/html/2405.20947v5",
        tmp_path / "assets",
    )
    assert requested == [
        "https://arxiv.org/html/x1.png",
        "https://arxiv.org/html/2405.20947v5/x1.png",
    ]
    assert result["failures"] == []
    assert result["downloaded"][0]["url"] == "https://arxiv.org/html/2405.20947v5/x1.png"
    assert article.img is not None
    assert str(article.img["src"]).startswith("assets/")


def test_local_image_decode_qa_rejects_corrupt_images(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "broken.png").write_bytes(b"not an image")
    result = _decode_local_images(tmp_path, ["assets/broken.png"])
    assert result["engine"] == "PyMuPDF"
    assert result["checked"] == 0
    assert result["failures"][0]["asset"] == "assets/broken.png"


def test_local_image_decode_qa_accepts_renderable_svg(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "figure.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="8">'
        '<rect width="10" height="8" fill="blue"/></svg>',
        encoding="utf-8",
    )
    result = _decode_local_images(tmp_path, ["assets/figure.svg"])
    assert result == {"engine": "PyMuPDF", "checked": 1, "failures": []}


def test_tokenizer_protects_math_citations_and_cross_references():
    soup = BeautifulSoup(FIXTURE, "html.parser")
    paragraph = soup.find("p", id="abstract1.1")
    assert paragraph is not None
    text, placeholders = _tokenize_node(paragraph)
    assert text == "We study [[PTX_0001]] with [[PTX_0002]]."
    assert "<math" in placeholders["[[PTX_0001]]"]
    assert "<cite" in placeholders["[[PTX_0002]]"]
    assert 'id="m1"' not in placeholders["[[PTX_0001]]"]


def test_tokenizer_does_not_confuse_literal_placeholder_text_with_nodes():
    soup = BeautifulSoup(
        '<p data-papertrans-id="html-0001">Literal [[PTX_0001]] and <math>x</math>.</p>',
        "html.parser",
    )
    paragraph = soup.p
    assert paragraph is not None

    text, placeholders = _tokenize_node(paragraph)

    assert text == "Literal [[PTX_0001]] and [[PTX_0002]]."
    assert list(placeholders) == ["[[PTX_0002]]"]
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(
            BeautifulSoup(
                f'<article><h1>Title</h1>{paragraph}</article>',
                "html.parser",
            ).article
        ),
        "units": [
            {
                "id": "html-0001",
                "translationSource": text,
                "japanese": text,
                "placeholders": placeholders,
            }
        ],
    }

    markdown = arxiv_document_to_markdown(document)

    assert r"Literal \[\[PTX\_0001\]\] and $x$" in markdown


def test_markdown_restores_reordered_placeholders_by_token_id():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1 class="ltx_title ltx_title_document" data-papertrans-id="html-0001">Title</h1>
          <p class="ltx_p" data-papertrans-id="html-0002">
            Study <math><annotation encoding="application/x-tex">x</annotation></math>
            with <cite>[<a href="#bib.b1">1</a>]</cite>.
          </p>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [
            {
                "id": "html-0001",
                "translationSource": "Title",
                "japanese": "題名",
            },
            {
                "id": "html-0002",
                "translationSource": "Study [[PTX_0001]] with [[PTX_0002]].",
                "japanese": "引用 [[PTX_0002]] と数式 [[PTX_0001]]。",
            },
        ],
    }

    markdown = arxiv_document_to_markdown(document)

    assert "引用 [[PTX_" not in markdown
    assert markdown.index("[1](#bib.b1)") < markdown.index("$x$")


def test_markdown_preserves_subfigures_table_targets_and_list_markers():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1 class="ltx_title ltx_title_document">Title</h1>
          <p class="ltx_p">See <a href="#T1.cell">the value</a>.</p>
          <ul><li id="I1"><span class="ltx_tag ltx_tag_item">•</span> Item</li></ul>
          <figure class="ltx_figure" id="F1">
            <div>
              <figure class="ltx_figure ltx_figure_panel" id="F1.sf1">
                <img src="assets/a.png" alt="A"/>
                <figcaption>(a) Alpha</figcaption>
              </figure>
              <figure class="ltx_figure ltx_figure_panel" id="F1.sf2">
                <img src="assets/b.png" alt="B"/>
                <figcaption>(b) Beta</figcaption>
              </figure>
            </div>
            <figcaption>Figure 1: Combined.</figcaption>
          </figure>
          <figure class="ltx_table" id="T1">
            <figcaption>Table 1</figcaption>
            <table><tbody id="T1.body"><tr id="T1.row"><td id="T1.cell">42</td></tr></tbody></table>
          </figure>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "passed"
    assert qa["source"]["figures"] == qa["output"]["figures"] == 3
    assert qa["source"]["tables"] == qa["output"]["tables"] == 1
    assert '<a id="F1"></a>' not in markdown
    assert '<a id="F1.sf1"></a>' not in markdown
    assert '<a id="F1.sf2"></a>' not in markdown
    assert "(a) Alpha" in markdown and "(b) Beta" in markdown
    assert "Figure 1: Combined." in markdown
    assert '<a id="T1.cell"></a>' in markdown
    assert '<a id="T1.body"></a>' not in markdown
    assert '<a id="T1.row"></a>' not in markdown
    assert '- <a id="I1"></a>Item' not in markdown
    assert "- Item" in markdown
    assert "- • Item" not in markdown


def test_markdown_keeps_only_targets_referenced_by_rendered_content():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1 id="title">Title</h1>
          <script><a href="#unused">hidden source link</a></script>
          <p id="unused">Unused target</p>
          <p>See <a href="#kept">the kept target</a>.</p>
          <p id="kept">Kept target</p>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "passed"
    assert qa["emptyAnchors"] == qa["internalLinks"] == 1
    assert qa["unreferencedEmptyAnchors"] == []
    assert '<a id="kept"></a>' in markdown
    assert '<a id="title"></a>' not in markdown
    assert '<a id="unused"></a>' not in markdown


def test_markdown_does_not_duplicate_an_inherited_section_anchor():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1>Title</h1>
          <p>See <a href="#S1">the section</a>.</p>
          <section id="S1">
            <h2>Main section</h2>
            <h6>Acknowledgements.</h6>
          </section>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "passed"
    assert markdown.count('<a id="S1"></a>') == 1
    assert '<a id="S1-2"></a>' not in markdown
    assert "###### Acknowledgements." in markdown


def test_markdown_neutralizes_arxiv_tex_that_only_looks_like_markdown():
    soup = BeautifulSoup(
        r"""
        <article class="ltx_document">
          <h1>Title</h1>
          <p>
            <math><annotation encoding="application/x-tex">\sigma=&lt;(E-&lt;E&gt;)^{2}&gt;+[f](x),\alpha=1,\alpha&lt;1,\alpha&gt;1</annotation></math>,
            <math><annotation encoding="application/x-tex">&gt;3\sigma</annotation></math>, and
            <math><annotation encoding="application/x-tex">\lx@sectionsign
              2</annotation></math>.
          </p>
          <p>Before <math display="block" alttext="x">x</math> after.</p>
          <p>
            <math><semantics><mrow><mi>y</mi></mrow>
              <annotation encoding="text/plain">do not duplicate</annotation>
            </semantics></math>
          </p>
          <p><span class="ltx_font_typewriter">LRG3<math alttext="+"><semantics><mo>+</mo><annotation encoding="application/x-tex">+</annotation></semantics></math>ELG1</span></p>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "passed"
    assert qa["source"]["inlineMath"] == qa["output"]["inlineMath"] == 5
    assert qa["source"]["literalMath"] == 1
    assert qa["output"]["mathFallbacks"] == 0
    assert (
        r"$\sigma=\lt{}(E-\lt{}E\gt{})^{2}\gt{}+[f]{}(x),"
        r"\alpha=1,\alpha\lt{}1,\alpha\gt{}1$"
        in markdown
    )
    assert r"$\gt{}3\sigma$" in markdown
    assert r"$\S 2$" in markdown
    assert "Before $x$ after." in markdown
    assert "$y$" in markdown
    assert "do not duplicate" not in markdown
    assert "`LRG3+ELG1`" in markdown
    assert "LRG3++ELG1" not in markdown
    assert "```tex" not in markdown


def test_markdown_preserves_each_equation_row_label_and_target():
    soup = BeautifulSoup(
        r"""
        <article class="ltx_document">
          <h1>Title</h1>
          <p>See <a href="#E2">Equation 2</a> and <a href="#R2">its row</a>.</p>
          <table class="ltx_equationgroup ltx_eqn_table" id="EG1">
            <tbody id="E1"><tr class="ltx_equation ltx_eqn_row">
              <td><math display="inline"><annotation encoding="application/x-tex">
                \text{sample $x$}
              </annotation></math></td>
              <td><span class="ltx_tag ltx_tag_equation">(1)</span></td>
            </tr></tbody>
            <tbody id="E2"><tr class="ltx_equation ltx_eqn_row" id="R2">
              <td><math display="block"><annotation encoding="application/x-tex">
                z=\parbox{10pt}{\begin{flushleft}y\end{flushleft}}
              </annotation></math></td>
              <td><span class="ltx_tag ltx_tag_equation">(2)</span></td>
            </tr></tbody>
          </table>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "passed"
    assert qa["source"]["displayRows"] == qa["output"]["displayRows"] == 2
    assert qa["source"]["displayMath"] == qa["output"]["displayMath"] == 2
    assert qa["source"]["equationLabels"] == qa["output"]["equationLabels"] == 2
    assert qa["output"]["mathFallbacks"] == 0
    assert markdown.count("$$") == 4
    assert r"\text{sample $x$}" in markdown
    assert r"z={y}" in markdown
    assert "\\parbox" not in markdown
    assert "flushleft" not in markdown
    assert "(1)" in markdown and "(2)" in markdown
    assert markdown.count('<a id="E2"></a>') == 1
    assert markdown.count('<a id="R2"></a>') == 1
    assert markdown.index("(1)") < markdown.index('<a id="E2"></a>') < markdown.index("z={y}")
    assert markdown.index('<a id="E2"></a>') < markdown.index('<a id="R2"></a>')
    assert "[Equation 2](#E2)" in markdown
    assert "[its row](#R2)" in markdown
    assert "```tex" not in markdown


def test_arxiv_math_injection_remains_code_and_fails_math_qa():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1>Title</h1>
          <p><math alttext="x $$ ![track](https://evil.example/pixel) $$ y">x</math></p>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "failed"
    assert qa["output"]["mathFallbacks"] == 1
    assert "one or more math expressions fell back to code" in qa["failures"]
    assert "![track](" not in markdown


def test_markdown_preserves_svg_nested_in_a_verbatim_wrapper():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1>Title</h1>
          <code class="ltx_verbatim"><span>
            <svg id="diagram" viewBox="0 0 10 10">
              <a id="svg-internal-target"></a>
              <use href="#svg-internal-target"></use>
              <foreignObject><div class="ltx_listing">Diagram label</div></foreignObject>
            </svg>
          </span></code>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "passed"
    assert qa["source"]["svg"] == qa["output"]["svg"] == 1
    assert qa["source"]["listings"] == 0
    assert '<svg id="diagram"' in markdown
    assert '<a id="svg-internal-target"></a>' in markdown
    assert '<use href="#svg-internal-target"></use>' in markdown
    assert qa["unreferencedEmptyAnchors"] == []
    assert "<foreignobject>" in markdown
    assert "Diagram label" in markdown


def test_markdown_preserves_empty_anchors_inside_raw_complex_tables():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1>Title</h1>
          <figure class="ltx_table" id="T1">
            <table>
              <tr><td rowspan="2"><a id="raw-cell-target"></a>A</td><td>B</td></tr>
              <tr><td>C</td></tr>
            </table>
          </figure>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "passed"
    assert '<table>' in markdown
    assert '<a id="raw-cell-target"></a>' in markdown
    assert 'rowspan="2"' in markdown
    assert qa["unreferencedEmptyAnchors"] == []


def test_markdown_projects_latex_listing_as_a_fenced_code_block():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1>Title</h1>
          <div class="ltx_listing" id="alg1">
            <div class="ltx_listingline" id="alg1.l1">
              <span class="ltx_tag ltx_tag_listingline">1:</span> Set <math alttext="x">x</math>
            </div>
            <div class="ltx_listingline" id="alg1.l2">2: Return x</div>
          </div>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None
    document = {
        "schema": "papertrans.document-ir",
        "schemaVersion": "1.0",
        "profile": "official_arxiv_html",
        "content": serialize_article(article),
        "units": [],
    }

    blocks = arxiv_document_to_markdown_blocks(document)
    markdown = arxiv_document_to_markdown(document)
    qa = validate_arxiv_markdown(document, blocks, markdown)

    assert qa["status"] == "passed"
    assert qa["source"]["listings"] == qa["output"]["codeBlocks"] == 1
    assert "```text\n1: Set $x$\n2: Return x\n```" in markdown
    assert '<a id="alg1"></a>' not in markdown
    assert '<a id="alg1.l1"></a>' not in markdown
    assert '<a id="alg1.l2"></a>' not in markdown


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
    assert document["schema"] == "papertrans.document-ir"
    assert document["profile"] == "official_arxiv_html"
    assert document["content"]["root"]["tag"] == "article"


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


def test_repairs_sections_nested_by_unclosed_latex_verbatim_code():
    malformed = """
    <article class="ltx_document">
      <section id="S1" class="ltx_section">
        <h2 class="ltx_title ltx_title_section">One</h2>
        <code class="ltx_verbatim"><span class="ltx_inline-block">print(1)</span>
          <section id="S2" class="ltx_section">
            <h2 class="ltx_title ltx_title_section">Two</h2>
            <section id="S2.SS1" class="ltx_subsection">
              <h3 class="ltx_title ltx_title_subsection">Details</h3>
            </section>
          </section>
        </code>
      </section>
    </article>
    """
    soup = BeautifulSoup(malformed, "html.parser")
    article = soup.article
    assert article is not None
    metrics = _repair_section_hierarchy(article)
    assert metrics == {
        "sections": 3,
        "nestedInCodeBefore": 2,
        "nestedInCodeAfter": 0,
        "verbatimNodesMoved": 1,
        "listItemsMoved": 0,
    }
    assert [section.get("id") for section in article.find_all("section", recursive=False)] == [
        "S1",
        "S2",
    ]
    second = article.find("section", id="S2")
    assert second is not None
    assert second.find("section", id="S2.SS1", recursive=False) is not None
    assert not article.select("code section")


def test_section_repair_never_uses_bibliography_as_a_later_section_parent():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <section id="bib" class="ltx_bibliography"><h2>References</h2></section>
          <section id="A0.SS2" class="ltx_subsection"><h3>Appendix details</h3></section>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None

    _repair_section_hierarchy(article)

    appendix = article.find("section", id="A0.SS2")
    assert appendix is not None
    assert appendix.find_parent("section") is None


def test_repairs_list_items_nested_by_unclosed_latex_verbatim_code():
    malformed = """
    <article class="ltx_document"><ul id="I1"><li id="I1.i1"><div>
      <code class="ltx_verbatim"><span class="ltx_inline-block">print(1)</span>
        <li id="I1.i2">Second</li>
      </code>
    </div></li></ul></article>
    """
    soup = BeautifulSoup(malformed, "html.parser")
    article = soup.article
    assert article is not None
    metrics = _repair_section_hierarchy(article)
    items = article.select("#I1 > li")
    assert [item.get("id") for item in items] == ["I1.i1", "I1.i2"]
    assert metrics["verbatimNodesMoved"] == 1
    assert metrics["listItemsMoved"] == 1


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
    assert qa["browserDom"]["sectionsOutsideArticle"] == []
    assert qa["browserDom"]["sectionsNestedInCode"] == []
    assert len([math for math in rendered.find_all("math") if not math.find_parent("details")]) == 1
    assert rendered.find("a", href="#bib.b1") is not None
    layout_css = rendered.style.get_text() if rendered.style is not None else ""
    assert "body{display:block!important" in layout_css
    assert "grid-template-columns:minmax(0,1fr)" in layout_css
    assert ".ptx-main .ltx_table,.ptx-main .ltx_figure_panel:has(table){" in layout_css
    assert "min-width:0;max-width:100%;overflow-x:auto" in layout_css
    assert ".ptx-ja .ltx_cite{white-space:normal}" in layout_css
    assert ".ptx-main .ltx_url{white-space:normal!important" in layout_css
    assert ".ptx-main .ltx_equationgroup" in layout_css
    assert "@media(max-width:1200px)" in layout_css
    assert "width:max-content;min-width:100%" not in layout_css
    assert rendered.find("style", attrs={"data-papertrans-browser-compat": True}) is not None
    artifact_meta = rendered.find("meta", attrs={"name": "papertrans-artifact-version"})
    assert artifact_meta is not None
    assert artifact_meta.get("content") == arxiv_html_artifact_version()
    assert rendered.find("link", href="assets/arxiv-paper.css") is None
    qa_document = json.loads((output / "qa.json").read_text())
    assert qa_document["status"] == "passed"
    assert qa_document["artifactVersion"] == arxiv_html_artifact_version()
    markdown = (output / "index.md").read_text(encoding="utf-8")
    assert "# Model Xの研究" in markdown
    assert "本研究では $x$ を用いる" in markdown
    assert '<a id="m1"></a>' not in markdown
    assert "[Fig. 1](#S1.F1)" in markdown
    assert '<a id="bib.b1"></a>' in markdown
    assert "<svg>" in markdown
    assert "原文を表示" not in markdown
    assert "We study" not in markdown
    assert "[[PTX_" not in markdown
    markdown_qa = json.loads((output / "markdown-qa.json").read_text(encoding="utf-8"))
    assert markdown_qa["status"] == "passed"
    assert markdown_qa["source"]["figures"] == 1
    assert markdown_qa["source"]["math"] == 1
    assert document["schema"] == "papertrans.document-ir"
    assert document["profile"] == "official_arxiv_html"

    (work / "article-normalized.html").unlink()
    regenerated_output = tmp_path / "regenerated"
    render_arxiv_html_document(document, work, regenerated_output)
    assert (regenerated_output / "index.md").read_bytes() == (output / "index.md").read_bytes()
