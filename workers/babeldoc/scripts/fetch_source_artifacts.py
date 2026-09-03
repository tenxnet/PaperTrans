#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import tarfile
import tempfile
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(\\)?$")
REQUIREMENT_HASH_LINE_RE = re.compile(r"^\s+--hash=sha256:([0-9a-f]{64})\s*(\\)?$")
ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {
        "files.pythonhosted.org",
        "mupdf.com",
        "release-assets.githubusercontent.com",
    }
)
REQUIRED_KEYS = frozenset(
    {
        "id",
        "name",
        "version",
        "kind",
        "filename",
        "url",
        "sha256",
        "bytes",
        "top_level",
        "license_declaration",
        "license_path",
        "license_sha256",
        "runtime_artifact_sha256",
    }
)
OPTIONAL_KEYS = frozenset({"builds_for"})


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def load_lock(path: Path) -> list[dict[str, Any]]:
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(value) != {"schema_version", "artifacts"} or value["schema_version"] != 1:
        raise SystemExit("invalid source-artifact lock schema")
    artifacts = value["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise SystemExit("source-artifact lock must contain artifacts")
    seen_ids: set[str] = set()
    seen_filenames: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not REQUIRED_KEYS <= set(artifact):
            raise SystemExit("invalid source-artifact entry")
        if set(artifact) - REQUIRED_KEYS - OPTIONAL_KEYS:
            raise SystemExit(
                f"unknown source-artifact fields: {artifact.get('id', '?')}"
            )
        identifier = artifact["id"]
        filename = artifact["filename"]
        if not isinstance(identifier, str) or SAFE_ID_RE.fullmatch(identifier) is None:
            raise SystemExit("invalid source-artifact id")
        if identifier in seen_ids:
            raise SystemExit(f"duplicate source-artifact id: {identifier}")
        seen_ids.add(identifier)
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.endswith(".tar.gz")
            or filename in seen_filenames
        ):
            raise SystemExit(f"invalid source-artifact filename: {identifier}")
        seen_filenames.add(filename)
        parsed = urllib.parse.urlparse(artifact["url"])
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or PurePosixPath(parsed.path).name != filename
        ):
            raise SystemExit(f"unapproved source-artifact URL: {identifier}")
        if not isinstance(artifact["bytes"], int) or artifact["bytes"] <= 0:
            raise SystemExit(f"invalid source-artifact size: {identifier}")
        for field in ("sha256", "license_sha256"):
            if (
                not isinstance(artifact[field], str)
                or SHA256_RE.fullmatch(artifact[field]) is None
            ):
                raise SystemExit(f"invalid {field}: {identifier}")
        for field in ("name", "version", "kind", "license_declaration"):
            if not isinstance(artifact[field], str) or not artifact[field].strip():
                raise SystemExit(f"invalid {field}: {identifier}")
        if not _safe_relative(artifact["top_level"]) or "/" in artifact["top_level"]:
            raise SystemExit(f"invalid top-level directory: {identifier}")
        if not _safe_relative(artifact["license_path"]):
            raise SystemExit(f"invalid license path: {identifier}")
        runtime_hashes = artifact["runtime_artifact_sha256"]
        if not isinstance(runtime_hashes, list) or any(
            not isinstance(item, str) or SHA256_RE.fullmatch(item) is None
            for item in runtime_hashes
        ):
            raise SystemExit(f"invalid runtime-artifact hashes: {identifier}")
        if (
            len(set(runtime_hashes)) != len(runtime_hashes)
            or artifact["sha256"] in runtime_hashes
        ):
            raise SystemExit(f"duplicate runtime/source artifact hashes: {identifier}")
        if artifact["kind"] == "python-sdist":
            if not runtime_hashes or "builds_for" in artifact:
                raise SystemExit(f"invalid Python source mapping: {identifier}")
        elif artifact["kind"] == "native-source":
            if runtime_hashes or not isinstance(artifact.get("builds_for"), str):
                raise SystemExit(f"invalid native source mapping: {identifier}")
        else:
            raise SystemExit(f"invalid source-artifact kind: {identifier}")
    python_sources = {
        (_normalize_distribution_name(item["name"]), item["version"])
        for item in artifacts
        if item["kind"] == "python-sdist"
    }
    for artifact in artifacts:
        if artifact["kind"] != "native-source":
            continue
        match = REQUIREMENT_RE.fullmatch(artifact["builds_for"])
        if (
            match is None
            or match[3] is not None
            or (_normalize_distribution_name(match[1]), match[2]) not in python_sources
        ):
            raise SystemExit(f"invalid builds_for relationship: {artifact['id']}")
    return artifacts


def parse_requirement_stanzas(text: str) -> dict[str, tuple[str, set[str]]]:
    stanzas: dict[str, tuple[str, set[str]]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            match = REQUIREMENT_RE.fullmatch(line)
            if match is None:
                current = None
                continue
            name = _normalize_distribution_name(match[1])
            if name in stanzas:
                raise SystemExit(f"duplicate requirement stanza: {name}")
            stanzas[name] = (match[2], set())
            current = name if match[3] is not None else None
        elif current is not None and line[:1].isspace():
            match = REQUIREMENT_HASH_LINE_RE.fullmatch(line)
            if match is None:
                current = None
                continue
            if match[1] in stanzas[current][1]:
                raise SystemExit(f"duplicate requirement hash: {current}")
            stanzas[current][1].add(match[1])
            if match[2] is None:
                current = None
        else:
            current = None
    return stanzas


def validate_requirements_lock(artifacts: list[dict[str, Any]], path: Path) -> None:
    stanzas = parse_requirement_stanzas(path.read_text(encoding="utf-8"))
    for artifact in artifacts:
        if artifact["kind"] != "python-sdist":
            continue
        package = _normalize_distribution_name(artifact["name"])
        observed = stanzas.get(package)
        if observed is None or observed[0] != artifact["version"]:
            raise SystemExit(
                f"source artifact does not match requirement version: {artifact['id']}"
            )
        expected = {artifact["sha256"], *artifact["runtime_artifact_sha256"]}
        absent = sorted(expected - observed[1])
        if absent:
            raise SystemExit(
                f"source/runtime artifact is not pinned in requirements.lock: {artifact['id']}"
            )


def validate_upstream_lock(source_lock_path: Path, upstream_lock_path: Path) -> None:
    upstream = tomllib.loads(upstream_lock_path.read_text(encoding="utf-8"))
    expected = upstream.get("container", {}).get("source_artifacts_lock_sha256")
    if expected != hash_file(source_lock_path):
        raise SystemExit("source-artifact lock does not match UPSTREAM.lock")


def _normalized_link_target(member: tarfile.TarInfo) -> str:
    if member.linkname.startswith("/"):
        return ""
    base = "" if member.islnk() else posixpath.dirname(member.name)
    return posixpath.normpath(posixpath.join(base, member.linkname))


def validate_archive(path: Path, artifact: dict[str, Any]) -> None:
    if (
        path.stat().st_size != artifact["bytes"]
        or hash_file(path) != artifact["sha256"]
    ):
        raise SystemExit(f"source-artifact digest or size mismatch: {artifact['id']}")
    prefix = artifact["top_level"]
    license_member = f"{prefix}/{artifact['license_path']}"
    license_bytes: bytes | None = None
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise SystemExit(f"empty source archive: {artifact['id']}")
            for member in members:
                member_path = PurePosixPath(member.name)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or not member_path.parts
                    or member_path.parts[0] != prefix
                    or member.isdev()
                    or not (
                        member.isfile()
                        or member.isdir()
                        or member.issym()
                        or member.islnk()
                    )
                ):
                    raise SystemExit(f"unsafe source archive member: {artifact['id']}")
                if member.issym() or member.islnk():
                    target = _normalized_link_target(member)
                    if not target or PurePosixPath(target).parts[0] != prefix:
                        raise SystemExit(
                            f"unsafe source archive link: {artifact['id']}"
                        )
                if member.name == license_member:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise SystemExit(f"unreadable source license: {artifact['id']}")
                    license_bytes = extracted.read()
    except (OSError, tarfile.TarError) as error:
        raise SystemExit(
            f"invalid source archive: {artifact['id']}: {error}"
        ) from error
    if (
        license_bytes is None
        or hashlib.sha256(license_bytes).hexdigest() != artifact["license_sha256"]
    ):
        raise SystemExit(f"source license mismatch: {artifact['id']}")


def download_artifact(artifact: dict[str, Any], output: Path) -> Path:
    destination = output / artifact["filename"]
    if destination.is_symlink():
        raise SystemExit(f"unsafe existing source artifact: {artifact['id']}")
    if destination.exists():
        if not stat.S_ISREG(destination.lstat().st_mode):
            raise SystemExit(f"unsafe existing source artifact: {artifact['id']}")
        validate_archive(destination, artifact)
        return destination
    request = urllib.request.Request(
        artifact["url"],
        headers={"User-Agent": "PaperTrans-corresponding-source/1"},
    )
    temporary: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in ALLOWED_DOWNLOAD_HOSTS:
                raise SystemExit(
                    f"unapproved source-artifact redirect: {artifact['id']}"
                )
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) != artifact["bytes"]:
                raise SystemExit(
                    f"source-artifact Content-Length mismatch: {artifact['id']}"
                )
            with tempfile.NamedTemporaryFile(
                dir=output, prefix=".source-", delete=False
            ) as target:
                temporary = Path(target.name)
                total = 0
                digest = hashlib.sha256()
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > artifact["bytes"]:
                        raise SystemExit(
                            f"source-artifact exceeds locked size: {artifact['id']}"
                        )
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
        if total != artifact["bytes"] or digest.hexdigest() != artifact["sha256"]:
            raise SystemExit(f"downloaded source-artifact mismatch: {artifact['id']}")
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    validate_archive(destination, artifact)
    return destination


def prepare_output_directory(output: Path, artifacts: list[dict[str, Any]]) -> None:
    if output.is_symlink():
        raise SystemExit("source-artifact output directory is unsafe")
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or not output.is_dir():
        raise SystemExit("source-artifact output directory is unsafe")
    allowed = {item["filename"] for item in artifacts}
    allowed.add("source-artifacts.manifest.json")
    for candidate in output.iterdir():
        if (
            candidate.name not in allowed
            or candidate.is_symlink()
            or not stat.S_ISREG(candidate.lstat().st_mode)
        ):
            raise SystemExit(
                f"unexpected source-artifact output entry: {candidate.name}"
            )


def write_manifest(
    lock_path: Path, output: Path, artifacts: list[dict[str, Any]]
) -> None:
    files = []
    for artifact in artifacts:
        archive = output / artifact["filename"]
        files.append(
            {
                "id": artifact["id"],
                "name": artifact["name"],
                "version": artifact["version"],
                "path": artifact["filename"],
                "sha256": hash_file(archive),
                "bytes": archive.stat().st_size,
                "licenseDeclaration": artifact["license_declaration"],
                "licensePath": f"{artifact['top_level']}/{artifact['license_path']}",
                "licenseSha256": artifact["license_sha256"],
            }
        )
    value = {
        "schemaVersion": 1,
        "lockSha256": hash_file(lock_path),
        "files": files,
    }
    manifest = output / "source-artifacts.manifest.json"
    temporary = output / ".source-artifacts.manifest.json.tmp"
    with temporary.open("x", encoding="utf-8") as target:
        json.dump(
            value, target, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, manifest)


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--requirements-lock", required=True, type=Path)
    parser.add_argument("--upstream-lock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    artifacts = load_lock(args.lock)
    validate_requirements_lock(artifacts, args.requirements_lock)
    validate_upstream_lock(args.lock, args.upstream_lock)
    prepare_output_directory(args.output, artifacts)
    for artifact in artifacts:
        download_artifact(artifact, args.output)
    write_manifest(args.lock, args.output, artifacts)


if __name__ == "__main__":
    main()
