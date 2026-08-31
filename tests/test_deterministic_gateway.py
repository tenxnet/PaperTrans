from __future__ import annotations

import importlib.util
from pathlib import Path


GATEWAY_PATH = (
    Path(__file__).resolve().parents[1]
    / "workers"
    / "deterministic-gateway"
    / "gateway.py"
)
SPEC = importlib.util.spec_from_file_location("papertrans_deterministic_gateway", GATEWAY_PATH)
assert SPEC is not None and SPEC.loader is not None
gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway)


def test_translation_marker_is_stable_and_does_not_leak_engine_placeholders() -> None:
    source = "< style id = ' 1 ' >English< / style > { v 2 }"

    translated = gateway._translated_marker(source)

    assert "PaperTransの決定論的E2E翻訳です。" in translated
    assert "< style" not in translated
    assert "< / style" not in translated
    assert "{ v 2 }" not in translated
    assert "English" not in translated


def test_request_content_accepts_only_the_fixed_model_and_extracts_input() -> None:
    request = {
        "model": gateway.MODEL,
        "messages": [
            {
                "role": "user",
                "content": "instruction\nInput:\n\nHello",
            }
        ],
    }

    model, translated = gateway._request_content(request)

    assert model == gateway.MODEL
    assert translated == "PaperTransの決定論的E2E翻訳です。"
    assert gateway._request_content({**request, "model": "other"}) is None
