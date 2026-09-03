from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from papertrans.arxiv_html import (
    ARXIV_ASSET_MANIFEST_FILENAME,
    ArxivAcquisitionLimitError,
    _ArxivRedirectHandler,
    _asset_url_candidates,
    _citation_metadata,
    _decode_local_images,
    _download_assets,
    _arxiv_identity_matches,
    _find_resolved_id,
    _normalize_passive_image,
    _parse_codex_jsonl,
    _publish_local_assets,
    _repair_section_hierarchy,
    _request_bytes,
    _require_arxiv_https_url,
    _run_image_worker,
    _sanitize_tree,
    _section_chunks,
    _tokenize_node,
    _validate_passive_svg,
    _validate_rendered_html,
    _write_asset_manifest,
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
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAACXBIWXMAAA7EAAAOxAGVKw4b"
    "AAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)


def test_normalizes_arxiv_urls_and_versions():
    assert normalize_arxiv_id("https://arxiv.org/abs/2508.19843") == "2508.19843"
    assert normalize_arxiv_id("https://arxiv.org/html/2508.19843v3") == "2508.19843v3"
    assert normalize_arxiv_id("https://arxiv.org/pdf/2508.19843.pdf") == "2508.19843"
    assert normalize_arxiv_id("arXiv:2508.19843v3") == "2508.19843v3"
    assert normalize_arxiv_id("https://arxiv.org/abs/math/0211159") == "math/0211159"
    assert normalize_arxiv_id("arXiv:HEP-TH/9901001V2") == "hep-th/9901001v2"


def test_resolved_arxiv_identity_requires_the_requested_paper_and_revision():
    assert _arxiv_identity_matches("2508.19843", "2508.19843v3")
    assert _arxiv_identity_matches("math/0211159", "math/0211159v2")
    assert _arxiv_identity_matches("2508.19843v3", "2508.19843v3")
    assert not _arxiv_identity_matches("2508.19843", "2405.20947v2")
    assert not _arxiv_identity_matches("2508.19843v2", "2508.19843v3")


def test_resolved_arxiv_id_uses_a_bounded_complete_watermark_token():
    exact = BeautifulSoup(
        '<div id="watermark-tr">arXiv:2508.19843v3 [cs.CR]</div>',
        "html.parser",
    )
    embedded = BeautifulSoup(
        '<div id="watermark-tr">arXiv:12508.19843 [cs.CR]</div>',
        "html.parser",
    )

    assert _find_resolved_id(exact) == "2508.19843v3"
    assert _find_resolved_id(embedded) is None


@pytest.mark.parametrize(
    "value",
    [
        "12508.19843",
        "2599.19843",
        f"2508.19843v{'1' * 80}",
        f"{'a' * 40}/0211159",
        "math/٠٢١١١٥٩",
        "٢٥08.19843",
        "2508.19843v1١",
        "not-arxiv-2508.19843-extra",
        "https://arxiv.org.evil.example/abs/2508.19843",
        "https://user@arxiv.org/abs/2508.19843",
        "http://arxiv.org/abs/2508.19843",
        "https://arxiv.org:444/abs/2508.19843",
        "https://example.com/?id=2508.19843",
    ],
)
def test_rejects_arxiv_identifiers_embedded_in_untrusted_input(value: str):
    with pytest.raises(ValueError, match="invalid arXiv identifier"):
        normalize_arxiv_id(value)


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


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://arxiv.org/html/2405.20947v5/x1.png",
        "https://media.example/x1.png",
        "https://arxiv.org.evil.example/x1.png",
        "https://user@arxiv.org/x1.png",
        "https://arxiv.org:444/x1.png",
    ],
)
def test_arxiv_acquisition_rejects_non_official_origins(url: str):
    with pytest.raises(ValueError, match="official HTTPS origin"):
        _require_arxiv_https_url(url)


def test_arxiv_redirect_is_rejected_before_following_non_official_origin():
    handler = _ArxivRedirectHandler()

    with pytest.raises(ValueError, match="official HTTPS origin"):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "https://metadata.internal/latest",
        )


def test_arxiv_response_limit_checks_headers_and_streamed_bytes(monkeypatch):
    class FakeResponse:
        def __init__(self, payload: bytes, content_length: str | None = None):
            self.payload = payload
            self.headers = {"Content-Type": "application/octet-stream"}
            if content_length is not None:
                self.headers["Content-Length"] = content_length
            self.read_sizes: list[int] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://arxiv.org/html/2405.20947"

        def read(self, size: int):
            self.read_sizes.append(size)
            return self.payload[:size]

    class FakeOpener:
        def __init__(self, response: FakeResponse):
            self.response = response

        def open(self, _request, timeout: int):
            assert timeout == 60
            return self.response

    declared = FakeResponse(b"unused", content_length="5")
    monkeypatch.setattr(
        "papertrans.arxiv_html.urllib.request.build_opener",
        lambda *_handlers: FakeOpener(declared),
    )
    with pytest.raises(ArxivAcquisitionLimitError, match="exceeds 4 bytes"):
        _request_bytes("https://arxiv.org/html/2405.20947", max_bytes=4)
    assert declared.read_sizes == []

    streamed = FakeResponse(b"12345")
    monkeypatch.setattr(
        "papertrans.arxiv_html.urllib.request.build_opener",
        lambda *_handlers: FakeOpener(streamed),
    )
    with pytest.raises(ArxivAcquisitionLimitError, match="exceeds 4 bytes"):
        _request_bytes("https://arxiv.org/html/2405.20947", max_bytes=4)
    assert streamed.read_sizes == [5]

    exact = FakeResponse(b"1234")
    monkeypatch.setattr(
        "papertrans.arxiv_html.urllib.request.build_opener",
        lambda *_handlers: FakeOpener(exact),
    )
    payload, _, _ = _request_bytes(
        "https://arxiv.org/html/2405.20947",
        max_bytes=4,
    )
    assert payload == b"1234"
    assert exact.read_sizes == [5]


def test_arxiv_asset_candidates_reject_local_and_remote_references():
    base = "https://arxiv.org/html/2405.20947v5"

    for raw_url in ("file:///etc/passwd", "//media.example/x1.png"):
        with pytest.raises(ValueError, match="official HTTPS origin"):
            _asset_url_candidates(base, raw_url)
    assert _asset_url_candidates(base, "x1.png") == [
        "https://arxiv.org/html/x1.png",
        "https://arxiv.org/html/2405.20947v5/x1.png",
    ]


def test_asset_download_falls_back_to_arxiv_document_directory(monkeypatch, tmp_path: Path):
    requested: list[str] = []

    def fake_request(url: str, timeout: int = 60, *, max_bytes: int):
        requested.append(url)
        if url == "https://arxiv.org/html/x1.png":
            raise OSError("404")
        return PNG_BYTES, url, "image/png"

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


def test_downloaded_media_uses_magic_and_forces_a_passive_extension(
    monkeypatch,
    tmp_path: Path,
):
    def fake_request(url: str, timeout: int = 60, *, max_bytes: int):
        assert timeout == 60
        assert max_bytes > 0
        if url.endswith("active.html"):
            return b"<script>alert(1)</script>", url, "text/html"
        if url.endswith("vector.png"):
            return b"<svg><script>alert(1)</script></svg>", url, "image/svg+xml"
        if url.endswith("document.png"):
            return b"%PDF-1.7\n", url, "application/pdf"
        return PNG_BYTES, url, "text/html"

    monkeypatch.setattr("papertrans.arxiv_html._request_bytes", fake_request)
    soup = BeautifulSoup(
        (
            '<article><img src="active.html"><img src="vector.png">'
            '<img src="document.png"><img src="figure.html"></article>'
        ),
        "html.parser",
    )
    article = soup.article
    assert article is not None

    result = _download_assets(
        soup,
        article,
        "https://arxiv.org/html/2405.20947v5",
        tmp_path / "assets",
    )

    images = article.find_all("img")
    assert all(image.get("src") is None for image in images[:3])
    localized = str(images[3].get("src"))
    assert localized.endswith(".png")
    assert not any(path.suffix == ".html" for path in (tmp_path / "assets").iterdir())
    assert result["downloaded"][0]["contentType"] == "image/png"
    assert len(result["failures"]) == 3


def test_passive_svg_is_rasterized_to_a_bounded_png():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
        b'<defs><pattern id="p" width="2" height="2" patternUnits="userSpaceOnUse">'
        b'<rect width="2" height="2" fill="blue"/></pattern></defs>'
        b'<rect width="20" height="10" fill="url(#p)"/></svg>'
    )

    normalized, suffix, content_type = _normalize_passive_image(svg)

    assert normalized.startswith(PNG_MAGIC)
    assert suffix == ".png"
    assert content_type == "image/png"


def test_standalone_raster_is_decoded_and_reencoded_as_png():
    normalized, suffix, content_type = _normalize_passive_image(PNG_BYTES)

    assert normalized.startswith(PNG_MAGIC)
    assert suffix == ".png"
    assert content_type == "image/png"


def test_raster_pixel_bomb_is_rejected_before_native_decode():
    oversized = bytearray(PNG_BYTES)
    oversized[16:20] = (0xFFFFFFFF).to_bytes(4, "big")
    oversized[20:24] = (0xFFFFFFFF).to_bytes(4, "big")

    with pytest.raises(ArxivAcquisitionLimitError, match="resource limit"):
        _normalize_passive_image(bytes(oversized))


def test_image_worker_parent_rejects_reported_memory_overage(tmp_path: Path):
    script = (
        "import os,time;"
        "fd=int(os.environ['PAPERTRANS_IMAGE_HEARTBEAT_FD']);"
        "os.write(fd,b'536870913\\n');"
        "time.sleep(5)"
    )

    with pytest.raises(ArxivAcquisitionLimitError, match="memory limit"):
        _run_image_worker([sys.executable, "-I", "-c", script], tmp_path)


def test_image_worker_parent_rejects_stalled_memory_watchdog(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        "papertrans.arxiv_html.ARXIV_IMAGE_WORKER_HEARTBEAT_STALE_SECONDS",
        0.05,
    )
    script = (
        "import os,time;"
        "fd=int(os.environ['PAPERTRANS_IMAGE_HEARTBEAT_FD']);"
        "os.write(fd,b'1024\\n');"
        "time.sleep(5)"
    )

    with pytest.raises(ArxivAcquisitionLimitError, match="unresponsive"):
        _run_image_worker([sys.executable, "-I", "-c", script], tmp_path)


@pytest.mark.parametrize(
    "svg",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://media.example/x.png"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>rect{fill:u/**/rl(//media.example/x.svg)}</style><rect/></svg>',
        b'<!DOCTYPE svg [<!ENTITY x "boom">]><svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>',
        b'<?xml version="1.0"?><?xml-stylesheet href="https://media.example/x.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="https://example.org/foreign"><x:path/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" xml:base="https://media.example/"><use href="#glyph"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><filter id="f"><feTurbulence/></filter></svg>',
    ],
)
def test_active_or_externally_referencing_svg_is_rejected(svg: bytes):
    with pytest.raises(ValueError):
        _normalize_passive_image(svg)


def test_safe_svg_serialization_preserves_xlink_prefix_for_use_elements():
    safe = _validate_passive_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<defs><path id="glyph" d="M0 0L1 1"/></defs>'
        b'<use xlink:href="#glyph"/></svg>'
    )

    assert b'xlink:href="#glyph"' in safe
    assert b"ns1:href" not in safe


def test_asset_download_enforces_request_and_aggregate_budgets(monkeypatch, tmp_path: Path):
    requested: list[str] = []

    def fake_request(url: str, timeout: int = 60, *, max_bytes: int):
        requested.append(url)
        assert len(PNG_BYTES) <= max_bytes
        return PNG_BYTES, url, "image/png"

    monkeypatch.setattr("papertrans.arxiv_html._request_bytes", fake_request)
    monkeypatch.setattr("papertrans.arxiv_html.ARXIV_MAX_ASSET_REQUESTS", 2)
    monkeypatch.setattr(
        "papertrans.arxiv_html.ARXIV_MAX_TOTAL_ASSET_BYTES",
        len(PNG_BYTES),
    )
    soup = BeautifulSoup(
        '<article><img src="one.png"><img src="two.png"></article>',
        "html.parser",
    )
    article = soup.article
    assert article is not None

    result = _download_assets(
        soup,
        article,
        "https://arxiv.org/html/2405.20947v5",
        tmp_path / "assets",
    )

    assert requested == ["https://arxiv.org/html/one.png"]
    assert article.find("img", src=True) is not None
    assert len(article.find_all("img", src=True)) == 1
    assert len(result["downloaded"]) == 1
    assert "aggregate asset limit" in result["failures"][0]["error"]


def test_asset_download_counts_fallback_attempts_and_stops_after_limit(
    monkeypatch,
    tmp_path: Path,
):
    requested: list[str] = []

    def fake_request(url: str, timeout: int = 60, *, max_bytes: int):
        requested.append(url)
        raise OSError("missing")

    monkeypatch.setattr("papertrans.arxiv_html._request_bytes", fake_request)
    monkeypatch.setattr("papertrans.arxiv_html.ARXIV_MAX_ASSET_REQUESTS", 1)
    soup = BeautifulSoup(
        '<article><img src="one.png"><img src="two.png"></article>',
        "html.parser",
    )
    article = soup.article
    assert article is not None

    result = _download_assets(
        soup,
        article,
        "https://arxiv.org/html/2405.20947v5",
        tmp_path / "assets",
    )

    assert requested == ["https://arxiv.org/html/one.png"]
    assert article.find("img", src=True) is None
    assert len(result["failures"]) == 1
    assert "asset request limit" in result["failures"][0]["error"]


def test_asset_download_deduplicates_resolved_media_urls(monkeypatch, tmp_path: Path):
    requested: list[str] = []

    def fake_request(url: str, timeout: int = 60, *, max_bytes: int):
        requested.append(url)
        return PNG_BYTES, url, "image/png"

    monkeypatch.setattr("papertrans.arxiv_html._request_bytes", fake_request)
    soup = BeautifulSoup(
        '<article><img src="same.png"><img src="same.png"></article>',
        "html.parser",
    )
    article = soup.article
    assert article is not None

    result = _download_assets(
        soup,
        article,
        "https://arxiv.org/html/2405.20947v5",
        tmp_path / "assets",
    )

    assert requested == ["https://arxiv.org/html/same.png"]
    sources = [str(image["src"]) for image in article.find_all("img")]
    assert len(set(sources)) == 1
    assert len(result["downloaded"]) == 1


def test_asset_download_deduplicates_overlapping_candidate_urls(
    monkeypatch,
    tmp_path: Path,
):
    requested: list[str] = []

    def fake_request(url: str, timeout: int = 60, *, max_bytes: int):
        requested.append(url)
        return PNG_BYTES, url, "image/png"

    monkeypatch.setattr("papertrans.arxiv_html._request_bytes", fake_request)
    soup = BeautifulSoup(
        '<article><img src="x.png"><img src="/html/x.png"></article>',
        "html.parser",
    )
    article = soup.article
    assert article is not None

    result = _download_assets(
        soup,
        article,
        "https://arxiv.org/html/2405.20947v5",
        tmp_path / "assets",
    )

    assert requested == ["https://arxiv.org/html/x.png"]
    sources = [str(image["src"]) for image in article.find_all("img")]
    assert len(set(sources)) == 1
    assert len(result["downloaded"]) == 1


def test_remote_paper_css_is_not_acquired(monkeypatch, tmp_path: Path):
    requested: list[str] = []

    def fake_request(url: str, timeout: int = 60, *, max_bytes: int):
        requested.append(url)
        raise AssertionError("remote paper CSS must not be requested")

    monkeypatch.setattr("papertrans.arxiv_html._request_bytes", fake_request)
    soup = BeautifulSoup(
        '<link rel="stylesheet" href="arxiv-html-papers.css"><article></article>',
        "html.parser",
    )
    article = soup.article
    assert article is not None

    result = _download_assets(
        soup,
        article,
        "https://arxiv.org/html/2405.20947v5",
        tmp_path / "assets",
    )

    assert requested == []
    assert not (tmp_path / "assets" / "arxiv-paper.css").exists()
    assert result == {"downloaded": [], "failures": []}


def test_failed_asset_download_neutralizes_active_media_attributes(monkeypatch, tmp_path: Path):
    requested: list[str] = []

    def fail_request(url: str, timeout: int = 60):
        requested.append(url)
        raise OSError("unavailable")

    monkeypatch.setattr("papertrans.arxiv_html._request_bytes", fail_request)
    soup = BeautifulSoup(
        """
        <article>
          <img src="https://media.example/missing.png" alt="Missing image">
          <object data="//media.example/missing.pdf" aria-label="Missing object">
            Object fallback text
          </object>
          <img src="data:image/png;base64,AAAA" alt="Embedded image">
          <svg>
            <image href="https://media.example/missing.svg"></image>
            <image xlink:href="//media.example/missing-xlink.svg"></image>
            <image href="#local-symbol"></image>
          </svg>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None

    result = _download_assets(
        soup,
        article,
        "https://arxiv.org/html/2405.20947v5",
        tmp_path / "assets",
    )

    assert requested == []
    assert len(result["failures"]) == 5
    assert article.find("img", alt="Missing image") is not None
    assert article.find("img", alt="Missing image").get("src") is None
    assert article.find("img", alt="Embedded image") is not None
    assert article.find("img", alt="Embedded image").get("src") is None
    missing_object = article.find("object")
    assert missing_object is not None
    assert missing_object.get("data") is None
    assert "Object fallback text" in missing_object.get_text(" ", strip=True)
    svg_images = article.find_all("image")
    assert svg_images[0].get("href") is None
    assert svg_images[1].get("xlink:href") is None
    assert svg_images[2].get("href") == "#local-symbol"


def test_article_sanitizer_removes_active_svg_and_obfuscated_urls():
    soup = BeautifulSoup(
        """
        <article>
          <a id="unsafe" href="java&#10;script:alert(1)">unsafe</a>
          <a id="safe" href="https://arxiv.org/abs/2405.20947" ping="https://tracker.example/ping">safe</a>
          <img id="responsive" src="figure.png" srcset="https://tracker.example/a.png 2x" imagesrcset="https://tracker.example/b.png 3x">
          <embed src="https://arxiv.org/active.html">
          <style>@import "https://media.example/style.css";</style>
          <svg id="based-svg" xml:base="https://media.example/">
            <foreignObject><iframe srcdoc="bad"></iframe></foreignObject>
            <use id="external-use" href="https://media.example/symbol.svg#x"></use>
            <use id="local-use" href="#symbol"></use>
            <textPath id="external-text-path" href="//tracker.example/text">text</textPath>
            <path id="external-fill" fill="url(https://media.example/pattern.svg)"></path>
            <path id="local-fill" fill="url(#pattern)"></path>
            <animate attributeName="href" to="javascript:alert(1)"></animate>
          </svg>
          <div id="external-style" style="background:url(//media.example/pixel.png)"></div>
          <div id="same-origin-style" style="background:image-set('/api/private' 1x)"></div>
          <div id="safe-style" style="width:100%;color:red"></div>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None

    _sanitize_tree(article)

    assert article.find(id="unsafe").get("href") is None
    assert article.find(id="safe").get("href") == "https://arxiv.org/abs/2405.20947"
    assert article.find(id="safe").get("ping") is None
    assert article.find(id="responsive").get("srcset") is None
    assert article.find(id="responsive").get("imagesrcset") is None
    assert article.find("embed") is None
    assert article.find("style") is None
    assert article.find("foreignobject") is None
    assert article.find("animate") is None
    assert article.find(id="based-svg").get("xml:base") is None
    assert article.find(id="external-use").get("href") is None
    assert article.find(id="local-use").get("href") == "#symbol"
    assert article.find(id="external-text-path").get("href") is None
    assert article.find(id="external-fill").get("fill") is None
    assert article.find(id="local-fill").get("fill") == "url(#pattern)"
    assert article.find(id="external-style").get("style") is None
    assert article.find(id="same-origin-style").get("style") is None
    assert article.find(id="safe-style").get("style") == "width:100%;color:red"


def test_article_sanitizer_preserves_visible_foreign_object_content():
    soup = BeautifulSoup(
        """
        <article><svg><foreignObject class="layout">
          <div><code>print(&quot;kept&quot;)</code><script>alert(1)</script></div>
        </foreignObject></svg></article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None

    _sanitize_tree(article)

    preserved = article.find("div", class_="papertrans-foreign-object")
    assert preserved is not None
    assert 'print("kept")' in preserved.get_text()
    assert preserved.find("script") is None


def test_render_boundary_keeps_only_manifest_addressable_media():
    soup = BeautifulSoup(
        """
        <article>
          <img id="local" src="assets/figure.png">
          <img id="remote" src="https://media.example/figure.png">
          <img id="relative" src="figure.png">
          <object id="traversal" data="assets/../secret.html"></object>
          <svg><image id="svg-local" href="assets/vector.png"></image></svg>
        </article>
        """,
        "html.parser",
    )
    article = soup.article
    assert article is not None

    _sanitize_tree(article, local_resources_only=True)

    assert article.find(id="local").get("src") == "assets/figure.png"
    assert article.find(id="svg-local").get("href") == "assets/vector.png"
    assert article.find(id="remote").get("src") is None
    assert article.find(id="relative").get("src") is None
    assert article.find(id="traversal").get("data") is None


def test_publish_local_assets_copies_only_referenced_manifested_png(tmp_path: Path):
    work = tmp_path / "work"
    source_assets = work / "assets"
    source_assets.mkdir(parents=True)
    safe_path = source_assets / "safe.png"
    safe_path.write_bytes(PNG_BYTES)
    (source_assets / "legacy.html").write_text("<script>alert(1)</script>")
    _write_asset_manifest(
        source_assets,
        [
            {
                "path": "assets/safe.png",
                "bytes": len(PNG_BYTES),
                "sha256": hashlib.sha256(PNG_BYTES).hexdigest(),
                "contentType": "image/png",
            }
        ],
    )
    soup = BeautifulSoup(
        '<article><img src="assets/safe.png"></article>',
        "html.parser",
    )
    assert soup.article is not None
    output = tmp_path / "output"
    output.mkdir()

    published = _publish_local_assets(soup.article, work, output)

    assert published == ["assets/safe.png"]
    assert (output / "assets" / "safe.png").read_bytes() == PNG_BYTES
    assert not (output / "assets" / "legacy.html").exists()
    assert not (output / "assets" / ARXIV_ASSET_MANIFEST_FILENAME).exists()


def test_publish_local_assets_rejects_legacy_assets_without_manifest(tmp_path: Path):
    work = tmp_path / "work"
    (work / "assets").mkdir(parents=True)
    (work / "assets" / "legacy.png").write_bytes(PNG_BYTES)
    soup = BeautifulSoup(
        '<article><img src="assets/legacy.png"></article>',
        "html.parser",
    )
    assert soup.article is not None
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(RuntimeError, match="reacquire"):
        _publish_local_assets(soup.article, work, output)


def test_rendered_html_qa_includes_svg_image_assets():
    source = BeautifulSoup(
        '<article class="ltx_document"><svg><image href="assets/a.png">'
        '<image xlink:href="assets/b.png"><image href="#symbol"></svg></article>',
        "html.parser",
    )
    output = BeautifulSoup(f"<html><body>{source.article}</body></html>", "html.parser")
    assert source.article is not None

    qa = _validate_rendered_html(source.article, output)

    assert qa["localAssets"] == ["assets/a.png", "assets/b.png"]


def test_local_image_decode_qa_rejects_corrupt_images(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "broken.png").write_bytes(b"not an image")
    result = _decode_local_images(tmp_path, ["assets/broken.png"])
    assert result["engine"] == "bounded PNG header"
    assert result["checked"] == 0
    assert result["failures"][0]["asset"] == "assets/broken.png"


def test_local_image_decode_qa_rejects_non_normalized_svg(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "figure.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="8">'
        '<rect width="10" height="8" fill="blue"/></svg>',
        encoding="utf-8",
    )
    result = _decode_local_images(tmp_path, ["assets/figure.svg"])
    assert result["engine"] == "bounded PNG header"
    assert result["checked"] == 0
    assert result["failures"][0]["asset"] == "assets/figure.svg"


def test_local_image_decode_qa_accepts_bounded_png(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "figure.png").write_bytes(PNG_BYTES)

    result = _decode_local_images(tmp_path, ["assets/figure.png"])

    assert result == {
        "engine": "bounded PNG header",
        "checked": 1,
        "failures": [],
    }


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


def test_arxiv_markdown_projects_only_local_media_but_keeps_text_and_external_links():
    unsafe = [
        ("http", "http://media.example/inline-http.png"),
        ("https", "https://media.example/inline-https.png"),
        ("scheme", "//media.example/inline-scheme.png"),
        ("data", "data:image/png;base64,AAAA"),
        ("absolute", "/private/inline-absolute.png"),
        ("traversal", "../inline-traversal.png"),
        ("relative", "unlocalized-inline.png"),
    ]
    inline_images = "".join(
        f'<img src="{source}" alt="Inline {name}">' for name, source in unsafe
    )
    figure_media = [
        '<img src="http://media.example/block-http.png" alt="Block http">',
        '<object data="https://media.example/block-https.pdf" aria-label="Block https">Object https fallback</object>',
        '<img src="//media.example/block-scheme.png" alt="Block scheme">',
        '<object data="data:application/pdf;base64,AAAA" aria-label="Block data">Object data fallback</object>',
        '<img src="/private/block-absolute.png" alt="Block absolute">',
        '<object data="../block-traversal.pdf" aria-label="Block traversal">Object traversal fallback</object>',
        '<img src="unlocalized-block.png" alt="Block relative">',
    ]
    figures = "".join(
        f'<figure class="ltx_figure" id="F{index}">{media}'
        f'<figcaption>Caption {index}</figcaption></figure>'
        for index, media in enumerate(figure_media, start=1)
    )
    soup = BeautifulSoup(
        f"""
        <article class="ltx_document">
          <h1>Title</h1>
          <p>{inline_images}</p>
          <p>
            <img src="assets/local-inline.png" alt="Local inline">
            <a href="https://example.org/paper">External paper</a>
          </p>
          <svg>
            <image href="unlocalized-svg.png"></image>
            <image xlink:href="https://media.example/svg-tracker.png"></image>
            <image href="#local-symbol"></image>
          </svg>
          {figures}
          <figure class="ltx_figure" id="Flocal">
            <object data="assets/local-object.svg" aria-label="Local object">Unused fallback</object>
            <figcaption>Local caption</figcaption>
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
    assert qa["unlocalizedMedia"] == []
    assert "![Local inline](assets/local-inline.png)" in markdown
    assert "![Local object](assets/local-object.svg)" in markdown
    assert "[External paper](https://example.org/paper)" in markdown
    assert "unlocalized-svg.png" not in markdown
    assert "https://media.example/svg-tracker.png" not in markdown
    assert 'href="#local-symbol"' in markdown
    for name, source in unsafe:
        assert source not in markdown
        assert f"Inline {name}" in markdown
    for index, media in enumerate(figure_media, start=1):
        source = BeautifulSoup(media, "html.parser").find(True)
        assert source is not None
        unsafe_destination = source.get("src") or source.get("data")
        assert unsafe_destination not in markdown
        assert f"Caption {index}" in markdown
    assert "Block http" in markdown
    assert "Block https" in markdown and "Object https fallback" in markdown
    assert "Block scheme" in markdown
    assert "Block data" in markdown and "Object data fallback" in markdown
    assert "Block absolute" in markdown
    assert "Block traversal" in markdown and "Object traversal fallback" in markdown
    assert "Block relative" in markdown


def test_arxiv_markdown_qa_rejects_residual_unlocalized_media_only():
    soup = BeautifulSoup(
        """
        <article class="ltx_document">
          <h1>Title</h1>
          <p><a href="https://example.org/paper">External paper</a></p>
          <figure class="ltx_figure">
            <img src="assets/local.png" alt="Local">
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

    clean_qa = validate_arxiv_markdown(document, blocks, markdown)
    lazy_only = markdown + '\n<img data-src="https://media.example/lazy.png" alt="Lazy">\n'
    injected = (
        markdown
        + "\n[Another external link](https://example.org/allowed)\n"
        + "\n![Remote tracker](//media.example/tracker.png)\n"
        + "\n![Unlocalized relative](unlocalized.png)\n"
        + '\n<object data="data:application/pdf;base64,AAAA">Fallback</object>\n'
        + '\n<svg><image href="unlocalized-svg.png"></image></svg>\n'
    )
    lazy_qa = validate_arxiv_markdown(document, blocks, lazy_only)
    failed_qa = validate_arxiv_markdown(document, blocks, injected)

    assert clean_qa["status"] == "passed"
    assert clean_qa["unlocalizedMedia"] == []
    assert lazy_qa["status"] == "passed"
    assert lazy_qa["unlocalizedMedia"] == []
    assert failed_qa["status"] == "failed"
    assert failed_qa["unlocalizedMedia"] == [
        "//media.example/tracker.png",
        "data:application/pdf;base64,AAAA",
        "unlocalized-svg.png",
        "unlocalized.png",
    ]
    assert "Markdown output contains remote or unlocalized media" in failed_qa["failures"]


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
    csp_meta = rendered.find(
        "meta",
        attrs={"http-equiv": "Content-Security-Policy"},
    )
    assert csp_meta is not None
    assert "script-src 'none'" in str(csp_meta.get("content"))
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
