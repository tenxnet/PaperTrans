from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import anyio
import pymupdf
import pytest

from papertrans import chatgpt_worker
from papertrans.chatgpt_worker import ChatGPTTranslationStore, TranslationJobError
from papertrans.mcp_server import server
from papertrans.pdf_artifacts import write_pdf_job_manifest
from papertrans.render import arxiv_html_artifact_version


ARTICLE = f"""
<article class="ltx_document">
  <h1 class="ltx_title ltx_title_document">A Stateful Translation Test</h1>
  <section id="S1" class="ltx_section">
    <h2 class="ltx_title ltx_title_section">1 First Section</h2>
    <p id="S1.p1" class="ltx_p">{'Alpha ' * 110}<math><mi>x</mi></math>.</p>
    <figure id="S1.F1" class="ltx_figure"><svg><path d="M0 0"></path></svg><figcaption>Figure 1 stays in English.</figcaption></figure>
  </section>
  <section id="S2" class="ltx_section">
    <h2 class="ltx_title ltx_title_section">2 Second Section</h2>
    <p id="S2.p1" class="ltx_p">{'Beta ' * 130}</p>
  </section>
  <section id="bib" class="ltx_bibliography">
    <h2 class="ltx_title ltx_title_bibliography">References</h2>
    <ul><li id="bib.b1" class="ltx_bibitem">[1] Original reference.</li></ul>
  </section>
</article>
"""


def _fake_acquire(
    arxiv_id: str,
    work_dir: Path,
    repo_root: Path,
    metrics_path: Path | None = None,
) -> dict:
    del repo_root, metrics_path
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "article-source.html").write_text(ARTICLE, encoding="utf-8")
    acquisition = {
        "requestedArxivId": arxiv_id,
        "resolvedArxivId": f"{arxiv_id}v1",
        "sourceUrl": f"https://arxiv.org/html/{arxiv_id}v1",
        "sourceSha256": "0" * 64,
        "license": "CC BY 4.0",
        "metadata": {
            "authors": ["Ada Lovelace", "Alan Turing"],
            "publishedAt": "17 Nov 2025",
        },
        "validation": {
            "status": "passed",
            "title": "A Stateful Translation Test",
            "sections": 3,
            "paragraphs": 2,
            "figures": 1,
            "tables": 0,
            "math": 1,
            "bibliographyEntries": 1,
        },
    }
    (work_dir / "acquisition.json").write_text(json.dumps(acquisition), encoding="utf-8")
    (work_dir / "source-route.json").write_text(
        json.dumps({"status": "selected", "selected": {"route": "official_arxiv_html"}}),
        encoding="utf-8",
    )
    return acquisition


def _translations(chunk: dict) -> list[dict]:
    return [
        {
            "blockId": block["blockId"],
            "japanese": f"日本語訳：{block['text']}",
            "preservedTerms": [],
            "warnings": [],
        }
        for block in chunk["blocks"]
    ]


def _pdf_unit(
    block_id: str,
    kind: str,
    original: str,
    *,
    section_id: str | None = None,
) -> dict:
    return {
        "id": block_id,
        "kind": kind,
        "sectionId": section_id,
        "original": original,
        "japanese": "",
        "sourceBlockIds": [f"source-{block_id}"],
        "pages": [1],
        "citations": ["[1]"] if "[1]" in original else [],
        "objectReferences": [],
        "externalLinks": [],
        "referenceLabel": None,
        "preservedTerms": [],
        "warnings": [],
    }


def _prepare_pdf_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    data_root: Path | None = None,
) -> ChatGPTTranslationStore:
    job_id = "pdf-mcp"
    selected_data_root = data_root or tmp_path / "data"
    source = selected_data_root / "papers" / job_id / "source.pdf"
    source.parent.mkdir(parents=True)
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "LASA PDF MCP fixture")
    pdf.save(source)
    pdf.close()

    title = _pdf_unit("paper-title", "title", "LASA PDF MCP Test")
    heading = _pdf_unit("heading-sec-1", "heading", "1 Introduction", section_id="sec-1")
    paragraph = _pdf_unit(
        "para-1",
        "paragraph",
        "LASA preserves multilingual safety behavior [1].",
        section_id="sec-1",
    )
    document = {
        "version": 3,
        "sourceFile": "source.pdf",
        "pageCount": 1,
        "sourcePageCount": 1,
        "partial": False,
        "status": "structured",
        "model": {"translation": None, "translationReasoningEffort": None},
        "title": title,
        "frontMatter": {"authors": [], "affiliations": [], "metadata": []},
        "sections": [
            {
                "id": "sec-1",
                "number": "1",
                "level": 1,
                "parentSectionId": None,
                "pageStart": 1,
                "title": heading,
                "content": [{"type": "unit", "position": 2, "value": paragraph}],
            }
        ],
        "visualObjects": [],
        "warnings": [],
        "glossary": [],
    }
    work = tmp_path / "output" / job_id / "work"
    work.mkdir(parents=True)
    (work / "semantic-document.json").write_text(
        json.dumps(document), encoding="utf-8"
    )
    (work / "layout-evidence.json").write_text("{}", encoding="utf-8")
    (work / "structure.json").write_text("{}", encoding="utf-8")
    write_pdf_job_manifest(
        work / "papertrans-job.json",
        slug=job_id,
        source=source,
        status="prepared",
        pdf_parser="docling",
        structure_mode="docling",
        document=document,
    )

    def fake_pdf_qa(
        _document: dict,
        publication_dir: Path,
        **_kwargs,
    ) -> dict:
        qa = {
            "status": "passed",
            "output": {"figures": 0, "tables": 0, "visibleMath": 0},
            "unresolvedInternalLinks": 0,
            "missingLocalAssets": [],
        }
        (publication_dir / "qa.json").write_text(json.dumps(qa), encoding="utf-8")
        return qa

    monkeypatch.setattr(chatgpt_worker, "write_semantic_pdf_qa", fake_pdf_qa)
    if data_root is None:
        return ChatGPTTranslationStore(tmp_path, tmp_path / "output")
    return ChatGPTTranslationStore(tmp_path, tmp_path / "output", selected_data_root)


def test_default_job_id_is_safe_for_legacy_arxiv_identifiers():
    assert chatgpt_worker._default_job_id("math/0211159") == "arxiv-math-0211159-mcp"


def test_chatgpt_worker_persists_validates_resumes_and_finalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(chatgpt_worker, "acquire_official_arxiv_html", _fake_acquire)
    store = ChatGPTTranslationStore(tmp_path, tmp_path / "output")
    prepared = store.prepare("2508.19843", "paper-chatgpt", max_characters=1000)
    assert prepared["status"] == "prepared"
    assert prepared["targetLanguage"] == "ja"
    assert prepared["chunks"] == {"completed": 0, "total": 2, "remaining": 2}
    assert prepared["paper"]["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert prepared["paper"]["publishedAt"] == "17 Nov 2025"

    first = store.next_chunk("paper-chatgpt")
    assert first["chunkId"] == "chunk-001"
    assert "[[PTX_0001]]" in " ".join(block["text"] for block in first["blocks"])

    invalid = _translations(first)
    for entry in invalid:
        entry["japanese"] = entry["japanese"].replace("[[PTX_0001]]", "")
    with pytest.raises(TranslationJobError, match="placeholder mismatch"):
        store.save_chunk("paper-chatgpt", first["chunkId"], invalid)

    valid = _translations(first)
    saved = store.save_chunk("paper-chatgpt", first["chunkId"], valid)
    assert saved["chunks"]["remaining"] == 1
    replay = store.save_chunk("paper-chatgpt", first["chunkId"], valid)
    assert replay["idempotentReplay"] is True

    manifest_path = tmp_path / "output/paper-chatgpt/work/mcp-job.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["settings"]["targetLanguage"] == "ja"
    assert json.loads(
        (tmp_path / "output/paper-chatgpt/work/html-document.json").read_text(encoding="utf-8")
    )["targetLanguage"] == "ja"
    before_read = manifest_path.read_text(encoding="utf-8")
    restarted = ChatGPTTranslationStore(tmp_path, tmp_path / "output")
    assert restarted.status("paper-chatgpt")["chunks"]["completed"] == 1
    assert manifest_path.read_text(encoding="utf-8") == before_read
    assert restarted.list_jobs()[0]["jobId"] == "paper-chatgpt"

    second = restarted.next_chunk("paper-chatgpt")
    assert second["chunkId"] == "chunk-002"
    restarted.save_chunk("paper-chatgpt", second["chunkId"], _translations(second))
    assert restarted.status("paper-chatgpt")["status"] == "ready_to_finalize"

    finalized = restarted.finalize("paper-chatgpt")
    assert finalized["status"] == "completed"
    assert finalized["usage"]["available"] is False
    assert Path(finalized["indexPath"]).exists()
    assert '<html lang="ja">' in Path(finalized["indexPath"]).read_text(encoding="utf-8")
    assert Path(finalized["markdownPath"]).exists()
    assert "日本語訳" in Path(finalized["markdownPath"]).read_text(encoding="utf-8")
    assert Path(finalized["bundlePath"]).exists()
    assert json.loads(Path(finalized["qaPath"]).read_text(encoding="utf-8"))["status"] == "passed"
    assert json.loads(Path(finalized["markdownQaPath"]).read_text(encoding="utf-8"))["status"] == "passed"
    finalized_html = Path(finalized["indexPath"]).read_bytes()
    assert b"data-papertrans-browser-compat" in finalized_html
    assert b"papertrans-artifact-version" in finalized_html
    assert b"body{display:block!important" in finalized_html
    assert b"assets/arxiv-paper.css" not in finalized_html
    with ZipFile(finalized["bundlePath"]) as archive:
        assert archive.read("index.html") == finalized_html
        assert archive.read("index.md") == Path(finalized["markdownPath"]).read_bytes()
        assert "qa.json" in archive.namelist()
    finalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert finalized_manifest["artifacts"]["rendererVersion"] == arxiv_html_artifact_version()
    assert finalized_manifest["artifacts"]["markdownPath"] == finalized["markdownPath"]
    assert restarted.finalize("paper-chatgpt") == finalized
    assert restarted.status("paper-chatgpt")["status"] == "completed"

    Path(finalized["indexPath"]).write_text("stale artifact", encoding="utf-8")
    finalized_manifest["artifacts"]["rendererVersion"] = "1-stale"
    manifest_path.write_text(json.dumps(finalized_manifest), encoding="utf-8")
    assert restarted.status("paper-chatgpt")["status"] == "ready_to_finalize"

    refreshed = restarted.finalize("paper-chatgpt")
    refreshed_html = Path(refreshed["indexPath"]).read_bytes()
    assert b"stale artifact" not in refreshed_html
    assert b"data-papertrans-browser-compat" in refreshed_html
    with ZipFile(refreshed["bundlePath"]) as archive:
        assert archive.read("index.html") == refreshed_html
    refreshed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert refreshed_manifest["artifacts"]["rendererVersion"] == arxiv_html_artifact_version()


def test_prepare_failure_cleans_staging_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    attempts = 0

    def flaky_acquire(
        arxiv_id: str,
        work_dir: Path,
        repo_root: Path,
        metrics_path: Path | None = None,
    ) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / "partial-download").write_text("partial", encoding="utf-8")
            raise RuntimeError("synthetic acquisition failure")
        return _fake_acquire(arxiv_id, work_dir, repo_root, metrics_path)

    monkeypatch.setattr(chatgpt_worker, "acquire_official_arxiv_html", flaky_acquire)
    output_root = tmp_path / "output"
    store = ChatGPTTranslationStore(tmp_path, output_root)

    with pytest.raises(RuntimeError, match="synthetic acquisition failure"):
        store.prepare("2508.19843", "retryable-job")

    assert not (output_root / "retryable-job").exists()
    assert not list(output_root.glob(".retryable-job.mcp-prepare-*"))

    prepared = store.prepare("2508.19843", "retryable-job")

    assert prepared["status"] == "prepared"
    assert attempts == 2
    assert (output_root / "retryable-job/work/mcp-job.json").is_file()
    assert not list(output_root.glob(".retryable-job.mcp-prepare-*"))


def test_prepare_serializes_concurrent_calls_for_the_same_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    start_barrier = threading.Barrier(2)
    acquisition_started = threading.Event()
    allow_acquisition = threading.Event()
    concurrent_acquisition = threading.Event()
    attempts_lock = threading.Lock()
    attempts = 0

    def blocking_acquire(
        arxiv_id: str,
        work_dir: Path,
        repo_root: Path,
        metrics_path: Path | None = None,
    ) -> dict:
        nonlocal attempts
        with attempts_lock:
            attempts += 1
            attempt = attempts
        if attempt == 1:
            acquisition_started.set()
            assert allow_acquisition.wait(timeout=2)
        else:
            concurrent_acquisition.set()
        return _fake_acquire(arxiv_id, work_dir, repo_root, metrics_path)

    monkeypatch.setattr(
        chatgpt_worker,
        "acquire_official_arxiv_html",
        blocking_acquire,
    )
    store = ChatGPTTranslationStore(tmp_path, tmp_path / "output")

    def prepare() -> dict:
        start_barrier.wait(timeout=2)
        return store.prepare("2508.19843", "concurrent-job")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(prepare) for _ in range(2)]
        assert acquisition_started.wait(timeout=2)
        assert not concurrent_acquisition.wait(timeout=0.1)
        allow_acquisition.set()
        results = [future.result(timeout=2) for future in futures]

    assert attempts == 1
    assert [result["status"] for result in results] == ["prepared", "prepared"]


def test_concurrent_distinct_chunk_saves_are_serialized_without_lost_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(chatgpt_worker, "acquire_official_arxiv_html", _fake_acquire)
    store = ChatGPTTranslationStore(tmp_path, tmp_path / "output")
    prepared = store.prepare("2508.19843", "concurrent-save", max_characters=1000)
    assert prepared["chunks"]["total"] == 2
    chunks = [
        store.next_chunk("concurrent-save", f"chunk-{index:03d}")
        for index in (1, 2)
    ]

    original_atomic_write = chatgpt_worker._atomic_write_json
    active_writes = 0
    writes_guard = threading.Lock()
    overlapping_write = threading.Event()

    def slow_atomic_write(path: Path, value: dict) -> None:
        nonlocal active_writes
        with writes_guard:
            active_writes += 1
            if active_writes > 1:
                overlapping_write.set()
        try:
            time.sleep(0.03)
            original_atomic_write(path, value)
        finally:
            with writes_guard:
                active_writes -= 1

    monkeypatch.setattr(chatgpt_worker, "_atomic_write_json", slow_atomic_write)
    start_barrier = threading.Barrier(2)

    def save(chunk: dict) -> dict:
        start_barrier.wait(timeout=2)
        return store.save_chunk(
            "concurrent-save",
            chunk["chunkId"],
            _translations(chunk),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, chunks))

    assert not overlapping_write.is_set()
    assert all(result["chunks"]["completed"] >= 1 for result in results)
    assert store.status("concurrent-save")["status"] == "ready_to_finalize"
    document = json.loads(
        (tmp_path / "output/concurrent-save/work/html-document.json").read_text(
            encoding="utf-8"
        )
    )
    translated_ids = {
        block["blockId"] for chunk in chunks for block in chunk["blocks"]
    }
    units_by_id = {unit["id"]: unit for unit in document["units"]}
    assert all(units_by_id[block_id].get("japanese") for block_id in translated_ids)


@pytest.mark.parametrize(
    "failed_write",
    ["html-document.json", "chunk-001.json", "mcp-job.json"],
)
def test_html_chunk_retry_repairs_every_partial_write_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_write: str,
):
    monkeypatch.setattr(chatgpt_worker, "acquire_official_arxiv_html", _fake_acquire)
    store = ChatGPTTranslationStore(tmp_path, tmp_path / "output")
    store.prepare("2508.19843", "retry-save", max_characters=1000)
    chunk = store.next_chunk("retry-save", "chunk-001")
    translations = _translations(chunk)

    original_atomic_write = chatgpt_worker._atomic_write_json
    failed = False

    def fail_once(path: Path, value: dict) -> None:
        nonlocal failed
        if path.name == failed_write and not failed:
            failed = True
            raise OSError(f"transient failure writing {failed_write}")
        original_atomic_write(path, value)

    monkeypatch.setattr(chatgpt_worker, "_atomic_write_json", fail_once)
    with pytest.raises(OSError, match="transient failure"):
        store.save_chunk("retry-save", "chunk-001", translations)

    retried = store.save_chunk("retry-save", "chunk-001", translations)

    work = tmp_path / "output/retry-save/work"
    result = json.loads(
        (work / "chatgpt-translations/chunk-001.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((work / "mcp-job.json").read_text(encoding="utf-8"))
    document = json.loads(
        (work / "html-document.json").read_text(encoding="utf-8")
    )
    units_by_id = {unit["id"]: unit for unit in document["units"]}

    assert failed
    assert result["translations"] == translations
    assert manifest["status"] == retried["status"] == "translating"
    assert manifest["chunks"][0]["status"] == "completed"
    assert all(units_by_id[item["blockId"]]["japanese"] for item in translations)


def test_prepare_preserves_manifestless_nonempty_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output_root = tmp_path / "output"
    job_root = output_root / "owned-by-user"
    job_root.mkdir(parents=True)
    marker = job_root / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        chatgpt_worker,
        "acquire_official_arxiv_html",
        lambda *_args, **_kwargs: pytest.fail("acquisition must not start"),
    )
    store = ChatGPTTranslationStore(tmp_path, output_root)

    with pytest.raises(TranslationJobError, match="already exists without"):
        store.prepare("2508.19843", "owned-by-user")

    assert marker.read_text(encoding="utf-8") == "keep"


def test_mcp_worker_translates_prepared_docling_pdf_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = _prepare_pdf_job(tmp_path, monkeypatch)

    status = store.status("pdf-mcp")
    assert status["status"] == "prepared"
    assert status["chunks"] == {"completed": 0, "total": 1, "remaining": 1}
    assert store.list_jobs()[0]["jobId"] == "pdf-mcp"

    chunk = store.next_chunk("pdf-mcp")
    assert chunk["chunkId"] == "chunk-001"
    assert [block["blockId"] for block in chunk["blocks"]] == [
        "paper-title",
        "heading-sec-1",
        "para-1",
    ]
    assert "PDF paper text" in chunk["translationInstructions"]

    saved = store.save_chunk("pdf-mcp", "chunk-001", _translations(chunk))
    assert saved["status"] == "ready_to_finalize"
    assert saved["chunks"]["remaining"] == 0
    mcp_manifest = json.loads(
        (tmp_path / "output/pdf-mcp/work/mcp-job.json").read_text(encoding="utf-8")
    )
    assert mcp_manifest["sourceType"] == "pdf"
    common_manifest = json.loads(
        (tmp_path / "output/pdf-mcp/work/papertrans-job.json").read_text(
            encoding="utf-8"
        )
    )
    assert common_manifest["provider"] == "mcp"
    assert common_manifest["status"] == "ready_to_finalize"

    finalized = store.finalize("pdf-mcp")
    assert finalized["status"] == "completed"
    assert Path(finalized["indexPath"]).is_file()
    assert Path(finalized["markdownPath"]).is_file()
    assert Path(finalized["bundlePath"]).is_file()
    assert json.loads(Path(finalized["markdownQaPath"]).read_text(encoding="utf-8"))[
        "status"
    ] == "passed"
    with ZipFile(finalized["bundlePath"]) as archive:
        assert "index.html" in archive.namelist()
        assert "index.md" in archive.namelist()
        assert "source.pdf" in archive.namelist()
    finalized_common = json.loads(
        (tmp_path / "output/pdf-mcp/work/papertrans-job.json").read_text(
            encoding="utf-8"
        )
    )
    assert finalized_common["provider"] == "mcp"
    assert finalized_common["status"] == "completed"
    completed_updated_at = finalized_common["updatedAt"]
    completed_finalized_at = finalized_common["finalizedAt"]
    finalized_common["status"] = "preparing"
    (tmp_path / "output/pdf-mcp/work/papertrans-job.json").write_text(
        json.dumps(finalized_common), encoding="utf-8"
    )
    assert store.finalize("pdf-mcp") == finalized
    repaired_common = json.loads(
        (tmp_path / "output/pdf-mcp/work/papertrans-job.json").read_text(
            encoding="utf-8"
        )
    )
    assert repaired_common["status"] == "completed"
    assert repaired_common["updatedAt"] == completed_updated_at
    assert repaired_common["finalizedAt"] == completed_finalized_at


@pytest.mark.parametrize(
    "failed_write",
    [
        "semantic-document.json",
        "chunk-001.json",
        "mcp-job.json",
        "papertrans-job.json",
    ],
)
def test_pdf_chunk_retry_repairs_every_partial_write_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_write: str,
):
    store = _prepare_pdf_job(tmp_path, monkeypatch)
    chunk = store.next_chunk("pdf-mcp")
    translations = _translations(chunk)
    original_atomic_write = chatgpt_worker._atomic_write_json
    original_pdf_manifest_write = chatgpt_worker.write_pdf_job_manifest
    failed = False

    def fail_atomic_once(path: Path, value: dict) -> None:
        nonlocal failed
        if path.name == failed_write and not failed:
            failed = True
            raise OSError(f"transient failure writing {failed_write}")
        original_atomic_write(path, value)

    def fail_pdf_manifest_once(path: Path, **kwargs) -> dict:
        nonlocal failed
        if path.name == failed_write and not failed:
            failed = True
            raise OSError(f"transient failure writing {failed_write}")
        return original_pdf_manifest_write(path, **kwargs)

    monkeypatch.setattr(chatgpt_worker, "_atomic_write_json", fail_atomic_once)
    monkeypatch.setattr(
        chatgpt_worker,
        "write_pdf_job_manifest",
        fail_pdf_manifest_once,
    )
    with pytest.raises(OSError, match="transient failure"):
        store.save_chunk("pdf-mcp", "chunk-001", translations)

    retried = store.save_chunk("pdf-mcp", "chunk-001", translations)

    work = tmp_path / "output/pdf-mcp/work"
    result = json.loads(
        (work / "chatgpt-translations/chunk-001.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((work / "mcp-job.json").read_text(encoding="utf-8"))
    common_manifest = json.loads(
        (work / "papertrans-job.json").read_text(encoding="utf-8")
    )
    document = json.loads(
        (work / "semantic-document.json").read_text(encoding="utf-8")
    )
    units_by_id = chatgpt_worker._pdf_unit_map(document)

    assert failed
    assert result["translations"] == translations
    assert manifest["status"] == retried["status"] == "ready_to_finalize"
    assert manifest["chunks"][0]["status"] == "completed"
    assert common_manifest["status"] == "ready_to_finalize"
    assert common_manifest["provider"] == "mcp"
    assert all(units_by_id[item["blockId"]]["japanese"] for item in translations)


def test_mcp_worker_reads_pdf_from_custom_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    custom_data_root = tmp_path / "custom-data"
    store = _prepare_pdf_job(
        tmp_path,
        monkeypatch,
        data_root=custom_data_root,
    )

    chunk = store.next_chunk("pdf-mcp")

    assert store.data_root == custom_data_root.resolve()
    assert chunk["chunkId"] == "chunk-001"
    assert (custom_data_root / "papers/pdf-mcp/source.pdf").is_file()
    assert not (tmp_path / "data/papers/pdf-mcp/source.pdf").exists()


def test_mcp_worker_does_not_fallback_to_repo_data_for_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _prepare_pdf_job(tmp_path, monkeypatch)
    repo_source = tmp_path / "data/papers/pdf-mcp/source.pdf"
    custom_data_root = tmp_path / "custom-data"
    custom_data_root.mkdir()
    store = ChatGPTTranslationStore(
        tmp_path,
        tmp_path / "output",
        custom_data_root,
    )

    assert repo_source.is_file()
    with pytest.raises(
        TranslationJobError,
        match="missing from the configured data root",
    ):
        store.next_chunk("pdf-mcp")


def test_mcp_pdf_finalize_marks_empty_text_pages_for_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = _prepare_pdf_job(tmp_path, monkeypatch)
    chunk = store.next_chunk("pdf-mcp")
    store.save_chunk("pdf-mcp", chunk["chunkId"], _translations(chunk))

    def qa_with_blank_page(_document: dict, publication_dir: Path, **_kwargs) -> dict:
        qa = {
            "status": "passed",
            "emptyTextPages": [2],
            "output": {"figures": 0, "tables": 0, "visibleMath": 0},
            "unresolvedInternalLinks": 0,
            "missingLocalAssets": [],
        }
        (publication_dir / "qa.json").write_text(json.dumps(qa), encoding="utf-8")
        return qa

    monkeypatch.setattr(chatgpt_worker, "write_semantic_pdf_qa", qa_with_blank_page)

    finalized = store.finalize("pdf-mcp")

    assert finalized["status"] == "needs_review"
    document = json.loads(
        (tmp_path / "output/pdf-mcp/work/semantic-document.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["status"] == "needs_review"
    common = json.loads(
        (tmp_path / "output/pdf-mcp/work/papertrans-job.json").read_text(
            encoding="utf-8"
        )
    )
    assert common["status"] == "needs_review"


@pytest.mark.parametrize("status", ["preparing", "translating", "failed"])
def test_mcp_worker_rejects_pdf_job_before_prepared_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
):
    store = _prepare_pdf_job(tmp_path, monkeypatch)
    manifest_path = tmp_path / "output/pdf-mcp/work/papertrans-job.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = status
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TranslationJobError, match="not ready for MCP translation"):
        store.status("pdf-mcp")
    assert store.list_jobs() == []
    assert not (tmp_path / "output/pdf-mcp/work/mcp-job.json").exists()


def test_mcp_worker_accepts_legacy_review_job_after_artifact_qa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = _prepare_pdf_job(tmp_path, monkeypatch)
    manifest_path = tmp_path / "output/pdf-mcp/work/papertrans-job.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "needs_review"
    manifest["provider"] = "none"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    qa_path = tmp_path / "output/pdf-mcp/html/qa.json"
    qa_path.parent.mkdir(parents=True)
    qa_path.write_text(json.dumps({"status": "passed"}), encoding="utf-8")

    status = store.status("pdf-mcp")

    assert status["status"] == "prepared"
    assert status["chunks"]["remaining"] == 1


def test_mcp_worker_rejects_pdf_source_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = _prepare_pdf_job(tmp_path, monkeypatch)
    source = tmp_path / "data/papers/pdf-mcp/source.pdf"
    source.write_bytes(b"%PDF-1.7\nchanged")

    with pytest.raises(TranslationJobError, match="source hash"):
        store.next_chunk("pdf-mcp")
    assert not (tmp_path / "output/pdf-mcp/work/mcp-job.json").exists()


def test_chatgpt_worker_rejects_unsupported_target_language(tmp_path: Path):
    store = ChatGPTTranslationStore(tmp_path, tmp_path / "output")
    with pytest.raises(TranslationJobError, match="PaperTrans v1"):
        store.prepare("2508.19843", target_language="en")


def test_mcp_job_defaults_are_provider_neutral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(chatgpt_worker, "acquire_official_arxiv_html", _fake_acquire)
    store = ChatGPTTranslationStore(tmp_path, tmp_path / "output")
    prepared = store.prepare("2508.19843")

    assert prepared["jobId"] == "arxiv-2508.19843-mcp"
    manifest = json.loads(
        (tmp_path / "output/arxiv-2508.19843-mcp/work/mcp-job.json").read_text(
            encoding="utf-8"
        )
    )
    document = json.loads(
        (tmp_path / "output/arxiv-2508.19843-mcp/work/html-document.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["provider"] == "mcp"
    assert document["model"]["translation"] == "mcp-worker"


def test_mcp_tools_expose_structured_schemas_and_accurate_annotations():
    async def inspect_tools():
        return await server.list_tools()

    tools = {tool.name: tool for tool in anyio.run(inspect_tools)}
    assert set(tools) == {
        "prepare_arxiv_translation",
        "list_translation_jobs",
        "get_translation_status",
        "get_translation_chunk",
        "save_translation_chunk",
        "finalize_translation_html",
    }
    assert tools["prepare_arxiv_translation"].annotations.open_world_hint is True
    assert tools["get_translation_chunk"].annotations.read_only_hint is True
    assert tools["save_translation_chunk"].annotations.read_only_hint is False
    assert tools["save_translation_chunk"].annotations.destructive_hint is True
    assert tools["save_translation_chunk"].output_schema is not None
