from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from papertrans import mcp_server


def test_configure_store_accepts_an_explicit_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "_store_instance", None)
    store = mcp_server.configure_store(
        tmp_path / "repo",
        tmp_path / "output",
        tmp_path / "custom-data",
    )

    assert store.data_root == (tmp_path / "custom-data").resolve()


def test_lazy_store_uses_data_root_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    custom_data_root = tmp_path / "custom-data"
    monkeypatch.setattr(mcp_server, "_store_instance", None)
    monkeypatch.setenv("PAPERTRANS_REPO_ROOT", str(repo_root))
    monkeypatch.setenv("PAPERTRANS_OUTPUT_ROOT", str(tmp_path / "output"))
    monkeypatch.setenv("PAPERTRANS_DATA_ROOT", str(custom_data_root))

    store = mcp_server._store()

    assert store.repo_root == repo_root.resolve()
    assert store.data_root == custom_data_root.resolve()


def test_mcp_parser_accepts_explicit_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "custom-data"

    args = mcp_server._parser().parse_args(["--data-root", str(data_root)])

    assert args.data_root == data_root


@pytest.mark.parametrize("use_cli_argument", [False, True])
def test_mcp_main_resolves_and_passes_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_cli_argument: bool,
) -> None:
    repo_root = tmp_path / "repo"
    environment_data_root = tmp_path / "environment-data"
    cli_data_root = tmp_path / "cli-data"
    args = SimpleNamespace(
        transport="stdio",
        host="127.0.0.1",
        port=8000,
        repo_root=repo_root,
        output_root=tmp_path / "output",
        data_root=cli_data_root if use_cli_argument else None,
    )
    configured: dict[str, Path] = {}

    def capture_store(repo: Path, output: Path, data: Path) -> None:
        configured.update(repo=repo, output=output, data=data)

    monkeypatch.setenv("PAPERTRANS_DATA_ROOT", str(environment_data_root))
    monkeypatch.setattr(
        mcp_server,
        "_parser",
        lambda: SimpleNamespace(parse_args=lambda: args),
    )
    monkeypatch.setattr(mcp_server, "configure_store", capture_store)
    monkeypatch.setattr(mcp_server, "server", SimpleNamespace(run=lambda **_kwargs: None))

    mcp_server.main()

    assert configured["repo"] == repo_root
    assert configured["data"] == (
        cli_data_root if use_cli_argument else environment_data_root
    )
