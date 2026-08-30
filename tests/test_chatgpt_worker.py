from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import anyio
import pytest

from papertrans import chatgpt_worker
from papertrans.chatgpt_worker import ChatGPTTranslationStore, TranslationJobError
from papertrans.mcp_server import server
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
