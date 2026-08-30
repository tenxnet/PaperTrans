import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

import papertrans.cli as cli
from papertrans.pdf_artifacts import write_pdf_job_manifest, write_semantic_pdf_qa


def sample_document() -> dict:
    return {
        "title": {
            "id": "paper-title",
            "original": "Source title",
            "japanese": "日本語題",
            "sourceBlockIds": ["title-source"],
        },
        "frontMatter": {"authors": [{"original": "Ada Example"}]},
        "sections": [
            {
                "title": {"id": "heading-1", "original": "Introduction", "japanese": "序論"},
                "content": [
                    {
                        "type": "unit",
                        "value": {
                            "id": "p-1",
                            "kind": "paragraph",
                            "original": "Body",
                            "japanese": "本文",
                        },
                    },
                    {
                        "type": "unit",
                        "value": {
                            "id": "ref-1",
                            "kind": "reference",
                            "original": "[1] Reference",
                            "japanese": "",
                        },
                    },
                ],
            }
        ],
        "visualObjects": [
            {"kind": "figure"},
            {"kind": "table"},
            {"kind": "equation"},
        ],
        "warnings": [],
    }


def test_writes_source_neutral_pdf_manifest(tmp_path: Path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-fixture")
    manifest_path = tmp_path / "output" / "paper" / "work" / "papertrans-job.json"
    manifest = write_pdf_job_manifest(
        manifest_path,
        slug="paper",
        source=source,
        status="completed",
        pdf_parser="docling",
        structure_mode="docling",
        document=sample_document(),
        started_at="2026-08-30T00:00:00Z",
    )
    assert manifest["sourceType"] == "pdf"
    assert manifest["paper"]["title"] == "日本語題"
    assert manifest["paper"]["authors"] == ["Ada Example"]
    assert manifest["settings"]["pdfParser"] == "docling"
    assert manifest["artifacts"]["html"] == "html/index.html"
    assert manifest["artifacts"]["markdown"] == "html/index.md"
    assert manifest["artifacts"]["markdownQa"] == "html/markdown-qa.json"
    assert all(chunk["status"] == "completed" for chunk in manifest["chunks"])
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["jobId"] == "paper"


def test_pdf_qa_checks_links_assets_and_semantic_counts(tmp_path: Path):
    publication = tmp_path / "html"
    (publication / "assets").mkdir(parents=True)
    (publication / "assets" / "figure.png").write_bytes(b"png")
    (publication / "index.html").write_text(
        '<html><body><h1 id="target">Title</h1><a href="#target">ok</a>'
        '<img src="assets/figure.png"></body></html>',
        encoding="utf-8",
    )
    qa = write_semantic_pdf_qa(
        sample_document(),
        publication,
        pdf_parser="docling",
        evidence={
            "pages": [
                {"pageNumber": 1, "widthPdf": 612, "heightPdf": 792, "blocks": [{"text": "Body"}]}
            ]
        },
    )
    assert qa["status"] == "passed"
    assert qa["output"]["figures"] == 1
    assert qa["output"]["tables"] == 1
    assert qa["output"]["visibleMath"] == 1
    assert qa["output"]["bibliographyEntries"] == 1
    assert qa["output"]["translatedUnits"] == 3
    assert qa["invalidPageGeometry"] == []
    assert qa["missingTitleSource"] is False


def test_pdf_qa_fails_when_a_blank_visual_asset_is_emitted(tmp_path: Path):
    publication = tmp_path / "html"
    assets = publication / "assets"
    assets.mkdir(parents=True)
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    pixmap.clear_with(255)
    pixmap.save(assets / "blank.png")
    (publication / "index.html").write_text(
        '<html><body><img src="assets/blank.png"></body></html>', encoding="utf-8"
    )
    document = sample_document()
    document["visualObjects"] = [
        {
            "objectId": "blank-figure",
            "pageNumber": 1,
            "kind": "figure",
            "asset": "assets/blank.png",
        }
    ]

    qa = write_semantic_pdf_qa(document, publication, pdf_parser="docling")

    assert qa["status"] == "failed"
    assert qa["emittedBlankVisualAssets"] == [
        {
            "objectId": "blank-figure",
            "pageNumber": 1,
            "asset": "assets/blank.png",
        }
    ]


def test_pdf_qa_reports_filtered_blank_visual_without_failing(tmp_path: Path):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text("<html><body></body></html>", encoding="utf-8")
    filtered = {
        "objectId": "blank-figure",
        "pageNumber": 1,
        "kind": "figure",
        "bboxNormalized": [0.1, 0.2, 0.3, 0.4],
        "reason": "Every rendered RGB sample was near-white (>= 250).",
    }

    qa = write_semantic_pdf_qa(
        sample_document(),
        publication,
        pdf_parser="docling",
        structure={"pages": [], "renderDiagnostics": {"filteredBlankVisuals": [filtered]}},
    )

    assert qa["status"] == "passed"
    assert qa["emittedBlankVisualAssets"] == []
    assert qa["filteredBlankVisuals"] == [filtered]


def test_pdf_qa_rejects_docling_title_fallback_without_source_evidence(
    tmp_path: Path,
):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text(
        "<html><body>paper</body></html>", encoding="utf-8"
    )
    document = sample_document()
    document["title"]["original"] = "arxiv-1512-03385"
    document["title"]["sourceBlockIds"] = []

    qa = write_semantic_pdf_qa(
        document,
        publication,
        pdf_parser="docling",
        evidence={
            "pages": [
                {
                    "pageNumber": 1,
                    "widthPdf": 612,
                    "heightPdf": 792,
                    "blocks": [{"text": "Body"}],
                }
            ]
        },
    )

    assert qa["status"] == "failed"
    assert qa["missingTitleSource"] is True


def test_pdf_qa_fails_closed_for_broken_local_references(tmp_path: Path):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text(
        '<html><body><a href="#missing">broken</a><img src="../escape.png"></body></html>',
        encoding="utf-8",
    )
    qa = write_semantic_pdf_qa(sample_document(), publication, pdf_parser="docling")
    assert qa["status"] == "failed"
    assert qa["unresolvedInternalLinks"] == 1
    assert qa["missingLocalAssets"] == ["../escape.png"]


def test_pdf_qa_fails_for_a_visible_unlinked_or_spaced_url(tmp_path: Path):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text(
        "<html><body><p>See https: //example.org/paper.</p></body></html>",
        encoding="utf-8",
    )

    qa = write_semantic_pdf_qa(
        sample_document(), publication, pdf_parser="docling"
    )

    assert qa["status"] == "failed"
    assert qa["unlinkedExternalUrlText"] == [
        "See https: //example.org/paper."
    ]


def test_pdf_qa_rejects_docling_pages_with_missing_geometry(tmp_path: Path):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text("<html><body>paper</body></html>", encoding="utf-8")
    qa = write_semantic_pdf_qa(
        sample_document(),
        publication,
        pdf_parser="docling",
        evidence={
            "pages": [
                {"pageNumber": 1, "widthPdf": 1, "heightPdf": 1, "blocks": []},
                {"pageNumber": 2, "widthPdf": 612, "heightPdf": 792, "blocks": [{"text": "Body"}]},
            ]
        },
    )
    assert qa["status"] == "failed"
    assert qa["invalidPageGeometry"] == [1]
    assert qa["emptyTextPages"] == [1]


def test_pdf_qa_rejects_an_all_text_empty_document(tmp_path: Path):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text("<html><body></body></html>", encoding="utf-8")

    qa = write_semantic_pdf_qa(
        sample_document(),
        publication,
        pdf_parser="pymupdf",
        evidence={
            "pages": [
                {"pageNumber": 1, "widthPdf": 612, "heightPdf": 792, "blocks": []},
                {"pageNumber": 2, "widthPdf": 612, "heightPdf": 792, "blocks": []},
            ]
        },
    )

    assert qa["status"] == "failed"
    assert qa["allTextPagesEmpty"] is True


def test_pdf_qa_allows_a_single_blank_page_but_records_it(tmp_path: Path):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text("<html><body>paper</body></html>", encoding="utf-8")

    qa = write_semantic_pdf_qa(
        sample_document(),
        publication,
        pdf_parser="docling",
        evidence={
            "pages": [
                {"pageNumber": 1, "widthPdf": 612, "heightPdf": 792, "blocks": [{"text": "Body"}]},
                {"pageNumber": 2, "widthPdf": 612, "heightPdf": 792, "blocks": []},
            ]
        },
    )

    assert qa["status"] == "passed"
    assert qa["allTextPagesEmpty"] is False
    assert qa["emptyTextPages"] == [2]


def test_pdf_qa_fails_when_a_semantic_source_block_was_dropped(tmp_path: Path):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text("<html><body>paper</body></html>", encoding="utf-8")
    qa = write_semantic_pdf_qa(
        sample_document(),
        publication,
        pdf_parser="docling",
        evidence={
            "pages": [
                {"pageNumber": 1, "widthPdf": 612, "heightPdf": 792, "blocks": [{"text": "Body"}]}
            ]
        },
        structure={
            "pages": [
                {
                    "blockAssignments": [
                        {
                            "blockId": "missing-body",
                            "role": "paragraph",
                            "hidden": False,
                        }
                    ]
                }
            ]
        },
    )

    assert qa["status"] == "failed"
    assert qa["missingSemanticBlocks"] == ["missing-body"]


def test_pdf_qa_rejects_embedded_visual_text_leaking_into_semantic_output(
    tmp_path: Path,
):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text(
        "<html><body>deception</body></html>", encoding="utf-8"
    )
    document = sample_document()
    document["sections"][0]["content"][0]["value"]["sourceBlockIds"] = [
        "chart-label"
    ]
    qa = write_semantic_pdf_qa(
        document,
        publication,
        pdf_parser="docling",
        evidence={
            "pages": [
                {
                    "pageNumber": 1,
                    "widthPdf": 612,
                    "heightPdf": 792,
                    "blocks": [
                        {
                            "blockId": "chart-label",
                            "text": "deception",
                            "suppressedVisualText": True,
                        }
                    ],
                }
            ]
        },
        structure={
            "pages": [
                {
                    "blockAssignments": [
                        {
                            "blockId": "chart-label",
                            "role": "noise",
                            "hidden": False,
                            "suppressedVisualText": True,
                        }
                    ]
                }
            ]
        },
    )

    assert qa["status"] == "failed"
    assert qa["visualTextDetected"] == 1
    assert qa["visualTextSuppressed"] == 0
    assert qa["leakedVisualTextBlockIds"] == ["chart-label"]


def test_pdf_qa_rejects_caption_conflicts_and_visible_visual_overlap(
    tmp_path: Path,
):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text(
        "<html><body>paper</body></html>", encoding="utf-8"
    )
    document = sample_document()
    document["sections"][0]["content"][0]["value"]["sourceBlockIds"] = [
        "body-overlap",
        "orphan-caption",
    ]
    document["visualObjects"][0]["captionBlockIds"] = ["shared-caption"]
    qa = write_semantic_pdf_qa(
        document,
        publication,
        pdf_parser="docling",
        evidence={
            "pages": [
                {
                    "pageNumber": 1,
                    "widthPdf": 612,
                    "heightPdf": 792,
                    "blocks": [
                        {
                            "blockId": "body-overlap",
                            "text": "axis label",
                            "bboxNormalized": [0.2, 0.2, 0.4, 0.3],
                        },
                        {
                            "blockId": "orphan-caption",
                            "text": "Figure 2. Missing crop.",
                            "bboxNormalized": [0.2, 0.7, 0.8, 0.73],
                        },
                        {
                            "blockId": "shared-caption",
                            "text": "Figure 1. Shared.",
                            "bboxNormalized": [0.2, 0.5, 0.8, 0.53],
                        },
                    ],
                }
            ]
        },
        structure={
            "pages": [
                {
                    "pageNumber": 1,
                    "blockAssignments": [
                        {
                            "blockId": "body-overlap",
                            "role": "paragraph",
                            "hidden": False,
                        },
                        {
                            "blockId": "orphan-caption",
                            "role": "paragraph",
                            "hidden": True,
                            "visualCaptionCandidate": True,
                            "associatedVisualCaption": False,
                        },
                        {
                            "blockId": "shared-caption",
                            "role": "caption",
                            "hidden": False,
                            "visualCaptionCandidate": True,
                            "associatedVisualCaption": True,
                        },
                    ],
                    "visualObjects": [
                        {
                            "objectId": "figure-1",
                            "bboxNormalized": [0.1, 0.1, 0.9, 0.4],
                            "captionBlockIds": ["shared-caption"],
                        },
                        {
                            "objectId": "figure-2",
                            "bboxNormalized": [0.1, 0.55, 0.9, 0.68],
                            "captionBlockIds": ["shared-caption"],
                        },
                    ],
                }
            ]
        },
    )

    assert qa["status"] == "failed"
    assert qa["duplicateVisualCaptionBlockIds"] == ["shared-caption"]
    assert qa["unattachedVisualCaptionBlockIds"] == ["orphan-caption"]
    assert qa["visibleVisualOverlapBlockIds"] == ["body-overlap"]
    assert qa["output"]["figures"] == 1
    assert qa["output"]["tables"] == 1
    assert qa["output"]["visibleMath"] == 1


def test_pdf_qa_fails_closed_for_unresolved_docling_visual_recovery(
    tmp_path: Path,
):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text(
        "<html><body>paper</body></html>", encoding="utf-8"
    )

    qa = write_semantic_pdf_qa(
        sample_document(),
        publication,
        pdf_parser="docling",
        structure={
            "pages": [],
            "doclingDiagnostics": {
                "suppressedInternalCaptionBlockIds": ["internal-caption"],
                "overlargeVisualObjectIds": ["figure-overlarge"],
                "missingCaptionTextOverrideObjectIds": ["figure-interleaved"],
                "unmergedMultiPanelVisualObjectIds": ["figure-left"],
                "danglingParentSectionIds": ["sec-child"],
                "blankVisibleHeadingBlockIds": ["heading-blank"],
                "suppressedBlankHeadingBlockIds": ["heading-metadata"],
                "unabsorbedPanelHeadingBlockIds": ["heading-panel"],
            },
        },
    )

    assert qa["status"] == "failed"
    assert qa["suppressedInternalCaptionBlockIds"] == ["internal-caption"]
    assert qa["overlargeVisualObjectIds"] == ["figure-overlarge"]
    assert qa["missingCaptionTextOverrideObjectIds"] == ["figure-interleaved"]
    assert qa["unmergedMultiPanelVisualObjectIds"] == ["figure-left"]
    assert qa["danglingParentSectionIds"] == ["sec-child"]
    assert qa["blankVisibleHeadingBlockIds"] == ["heading-blank"]
    assert qa["suppressedBlankHeadingBlockIds"] == ["heading-metadata"]
    assert qa["unabsorbedPanelHeadingBlockIds"] == ["heading-panel"]


def test_pdf_qa_rejects_title_only_semantic_output(tmp_path: Path):
    publication = tmp_path / "html"
    publication.mkdir()
    (publication / "index.html").write_text("<html><body>Title</body></html>", encoding="utf-8")
    document = sample_document()
    document["sections"] = []

    qa = write_semantic_pdf_qa(
        document,
        publication,
        pdf_parser="pymupdf",
        evidence={
            "pages": [
                {"pageNumber": 1, "widthPdf": 612, "heightPdf": 792, "blocks": [{"text": "Title"}]}
            ]
        },
    )

    assert qa["status"] == "failed"
    assert qa["output"]["semanticBodyUnits"] == 0


def test_semantic_pipeline_marks_manifest_failed_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-fixture")
    args = SimpleNamespace(
        command="semantic-pipeline",
        repo_root=tmp_path,
        output_root=tmp_path / "output",
        slug="interrupted",
        source=source,
        layout_parser="docling",
        structure_mode="hybrid",
        skip_translation=True,
    )
    monkeypatch.setattr(cli, "_parser", lambda: SimpleNamespace(parse_args=lambda: args))
    monkeypatch.setattr(
        cli,
        "extract_docling_semantics",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        cli.main()

    manifest = json.loads(
        (
            tmp_path
            / "output"
            / "interrupted"
            / "work"
            / "papertrans-job.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
