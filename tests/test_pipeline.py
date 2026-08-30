from pathlib import Path
from zipfile import ZipFile

from papertrans.models import BBox, DocumentIR, DocumentItem, PageIR
from papertrans.render import create_bundle, render_document
from papertrans.translate import _check_invariants, _command, create_chunks


def sample_document() -> DocumentIR:
    return DocumentIR(
        version=1,
        source_file="paper.pdf",
        source_sha256="abc",
        title="A Test Paper",
        authors="A. Researcher",
        page_count=1,
        pages=[
            PageIR(
                number=1,
                width=612,
                height=792,
                source_image="assets/page-001-original.jpg",
                items=[
                    DocumentItem(
                        id="p1-text-1",
                        kind="paragraph",
                        page=1,
                        order=1,
                        original="Transformer results are reported in [12].",
                        japanese="Transformer の結果を [12] に示す。",
                        bbox=BBox(10, 10, 100, 40),
                    )
                ],
            )
        ],
    )


def test_chunks_do_not_merge_block_identity():
    document = sample_document()
    document.pages[0].items[0].japanese = ""
    chunks = create_chunks(document.iter_items(), max_characters=10)
    assert [item.id for chunk in chunks for item in chunk.items] == ["p1-text-1"]


def test_invariant_check_detects_missing_citation_and_term():
    warnings = _check_invariants("Transformer [12]", "変換器")
    assert any("citation" in warning for warning in warnings)
    assert any("protected terms" in warning for warning in warnings)


def test_render_and_zip(tmp_path: Path):
    document = sample_document()
    work = tmp_path / "work"
    work.mkdir()
    output = tmp_path / "html"
    index = render_document(document, work, output)
    bundle = create_bundle(output, tmp_path / "paper.zip")
    text = index.read_text(encoding="utf-8")
    markdown = (output / "index.md").read_text(encoding="utf-8")
    assert "Transformer の結果" in text
    assert "原文を表示" in text
    assert "図表・数式の確認用" in text
    assert "page-001-original.jpg" in text
    assert "# A Test Paper" in markdown
    assert "A. Researcher" in markdown
    assert "Transformer の結果を \\[12\\] に示す。" in markdown
    assert "results are reported" not in markdown
    assert bundle.exists()
    with ZipFile(bundle) as archive:
        assert archive.read("index.html") == index.read_bytes()
        assert archive.read("index.md") == (output / "index.md").read_bytes()
        assert set(archive.namelist()) == {"document.json", "index.html", "index.md"}


def test_codex_command_uses_read_only_sandbox(tmp_path: Path):
    command = _command(tmp_path, tmp_path / "schema.json")
    assert command[0] == "codex"
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ask-for-approval" not in command
