from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest


transformers = pytest.importorskip(
    "transformers",
    reason="run with --group docling to exercise the application dependency",
)


def _tokenizer_with_templates(templates: dict[str, str]):
    from transformers.tokenization_utils_base import PreTrainedTokenizerBase

    tokenizer = object.__new__(PreTrainedTokenizerBase)
    tokenizer.chat_template = templates
    return tokenizer


def test_docling_group_uses_security_fixed_transformers() -> None:
    assert importlib.metadata.version("transformers") == "5.10.4"


def test_named_chat_template_cannot_escape_save_directory(tmp_path: Path) -> None:
    save_directory = tmp_path / "save"
    save_directory.mkdir()
    tokenizer = _tokenizer_with_templates(
        {"default": "safe", "../../PWNED": "attacker content"}
    )

    with pytest.raises(ValueError, match="Invalid chat template name"):
        tokenizer.save_chat_templates(save_directory, {}, None, True)

    assert not (tmp_path / "PWNED.jinja").exists()


def test_legitimate_named_chat_template_still_saves(tmp_path: Path) -> None:
    save_directory = tmp_path / "save"
    save_directory.mkdir()
    tokenizer = _tokenizer_with_templates(
        {"default": "safe", "alternate": "legitimate"}
    )

    _config, saved_files = tokenizer.save_chat_templates(
        save_directory, {}, None, True
    )

    alternate = next(
        path for path in map(Path, saved_files) if path.name == "alternate.jinja"
    )
    assert alternate.resolve().is_relative_to(save_directory.resolve())
    assert alternate.read_text(encoding="utf-8") == "legitimate"
