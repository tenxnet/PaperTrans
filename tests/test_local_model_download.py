from __future__ import annotations

import json
from pathlib import Path

import pytest

from papertrans.local_model_download import ModelDownloadError, download_locked_models


def _lock(tmp_path: Path) -> Path:
    path = tmp_path / "models.lock.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "repository": "docling-project/layout",
                        "revision": "a" * 40,
                        "directory": "docling-project--layout",
                        "allowPatterns": ["config.json", "weights/model.safetensors"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_download_uses_exact_revision_subset_and_no_token(tmp_path: Path) -> None:
    lock = _lock(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    calls: list[dict[str, object]] = []

    download_locked_models(lock, output, downloader=lambda **kwargs: calls.append(kwargs))

    assert calls == [
        {
            "repo_id": "docling-project/layout",
            "revision": "a" * 40,
            "local_dir": output / "docling-project--layout",
            "allow_patterns": ["config.json", "weights/model.safetensors"],
            "token": False,
            "max_workers": 4,
        }
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "main"),
        ("directory", "../escape"),
        ("allowPatterns", ["../secret"]),
        ("repository", "https://example.invalid/model"),
    ],
)
def test_download_rejects_unpinned_or_unsafe_source(
    tmp_path: Path, field: str, value: object
) -> None:
    lock = _lock(tmp_path)
    document = json.loads(lock.read_text(encoding="utf-8"))
    document["sources"][0][field] = value
    lock.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(ModelDownloadError, match="unsafe or unpinned"):
        download_locked_models(lock, output, downloader=lambda **_kwargs: None)


def test_download_rejects_symlinked_output(tmp_path: Path) -> None:
    lock = _lock(tmp_path)
    real = tmp_path / "real-output"
    real.mkdir()
    output = tmp_path / "output"
    output.symlink_to(real, target_is_directory=True)

    with pytest.raises(ModelDownloadError, match="unsafe"):
        download_locked_models(lock, output, downloader=lambda **_kwargs: None)
