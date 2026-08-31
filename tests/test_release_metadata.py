from __future__ import annotations

import json
import os
import re
from pathlib import Path

from papertrans import __release__, __version__
from papertrans.render import ARXIV_HTML_RENDERER_VERSION


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _python_project_version() -> str:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = pyproject.split("[project]", 1)[1].split("\n[", 1)[0]
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', project, re.MULTILINE)
    assert match is not None, "pyproject.toml is missing [project].version"
    return match.group(1)


def _pep440_release(release: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-rc\.(\d+))?", release)
    assert match is not None, (
        "releases must use MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-rc.N"
    )
    if match.group(2) is None:
        return match.group(1)
    return f"{match.group(1)}rc{match.group(2)}"


def test_release_version_is_synchronized_across_public_metadata() -> None:
    package = json.loads((REPOSITORY_ROOT / "package.json").read_text(encoding="utf-8"))
    model_lock = json.loads(
        (REPOSITORY_ROOT / "docling-models.lock.json").read_text(encoding="utf-8")
    )
    changelog = (REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert package["version"] == __release__
    assert _python_project_version() == __version__ == _pep440_release(__release__)
    assert model_lock["release"] == f"v{__release__}"
    assert re.search(
        rf"^## \[{re.escape(__release__)}\](?:\s+-\s+\d{{4}}-\d{{2}}-\d{{2}})?\s*$",
        changelog,
        re.MULTILINE,
    )


def test_release_tag_matches_public_metadata_when_provided() -> None:
    release_tag = os.environ.get("PAPERTRANS_RELEASE_TAG")
    if release_tag is None:
        return
    assert release_tag == f"v{__release__}"


def test_arxiv_renderer_security_generation_matches_web_gate() -> None:
    web_security = (
        REPOSITORY_ROOT / "lib/artifact-security.ts"
    ).read_text(encoding="utf-8")
    match = re.search(
        r'^export const SAFE_ARXIV_RENDERER_GENERATION = "([0-9]+)";$',
        web_security,
        re.MULTILINE,
    )
    assert match is not None
    assert match.group(1) == ARXIV_HTML_RENDERER_VERSION
