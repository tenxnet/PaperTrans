from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from papertrans.pdf_translation_worker import validate_ndjson_events
from papertrans_babeldoc_worker import runner
from papertrans_babeldoc_worker.cli import _event_emitter
from papertrans_babeldoc_worker.contract import Limits
from papertrans_babeldoc_worker.contract import ProviderProfile
from papertrans_babeldoc_worker.contract import Request
from papertrans_babeldoc_worker.contract import Source
from papertrans_babeldoc_worker.contract import Translation


def _request(outputs: tuple[str, ...] = ("translated_mono_pdf",)) -> Request:
    return Request(
        schema_version=1,
        run_id="pdf-babeldoc-01",
        source=Source("application/pdf", "a" * 64, 9),
        translation=Translation(
            "en",
            "ja",
            "evaluation-ja-v1",
            "openai-compatible-local",
            "test-model",
            "papertrans-pdf-ja-v1",
            None,
        ),
        outputs=outputs,
        limits=Limits(300, 524_288_000, 1_500),
    )


def test_worker_result_uses_exact_common_five_field_contract() -> None:
    artifacts = [{"role": "translated_mono_pdf"}]
    page_maps = {"translated_mono_pdf": [{"sourcePage": 1, "outputPages": [1]}]}
    result = runner._worker_result(_request(), artifacts, page_maps)

    assert set(result) == {
        "schemaVersion",
        "runId",
        "sourceSha256",
        "artifacts",
        "pageMaps",
    }
    assert result["sourceSha256"] == "a" * 64
    assert result["artifacts"] is artifacts
    assert result["pageMaps"] is page_maps


@pytest.mark.parametrize(
    ("role", "output_pages", "last_mapping"),
    [
        ("translated_mono_pdf", 3, [3]),
        ("translated_dual_pdf", 6, [5, 6]),
    ],
)
def test_copy_pdf_declares_common_page_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    output_pages: int,
    last_mapping: list[int],
) -> None:
    engine_pdf = tmp_path / "engine.pdf"
    engine_pdf.write_bytes(b"%PDF-fake")
    destination = tmp_path / "collected.pdf"
    monkeypatch.setattr(runner, "_inspect_pdf", lambda path, max_pages: output_pages)
    collected = runner._copy_pdf(
        engine_pdf,
        destination,
        role=role,
        source_pages=3,
        max_pages=6,
        max_output_bytes=1024,
    )
    assert destination.read_bytes() == b"%PDF-fake"
    assert collected["pageMap"][-1] == {"sourcePage": 3, "outputPages": last_mapping}


def test_copy_pdf_rejects_wrong_dual_page_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine_pdf = tmp_path / "engine.pdf"
    engine_pdf.write_bytes(b"%PDF-fake")
    destination = tmp_path / "collected.pdf"
    monkeypatch.setattr(runner, "_inspect_pdf", lambda path, max_pages: 3)
    with pytest.raises(runner.PdfFailure) as caught:
        runner._copy_pdf(
            engine_pdf,
            destination,
            role="translated_dual_pdf",
            source_pages=3,
            max_pages=6,
            max_output_bytes=1024,
        )
    assert caught.value.code == "output_page_mismatch"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("role", "source_pages", "expected_max_pages"),
    [
        ("translated_mono_pdf", 300, 300),
        ("translated_dual_pdf", 151, 600),
        ("translated_dual_pdf", 300, 600),
    ],
)
def test_execute_applies_role_specific_output_page_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    source_pages: int,
    expected_max_pages: int,
) -> None:
    request = _request((role,))
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-fake")
    output = tmp_path / "output"
    output.mkdir()
    engine_pdf = tmp_path / "engine.pdf"
    engine_pdf.write_bytes(b"%PDF-engine")
    observed: dict[str, int] = {}

    async def fake_consume_engine(*args, **kwargs):
        del args, kwargs
        return (
            SimpleNamespace(
                no_watermark_mono_pdf_path=engine_pdf,
                no_watermark_dual_pdf_path=engine_pdf,
                total_seconds=1.0,
                peak_memory_usage=1.0,
            ),
            {"upstreamEvents": 1, "progressEvents": 0},
        )

    def fake_copy_pdf(*args, **kwargs):
        del args
        observed["max_pages"] = kwargs["max_pages"]
        return {
            "sha256": "e" * 64,
            "bytes": 1,
            "pageMap": [{"sourcePage": 1, "outputPages": [1]}],
        }

    monkeypatch.setattr(runner, "validate_source", lambda path, value: source_pages)
    monkeypatch.setattr(runner, "_consume_engine", fake_consume_engine)
    monkeypatch.setattr(runner, "_regular_file_inside", lambda path, root: engine_pdf)
    monkeypatch.setattr(runner, "_copy_pdf", fake_copy_pdf)

    asyncio.run(
        runner.execute(
            request,
            source,
            output,
            profile=None,
            emitter=runner.EventEmitter(request.run_id, io.StringIO()),
        )
    )

    assert observed["max_pages"] == expected_max_pages


def test_build_settings_selects_adjacent_alternating_dual_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "pdf2zh_next"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text(
        "class OpenAISettings:\n"
        "    def __init__(self, **kwargs): self.kwargs = kwargs\n"
        "class SettingsModel:\n"
        "    def __init__(self, **kwargs): self.kwargs = kwargs\n",
        encoding="utf-8",
    )
    module_names = ("pdf2zh_next", "pdf2zh_next.config")
    for name in module_names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    profile = ProviderProfile(
        "evaluation-ja-v1",
        "openai-compatible-local",
        "test-model",
        "http://translation-gateway:8080/v1",
        "not-a-real-key",
        1,
    )
    try:
        settings = runner._build_settings(
            _request(("translated_dual_pdf",)), profile, tmp_path / "engine-output"
        )
    finally:
        for name in module_names:
            sys.modules.pop(name, None)

    pdf_settings = settings.kwargs["pdf"]
    assert pdf_settings["use_alternating_pages_dual"] is True
    assert pdf_settings["dual_translate_first"] is False
    assert pdf_settings["no_mono"] is True
    assert pdf_settings["no_dual"] is False


def test_engine_import_and_native_stdio_cannot_corrupt_run_ndjson(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    package = tmp_path / "pdf2zh_next"
    package.mkdir()
    (package / "__init__.py").write_text(
        "import os\nos.write(1, b'package-stdout-noise\\n')\n"
        "os.write(2, b'package-stderr-noise\\n')\n",
        encoding="utf-8",
    )
    (package / "config.py").write_text(
        "import os\nos.write(1, b'config-stdout-noise\\n')\n"
        "os.write(2, b'config-stderr-noise\\n')\n"
        "class OpenAISettings:\n"
        "    def __init__(self, **kwargs): self.kwargs = kwargs\n"
        "class SettingsModel:\n"
        "    def __init__(self, **kwargs): self.kwargs = kwargs\n",
        encoding="utf-8",
    )
    (package / "high_level.py").write_text(
        "import os\nos.write(1, b'high-level-stdout-noise\\n')\n"
        "os.write(2, b'high-level-stderr-noise\\n')\n"
        "class Result: pass\n"
        "async def do_translate_async_stream(settings, file):\n"
        "    os.write(1, b'stream-stdout-noise\\n')\n"
        "    os.write(2, b'stream-stderr-noise\\n')\n"
        "    yield {'type': 'progress_update', 'stage': 'translate', 'overall_progress': 0.5}\n"
        "    yield {'type': 'finish', 'translate_result': Result()}\n",
        encoding="utf-8",
    )
    module_names = (
        "pdf2zh_next",
        "pdf2zh_next.config",
        "pdf2zh_next.high_level",
    )
    for name in module_names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))

    request = _request()
    profile = ProviderProfile(
        "evaluation-ja-v1",
        "openai-compatible-local",
        "test-model",
        "http://translation-gateway:8080/v1",
        "not-a-real-key",
        1,
    )
    emitter = _event_emitter(request.run_id)
    emitter.emit("started", backendId="pdf2zh-next-babeldoc-papertrans")
    try:
        result, counters = asyncio.run(
            runner._consume_engine(
                request,
                tmp_path / "source.pdf",
                tmp_path / "engine-output",
                profile,
                emitter,
            )
        )
    finally:
        emitter.stream.close()
        for name in module_names:
            sys.modules.pop(name, None)

    stdout, stderr = capfd.readouterr()
    assert stderr == ""
    events = validate_ndjson_events(
        stdout,
        run_id=request.run_id,
        require_terminal=False,
    )
    assert [event["type"] for event in events] == ["started", "progress"]
    assert json.loads(stdout.splitlines()[0])["type"] == "started"
    assert result is not None
    assert counters == {"upstreamEvents": 2, "progressEvents": 1}
