from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import papertrans.docling_adapter as adapter
from papertrans.docling_adapter import (
    DoclingAdapterError,
    DoclingUnavailableError,
    docling_document_to_dict,
    docling_document_to_ir,
    extract_docling_semantics,
)
from papertrans.semantic import build_semantic_document
from papertrans.structure import validate_structure_batch


def sample_docling_document() -> dict:
    paragraph = "A claim cites [1] and Figure 1. It continues on page two."
    split = paragraph.index("It continues")
    bottom_left = "BOTTOMLEFT"
    texts = [
        {
            "self_ref": "#/texts/0",
            "label": "title",
            "orig": "Native Docling Paper",
            "text": "Native Docling Paper",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 60, "t": 760, "r": 540, "b": 720, "coord_origin": bottom_left},
                    "charspan": [0, 20],
                }
            ],
        },
        {
            "self_ref": "#/texts/1",
            "label": "paragraph",
            "orig": paragraph,
            "text": paragraph,
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 60, "t": 690, "r": 540, "b": 650, "coord_origin": bottom_left},
                    "charspan": [0, split],
                },
                {
                    "page_no": 2,
                    "bbox": {"l": 60, "t": 760, "r": 540, "b": 720, "coord_origin": bottom_left},
                    "charspan": [split, len(paragraph)],
                },
            ],
        },
        {
            "self_ref": "#/texts/2",
            "label": "section_header",
            "level": 1,
            "orig": "1 Introduction",
            "text": "1 Introduction",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 60, "t": 710, "r": 300, "b": 692, "coord_origin": bottom_left},
                    "charspan": [0, 14],
                }
            ],
        },
        {
            "self_ref": "#/texts/3",
            "label": "caption",
            "orig": "Figure 1: Native visual evidence.",
            "text": "Figure 1: Native visual evidence.",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 90, "t": 230, "r": 510, "b": 205, "coord_origin": bottom_left},
                    "charspan": [0, 33],
                }
            ],
        },
        {
            "self_ref": "#/texts/4",
            "label": "formula",
            "orig": "E = mc²",
            "text": "E = mc²",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 180, "t": 185, "r": 420, "b": 145, "coord_origin": bottom_left},
                    "charspan": [0, 7],
                }
            ],
        },
        {
            "self_ref": "#/texts/5",
            "label": "section_header",
            "level": 2,
            "orig": "1.1 Details",
            "text": "1.1 Details",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 60, "t": 680, "r": 300, "b": 660, "coord_origin": bottom_left},
                    "charspan": [0, 11],
                }
            ],
        },
        {
            "self_ref": "#/texts/6",
            "label": "list_item",
            "orig": "• First exact item",
            "text": "• First exact item",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 70, "t": 640, "r": 350, "b": 615, "coord_origin": bottom_left},
                    "charspan": [0, 18],
                }
            ],
        },
        {
            "self_ref": "#/texts/7",
            "label": "section_header",
            "level": 1,
            "orig": "References",
            "text": "References",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 60, "t": 560, "r": 260, "b": 535, "coord_origin": bottom_left},
                    "charspan": [0, 10],
                }
            ],
        },
        {
            "self_ref": "#/texts/8",
            "label": "reference",
            "orig": "[1] Exact source reference.",
            "text": "[1] Exact source reference.",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 60, "t": 520, "r": 500, "b": 490, "coord_origin": bottom_left},
                    "charspan": [0, 27],
                }
            ],
        },
        {
            "self_ref": "#/texts/9",
            "label": "page_header",
            "content_layer": "furniture",
            "orig": "Running head",
            "text": "Running head",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 60, "t": 795, "r": 220, "b": 780, "coord_origin": bottom_left},
                    "charspan": [0, 12],
                }
            ],
        },
        {
            "self_ref": "#/texts/10",
            "label": "page_footer",
            "content_layer": "furniture",
            "orig": "2",
            "text": "2",
            "prov": [
                {
                    "page_no": 2,
                    "bbox": {"l": 295, "t": 20, "r": 305, "b": 8, "coord_origin": bottom_left},
                    "charspan": [0, 1],
                }
            ],
        },
    ]
    return {
        "schema_name": "DoclingDocument",
        "version": "1.0.0",
        "name": "native-docling-paper",
        "origin": {"filename": "native-docling-paper.pdf"},
        "pages": {
            "1": {"page_no": 1, "size": {"width": 600, "height": 800}},
            "2": {"page_no": 2, "size": {"width": 600, "height": 800}},
        },
        "body": {
            "self_ref": "#/body",
            "children": [{"$ref": "#/texts/0"}, {"$ref": "#/groups/0"}],
        },
        "furniture": {
            "self_ref": "#/furniture",
            "children": [{"$ref": "#/texts/9"}, {"$ref": "#/texts/10"}],
        },
        "groups": [
            {
                "self_ref": "#/groups/0",
                "label": "section",
                # Deliberately differs from texts[] order: the graph is authoritative.
                "children": [
                    {"$ref": "#/texts/2"},
                    {"$ref": "#/texts/1"},
                    {"$ref": "#/pictures/0"},
                    {"$ref": "#/texts/3"},
                    {"$ref": "#/texts/4"},
                    {"$ref": "#/texts/5"},
                    {"$ref": "#/texts/6"},
                    {"$ref": "#/texts/7"},
                    {"$ref": "#/texts/8"},
                ],
            }
        ],
        "texts": texts,
        "pictures": [
            {
                "self_ref": "#/pictures/0",
                "label": "picture",
                "captions": [{"$ref": "#/texts/3"}],
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {"l": 60, "t": 500, "r": 540, "b": 250, "coord_origin": bottom_left},
                        "charspan": [0, 0],
                    }
                ],
            }
        ],
        "tables": [],
        "key_value_items": [],
    }


def _assignments(structure: dict) -> dict[str, dict]:
    return {
        assignment["blockId"]: assignment
        for page in structure["pages"]
        for assignment in page["blockAssignments"]
    }


def _rendered_visuals(evidence: dict, structure: dict) -> list[dict]:
    page_sizes = {
        page["pageNumber"]: (page["widthPdf"], page["heightPdf"])
        for page in evidence["pages"]
    }
    values = []
    for page in structure["pages"]:
        width, height = page_sizes[page["pageNumber"]]
        for visual in page["visualObjects"]:
            x0, y0, x1, y1 = visual["bboxNormalized"]
            values.append(
                {
                    **visual,
                    "pageNumber": page["pageNumber"],
                    "asset": f"assets/{visual['objectId']}.png",
                    "bboxPdf": [x0 * width, y0 * height, x1 * width, y1 * height],
                }
            )
    return values


def test_maps_native_docling_graph_to_existing_ir_without_markdown() -> None:
    evidence, structure = docling_document_to_ir(
        sample_docling_document(), source_file="requested.pdf"
    )

    assert evidence["sourceFile"] == "requested.pdf"
    assert evidence["extractionEngine"] == "docling"
    validate_structure_batch(evidence["pages"], structure)
    assert [block["text"] for block in evidence["pages"][0]["blocks"][:3]] == [
        "Native Docling Paper",
        "1 Introduction",
        "A claim cites [1] and Figure 1.",
    ]

    assignments = _assignments(structure)
    first = assignments["dl-texts-1"]
    continued = assignments["dl-texts-1-s2"]
    assert first["paragraphId"] == continued["paragraphId"]
    assert continued["continuesFrom"] == "dl-texts-1"
    assert first["citations"] == ["[1]"]
    assert first["objectReferences"] == ["Figure 1"]
    assert assignments["dl-texts-8"]["referenceLabel"] == "1"
    assert assignments["dl-texts-9"]["role"] == "header"
    assert assignments["dl-texts-9"]["hidden"] is True
    assert assignments["dl-texts-10"]["hidden"] is True

    section_by_id = {section["sectionId"]: section for section in structure["sections"]}
    assert section_by_id["sec-texts-5"]["parentSectionId"] == "sec-texts-2"
    assert section_by_id["sec-texts-2"]["number"] == "1"
    assert section_by_id["sec-texts-5"]["number"] == "1.1"

    visuals = structure["pages"][0]["visualObjects"]
    picture = next(value for value in visuals if value["kind"] == "figure")
    assert picture["bboxNormalized"] == [0.1, 0.375, 0.9, 0.6875]
    assert picture["captionBlockIds"] == ["dl-texts-3"]
    assert picture["label"] == "Figure 1"
    assert picture["insertAfterBlockId"] == "dl-texts-1"
    assert any(value["kind"] == "equation" for value in visuals)


def test_multi_page_character_spans_preserve_exact_text_and_reject_gaps() -> None:
    document = sample_docling_document()
    paragraph = document["texts"][1]
    paragraph["orig"] = paragraph["text"] = "  Alpha Beta  "
    paragraph["prov"][0]["charspan"] = [2, 7]
    paragraph["prov"][1]["charspan"] = [8, 12]

    evidence, structure = docling_document_to_ir(document)
    blocks = {
        block["blockId"]: block
        for page in evidence["pages"]
        for block in page["blocks"]
    }
    assert blocks["dl-texts-1"]["text"] == "Alpha"
    assert blocks["dl-texts-1-s2"]["text"] == "Beta"
    assert _assignments(structure)["dl-texts-1-s2"]["continuesFrom"] == "dl-texts-1"

    paragraph["prov"][1]["charspan"] = [9, 12]
    evidence, structure = docling_document_to_ir(document)
    blocks = {
        block["blockId"]: block
        for page in evidence["pages"]
        for block in page["blocks"]
    }
    assert blocks["dl-texts-1"]["text"] == "Alpha Beta"
    assert "dl-texts-1-s2" not in blocks
    assert any(
        "complete, ordered character spans" in warning
        for warning in _assignments(structure)["dl-texts-1"]["warnings"]
    )


def test_promotes_top_unnumbered_first_heading_to_missing_title() -> None:
    document = sample_docling_document()
    title = document["texts"][0]
    title["label"] = "section_header"
    title["orig"] = title["text"] = "A Simple Paper"
    title["prov"][0]["charspan"] = [0, len("A Simple Paper")]

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)

    assert assignments["dl-texts-0"]["role"] == "title"
    assert [section["number"] for section in structure["sections"][:2]] == ["1", "1.1"]
    assert "sec-texts-0" not in {section["sectionId"] for section in structure["sections"]}
    assert adapter._heading_details("A Simple Paper", {}) == (None, 1)
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    assert semantic["title"]["original"] == "A Simple Paper"


def test_heading_number_excludes_delimiter_and_sets_real_depth() -> None:
    assert adapter._heading_details("1. Introduction", {"level": 1}) == ("1", 1)
    assert adapter._heading_details("3.1. Details", {"level": 1}) == ("3.1", 2)
    assert adapter._heading_details("Appendix A. Supplement", {"level": 1}) == ("A", 1)
    assert adapter._heading_details("Appendix A.2. Proof", {"level": 1}) == ("A.2", 2)


def test_existing_semantic_builder_accepts_adapter_outputs() -> None:
    evidence, structure = docling_document_to_ir(sample_docling_document())
    document = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )

    assert document["title"]["original"] == "Native Docling Paper"
    introduction = next(section for section in document["sections"] if section["number"] == "1")
    paragraph = next(
        item["value"]
        for item in introduction["content"]
        if item["type"] == "unit" and item["value"]["kind"] == "paragraph"
    )
    assert paragraph["sourceBlockIds"] == ["dl-texts-1", "dl-texts-1-s2"]
    assert paragraph["pages"] == [1, 2]
    assert paragraph["citations"] == ["[1]"]
    figure = next(value for value in document["visualObjects"] if value["kind"] == "figure")
    assert figure["caption"] == "Figure 1: Native visual evidence."
    assert "Running head" not in json.dumps(document)


def test_accepts_mapping_json_and_docling_document_protocol() -> None:
    document = sample_docling_document()
    exported = docling_document_to_dict(
        SimpleNamespace(export_to_dict=lambda: document)
    )
    parsed = docling_document_to_dict(json.dumps(document))

    assert exported == document
    assert parsed == document
    exported["name"] = "changed"
    assert document["name"] == "native-docling-paper"
    with pytest.raises(DoclingAdapterError, match="Expected"):
        docling_document_to_dict(object())


def test_docling_import_is_lazy_and_reports_optional_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(name: str):
        assert name == "docling.document_converter"
        raise ModuleNotFoundError("No module named 'docling'", name="docling")

    monkeypatch.setattr(adapter.importlib, "import_module", unavailable)
    with pytest.raises(DoclingUnavailableError, match="uv sync --extra docling"):
        adapter.convert_pdf_with_docling(Path("paper.pdf"))


def test_converter_explicitly_disables_ocr_for_digital_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeAcceleratorDevice:
        CPU = "cpu"

    class FakeAcceleratorOptions:
        def __init__(self, *, device: str):
            observed["accelerator_device"] = device
            self.device = device

    class FakeOnnxRuntimeObjectDetectionEngineOptions:
        def __init__(self, *, providers: list[str]):
            observed["onnx_providers"] = providers
            self.providers = providers

    class FakeLayoutObjectDetectionOptions:
        def __init__(
            self, *, engine_options: FakeOnnxRuntimeObjectDetectionEngineOptions
        ):
            observed["layout_engine_options"] = engine_options
            self.engine_options = engine_options

    class FakeHeadingHierarchyOptions:
        def __init__(self, *, enabled: bool):
            observed["heading_hierarchy_enabled"] = enabled
            self.enabled = enabled

    class FakePdfPipelineOptions:
        def __init__(
            self,
            *,
            do_ocr: bool,
            document_timeout: float,
            artifacts_path: Path,
            accelerator_options: FakeAcceleratorOptions,
            layout_options: FakeLayoutObjectDetectionOptions,
            heading_hierarchy_options: FakeHeadingHierarchyOptions,
            generate_parsed_pages: bool,
        ):
            observed["pipeline_kwargs"] = {
                "do_ocr": do_ocr,
                "document_timeout": document_timeout,
                "artifacts_path": artifacts_path,
                "accelerator_options": accelerator_options,
                "layout_options": layout_options,
                "heading_hierarchy_options": heading_hierarchy_options,
                "generate_parsed_pages": generate_parsed_pages,
            }
            self.do_ocr = do_ocr

    class FakePdfFormatOption:
        def __init__(self, *, pipeline_options: FakePdfPipelineOptions):
            observed["pipeline_options"] = pipeline_options

    class FakeInputFormat:
        PDF = "pdf"

    class FakeConversionStatus:
        SUCCESS = "success"

    class FakeDocumentConverter:
        def __init__(self, *, format_options: dict):
            observed["format_options"] = format_options

        def convert(self, source: Path, *, raises_on_error: bool):
            observed["source"] = source
            observed["raises_on_error"] = raises_on_error
            return SimpleNamespace(
                status=FakeConversionStatus.SUCCESS,
                errors=[],
                document=SimpleNamespace(
                    export_to_dict=lambda: sample_docling_document()
                )
            )

    modules = {
        "docling.document_converter": SimpleNamespace(
            DocumentConverter=FakeDocumentConverter,
            PdfFormatOption=FakePdfFormatOption,
        ),
        "docling.datamodel.base_models": SimpleNamespace(
            InputFormat=FakeInputFormat,
            ConversionStatus=FakeConversionStatus,
        ),
        "docling.datamodel.accelerator_options": SimpleNamespace(
            AcceleratorDevice=FakeAcceleratorDevice,
            AcceleratorOptions=FakeAcceleratorOptions,
        ),
        "docling.datamodel.object_detection_engine_options": SimpleNamespace(
            OnnxRuntimeObjectDetectionEngineOptions=(
                FakeOnnxRuntimeObjectDetectionEngineOptions
            )
        ),
        "docling.datamodel.pipeline_options": SimpleNamespace(
            PdfPipelineOptions=FakePdfPipelineOptions,
            LayoutObjectDetectionOptions=FakeLayoutObjectDetectionOptions,
            HeadingHierarchyOptions=FakeHeadingHierarchyOptions,
        ),
        "onnxruntime": SimpleNamespace(),
    }
    monkeypatch.setattr(adapter.importlib, "import_module", modules.__getitem__)
    monkeypatch.setenv("PAPERTRANS_DOCLING_ARTIFACTS_PATH", "/models/docling")
    monkeypatch.setenv("PAPERTRANS_DOCLING_DOCUMENT_TIMEOUT", "123.5")

    result = adapter.convert_pdf_with_docling(Path("paper.pdf"))

    assert result["schema_name"] == "DoclingDocument"
    pipeline_kwargs = observed["pipeline_kwargs"]
    assert pipeline_kwargs["do_ocr"] is False
    assert pipeline_kwargs["document_timeout"] == 123.5
    assert pipeline_kwargs["artifacts_path"] == Path("/models/docling")
    assert pipeline_kwargs["accelerator_options"].device == FakeAcceleratorDevice.CPU
    assert (
        pipeline_kwargs["layout_options"].engine_options.providers
        == ["CPUExecutionProvider"]
    )
    assert pipeline_kwargs["heading_hierarchy_options"].enabled is True
    assert pipeline_kwargs["generate_parsed_pages"] is True
    assert observed["accelerator_device"] == FakeAcceleratorDevice.CPU
    assert observed["onnx_providers"] == ["CPUExecutionProvider"]
    assert observed["heading_hierarchy_enabled"] is True
    assert observed["source"] == Path("paper.pdf")
    assert observed["raises_on_error"] is False
    format_options = observed["format_options"]
    assert isinstance(format_options, dict)
    assert list(format_options) == [FakeInputFormat.PDF]
    assert format_options[FakeInputFormat.PDF].__class__ is FakePdfFormatOption
    assert observed["pipeline_options"].do_ocr is False


def test_converter_fails_closed_without_onnx_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def modules(name: str):
        if name == "onnxruntime":
            raise ModuleNotFoundError("No module named 'onnxruntime'", name="onnxruntime")
        return SimpleNamespace()

    monkeypatch.setattr(adapter.importlib, "import_module", modules)
    with pytest.raises(DoclingUnavailableError, match=r"uv sync --extra docling"):
        adapter.convert_pdf_with_docling(Path("paper.pdf"))


def test_converter_rejects_partial_success_with_sanitized_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Option:
        def __init__(self, **_kwargs):
            pass

    class InputFormat:
        PDF = "pdf"

    class ConversionStatus:
        SUCCESS = "success"
        PARTIAL_SUCCESS = "partial_success"

    class Converter:
        def __init__(self, **_kwargs):
            pass

        def convert(self, _source: Path, *, raises_on_error: bool):
            assert raises_on_error is False
            return SimpleNamespace(
                status=ConversionStatus.PARTIAL_SUCCESS,
                errors=[SimpleNamespace(error_message="page 1\nfailed\x00to parse")],
                document=SimpleNamespace(export_to_dict=lambda: sample_docling_document()),
            )

    modules = {
        "docling.document_converter": SimpleNamespace(
            DocumentConverter=Converter,
            PdfFormatOption=Option,
        ),
        "docling.datamodel.base_models": SimpleNamespace(
            InputFormat=InputFormat,
            ConversionStatus=ConversionStatus,
        ),
        "docling.datamodel.accelerator_options": SimpleNamespace(
            AcceleratorDevice=SimpleNamespace(CPU="cpu"),
            AcceleratorOptions=Option,
        ),
        "docling.datamodel.object_detection_engine_options": SimpleNamespace(
            OnnxRuntimeObjectDetectionEngineOptions=Option
        ),
        "docling.datamodel.pipeline_options": SimpleNamespace(
            PdfPipelineOptions=Option,
            LayoutObjectDetectionOptions=Option,
            HeadingHierarchyOptions=Option,
        ),
        "onnxruntime": SimpleNamespace(),
    }
    monkeypatch.setattr(adapter.importlib, "import_module", modules.__getitem__)

    with pytest.raises(
        DoclingAdapterError,
        match="conversion status partial_success: page 1 failed to parse",
    ) as captured:
        adapter.convert_pdf_with_docling(Path("paper.pdf"))
    assert "\n" not in str(captured.value)
    assert "\x00" not in str(captured.value)


def test_runtime_conversion_rejects_zero_size_but_allows_one_blank_page() -> None:
    document = sample_docling_document()
    document["pages"]["1"]["size"] = {"width": 0, "height": 0}
    document["texts"] = [
        item
        for item in document["texts"]
        if not any(prov.get("page_no") == 1 for prov in item.get("prov", []))
    ]
    document["pictures"] = []

    issues = adapter._runtime_document_issues(document)

    assert issues == ["page 1 (zero or missing page size)"]

    document["pages"]["1"]["size"] = {"width": 612, "height": 792}
    assert adapter._runtime_document_issues(document) == []


def test_runtime_conversion_rejects_document_without_textual_body() -> None:
    document = sample_docling_document()
    document["texts"] = []

    assert adapter._runtime_document_issues(document) == [
        "document (no textual body content)"
    ]


def test_preserves_unclassified_front_matter_as_translatable_preamble() -> None:
    document = sample_docling_document()
    front = {
        "self_ref": "#/texts/11",
        "label": "text",
        "orig": "Ada Lovelace · Example University",
        "text": "Ada Lovelace · Example University",
        "prov": [
            {
                "page_no": 1,
                "bbox": {"l": 80, "t": 715, "r": 520, "b": 700, "coord_origin": "BOTTOMLEFT"},
                "charspan": [0, 33],
            }
        ],
    }
    document["texts"].append(front)
    document["body"]["children"].insert(1, {"$ref": "#/texts/11"})

    evidence, structure = docling_document_to_ir(document)
    assignment = _assignments(structure)["dl-texts-11"]
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )

    assert assignment["role"] == "paragraph"
    preamble = semantic["sections"][0]
    assert preamble["title"]["original"] == "Preamble"
    assert preamble["content"][0]["value"]["original"] == front["orig"]


def test_joins_strong_cross_page_continuation_across_text_items() -> None:
    document = sample_docling_document()
    paragraph = document["texts"][1]
    paragraph["orig"] = paragraph["text"] = "A paragraph continues"
    paragraph["prov"] = [paragraph["prov"][0]]
    paragraph["prov"][0]["charspan"] = [0, len(paragraph["text"])]
    continuation = {
        "self_ref": "#/texts/11",
        "label": "paragraph",
        "orig": "across a separate Docling text item.",
        "text": "across a separate Docling text item.",
        "prov": [
            {
                "page_no": 2,
                "bbox": {"l": 60, "t": 760, "r": 540, "b": 720, "coord_origin": "BOTTOMLEFT"},
                "charspan": [0, 36],
            }
        ],
    }
    document["texts"].append(continuation)
    children = document["groups"][0]["children"]
    children.insert(children.index({"$ref": "#/texts/5"}), {"$ref": "#/texts/11"})

    evidence, structure = docling_document_to_ir(document)
    assignments = _assignments(structure)

    assert assignments["dl-texts-11"]["paragraphId"] == assignments["dl-texts-1"]["paragraphId"]
    assert assignments["dl-texts-11"]["continuesFrom"] == "dl-texts-1"
    semantic = build_semantic_document(
        evidence, structure, _rendered_visuals(evidence, structure)
    )
    introduction = next(section for section in semantic["sections"] if section["number"] == "1")
    merged = next(
        item["value"]
        for item in introduction["content"]
        if item["type"] == "unit" and item["value"]["id"] == assignments["dl-texts-1"]["paragraphId"]
    )
    assert merged["sourceBlockIds"] == ["dl-texts-1", "dl-texts-11"]


def test_parent_runs_worker_without_stdout_protocol_and_reads_atomic_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    work_dir = tmp_path / "work"
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        kwargs["stderr"].write(b"worker diagnostic\n")
        worker_output = Path(command[-1])
        observed["worker_output"] = worker_output
        worker_output.write_text(
            json.dumps(sample_docling_document()), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter._run_docling_worker(source, work_dir, timeout_seconds=12.5)

    command = observed["command"]
    assert command[:3] == [adapter.sys.executable, "-m", "papertrans.docling_worker"]
    assert command[3] == str(source)
    worker_output = Path(command[4])
    assert worker_output.parent == work_dir
    assert worker_output.name.startswith(".docling-document-")
    assert worker_output.suffix == ".json"
    assert observed["stdout"] is adapter.subprocess.DEVNULL
    assert observed["cwd"] == work_dir.resolve()
    assert observed["check"] is False
    assert observed["timeout"] == 12.5
    assert (work_dir / "docling-worker.log").read_text() == "worker diagnostic\n"
    assert not worker_output.exists()
    assert json.loads((work_dir / "docling-document.json").read_text()) == result
    assert result["schema_name"] == "DoclingDocument"


def test_parent_rejects_nonfinite_worker_timeout_without_starting_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("worker must not start")

    monkeypatch.setattr(adapter.subprocess, "run", unexpected_run)
    with pytest.raises(DoclingAdapterError, match="positive number"):
        adapter._run_docling_worker(
            tmp_path / "paper.pdf", tmp_path / "work", timeout_seconds=float("nan")
        )


def test_parent_caps_worker_stderr_and_raises_for_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"
    tail = b"FINAL-DIAGNOSTIC"

    def failed_run(_command: list[str], **kwargs):
        kwargs["stderr"].write(
            b"x" * (adapter._DOCLING_WORKER_LOG_LIMIT_BYTES + 200) + tail
        )
        return SimpleNamespace(returncode=139)

    monkeypatch.setattr(adapter.subprocess, "run", failed_run)
    with pytest.raises(adapter.DoclingWorkerError, match="status 139"):
        adapter._run_docling_worker(tmp_path / "paper.pdf", work_dir)

    log = (work_dir / "docling-worker.log").read_bytes()
    assert len(log) <= adapter._DOCLING_WORKER_LOG_LIMIT_BYTES
    assert log.startswith(b"[PaperTrans truncated ")
    assert log.endswith(tail)
    notice, retained = log.split(b"\n", 1)
    omitted = int(notice.removeprefix(b"[PaperTrans truncated ").split(b" ", 1)[0])
    assert omitted == adapter._DOCLING_WORKER_LOG_LIMIT_BYTES + 200 + len(tail) - len(retained)


def test_parent_turns_worker_timeout_into_specific_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"

    def timed_out(command: list[str], **kwargs):
        kwargs["stderr"].write(b"last worker line\n")
        raise adapter.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(adapter.subprocess, "run", timed_out)
    with pytest.raises(adapter.DoclingWorkerTimeoutError, match="3 seconds"):
        adapter._run_docling_worker(
            tmp_path / "paper.pdf", work_dir, timeout_seconds=3
        )
    log = (work_dir / "docling-worker.log").read_text()
    assert "last worker line" in log
    assert "timed out after 3 seconds" in log


def test_parent_removes_published_and_temporary_worker_output_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"
    observed: dict[str, Path] = {}

    def timed_out(command: list[str], **kwargs):
        worker_output = Path(command[-1])
        observed["output"] = worker_output
        worker_output.write_text('{"partial": true}', encoding="utf-8")
        worker_temp = worker_output.parent / f".{worker_output.name}.fixture.tmp"
        observed["temp"] = worker_temp
        worker_temp.write_text("partial", encoding="utf-8")
        raise adapter.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(adapter.subprocess, "run", timed_out)
    with pytest.raises(adapter.DoclingWorkerTimeoutError):
        adapter._run_docling_worker(tmp_path / "paper.pdf", work_dir, timeout_seconds=3)

    assert not observed["output"].exists()
    assert not observed["temp"].exists()


def test_parent_cleans_worker_output_and_reraises_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"
    observed: dict[str, Path] = {}

    def interrupted(command: list[str], **_kwargs):
        worker_output = Path(command[-1])
        observed["output"] = worker_output
        worker_output.write_text('{"partial": true}', encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(adapter.subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        adapter._run_docling_worker(tmp_path / "paper.pdf", work_dir)

    assert not observed["output"].exists()


def test_parent_cleans_worker_output_when_log_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = tmp_path / "work"
    observed: dict[str, Path] = {}

    def completed(command: list[str], **_kwargs):
        worker_output = Path(command[-1])
        observed["output"] = worker_output
        worker_output.write_text(
            json.dumps(sample_docling_document()), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    original_write_bytes = Path.write_bytes

    def fail_log_write(path: Path, data: bytes) -> int:
        if path.name == "docling-worker.log":
            raise OSError("disk full")
        return original_write_bytes(path, data)

    monkeypatch.setattr(adapter.subprocess, "run", completed)
    monkeypatch.setattr(Path, "write_bytes", fail_log_write)
    with pytest.raises(OSError, match="disk full"):
        adapter._run_docling_worker(tmp_path / "paper.pdf", work_dir)

    assert not observed["output"].exists()


def test_extract_api_persists_three_semantic_pipeline_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    work_dir = tmp_path / "work"
    evidence_path = work_dir / "layout-evidence.json"
    structure_path = work_dir / "structure.json"
    visuals_path = work_dir / "visual-objects.json"
    expected_document = sample_docling_document()
    observed: dict[str, object] = {}

    def fake_worker(value: Path, worker_dir: Path, *, timeout_seconds: float):
        observed["worker_source"] = value
        observed["worker_dir"] = worker_dir
        observed["worker_timeout"] = timeout_seconds
        return expected_document

    monkeypatch.setattr(adapter, "_run_docling_worker", fake_worker)

    def fake_render(value: Path, structure: dict, assets_dir: Path) -> list[dict]:
        observed["source"] = value
        observed["assets"] = assets_dir
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else None
        # Rendering happens before persistence, so construct only the fields the
        # downstream semantic builder requires.
        page_sizes = {1: (600, 800), 2: (600, 800)}
        rendered = []
        for page in structure["pages"]:
            width, height = page_sizes[page["pageNumber"]]
            for visual in page["visualObjects"]:
                x0, y0, x1, y1 = visual["bboxNormalized"]
                rendered.append(
                    {
                        **visual,
                        "pageNumber": page["pageNumber"],
                        "asset": f"assets/{visual['objectId']}.png",
                        "bboxPdf": [x0 * width, y0 * height, x1 * width, y1 * height],
                    }
                )
        assert evidence is None
        return rendered

    monkeypatch.setattr(adapter, "render_visual_objects", fake_render)
    evidence, structure, visuals = extract_docling_semantics(
        source, work_dir, evidence_path, structure_path, visuals_path
    )

    assert observed == {
        "worker_source": source.resolve(),
        "worker_dir": work_dir,
        "worker_timeout": None,
        "source": source.resolve(),
        "assets": work_dir / "assets",
    }
    assert json.loads(evidence_path.read_text()) == evidence
    assert json.loads(structure_path.read_text()) == structure
    assert json.loads(visuals_path.read_text()) == visuals
    build_semantic_document(evidence, structure, visuals)
