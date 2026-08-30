from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .pdf_translation_worker import (
    MAX_DEADLINE_SECONDS,
    MAX_OUTPUT_BYTES,
    PdfTranslationContractError,
    inspect_pdf,
    publish_candidate_run,
    validate_backend_health,
    validate_worker_request,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_REFERENCE = re.compile(
    r"^(?:(?:[A-Za-z0-9._-]+(?::[0-9]+)?/)*[A-Za-z0-9._-]+@)?"
    r"(?P<digest>sha256:[0-9a-f]{64})$"
)
_SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class PdfTranslationSupervisorError(RuntimeError):
    """A launch, timeout, or trusted worker configuration failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ContainerWorkerProfile:
    backend_id: str
    image: str
    source_revision: str
    sbom_sha256: str
    lock_sha256: str
    font_path: Path | None = None
    font_sha256: str | None = None
    memory: str = "4g"
    cpus: str = "4"
    pids_limit: int = 256
    network: str = "none"

    @property
    def image_digest(self) -> str:
        match = _IMAGE_REFERENCE.fullmatch(self.image)
        if match is None:
            raise PdfTranslationSupervisorError(
                "mutable_image",
                "worker image must be addressed by an immutable sha256 digest",
            )
        return match.group("digest")

    def validate(self) -> None:
        self.image_digest
        for label, value in (
            ("source revision", self.source_revision),
            ("SBOM digest", self.sbom_sha256),
            ("lock digest", self.lock_sha256),
        ):
            if not _SHA256.fullmatch(value):
                raise PdfTranslationSupervisorError(
                    "invalid_profile", f"{label} must be a lowercase SHA-256 digest"
                )
        if self.font_path is not None:
            if not self.font_path.is_absolute() or not self.font_path.is_file():
                raise PdfTranslationSupervisorError(
                    "invalid_profile", "trusted font must be an existing absolute file"
                )
            if not self.font_sha256 or _sha256_file(self.font_path) != self.font_sha256:
                raise PdfTranslationSupervisorError(
                    "invalid_profile", "trusted font digest does not match the file"
                )
        if self.network != "none":
            raise PdfTranslationSupervisorError(
                "unsafe_network",
                "the local deterministic evaluation profile requires network=none",
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_candidate_request(
    source_pdf: Path,
    *,
    run_id: str,
    source_language: str = "en",
    target_language: str = "ja",
    profile_id: str = "harumi-layout-eval-ja-v1",
    provider_id: str = "deterministic-local",
    model_id: str = "deterministic-layout-v1",
    prompt_revision: str = "papertrans-pdf-layout-v1",
    max_pages: int = 300,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    deadline_seconds: int = MAX_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Build the strict host-owned request used by an isolated candidate worker."""

    source_pdf = Path(source_pdf).resolve()
    inspection = inspect_pdf(source_pdf, max_pages=max_pages, reject_active_content=True)
    if inspection.page_count > max_pages:
        raise PdfTranslationSupervisorError("page_limit", "source PDF exceeds max_pages")
    request = {
        "schemaVersion": 1,
        "runId": run_id,
        "source": {
            "mediaType": "application/pdf",
            "sha256": _sha256_file(source_pdf),
            "bytes": source_pdf.stat().st_size,
        },
        "translation": {
            "sourceLanguage": source_language,
            "targetLanguage": target_language,
            "profileId": profile_id,
            "providerId": provider_id,
            "modelId": model_id,
            "promptRevision": prompt_revision,
            "glossarySha256": None,
        },
        "outputs": ["translated_mono_pdf"],
        "limits": {
            "maxPages": max_pages,
            "maxOutputBytes": max_output_bytes,
            "deadlineSeconds": deadline_seconds,
        },
    }
    return validate_worker_request(request, source_path=source_pdf)


def _mount(path: Path, destination: str, *, readonly: bool) -> str:
    source = str(path.resolve())
    if "," in source or "\n" in source:
        raise PdfTranslationSupervisorError(
            "unsafe_mount_path", "worker mount paths may not contain commas or newlines"
        )
    suffix = ",readonly" if readonly else ""
    return f"type=bind,src={source},dst={destination}{suffix}"


def container_command(
    docker: str,
    profile: ContainerWorkerProfile,
    *,
    command: str,
    container_name: str,
    request_path: Path | None = None,
    source_path: Path | None = None,
    output_path: Path | None = None,
) -> list[str]:
    """Return a shell-free, capability-dropped one-job container argv."""

    profile.validate()
    if command not in {"health", "run"}:
        raise PdfTranslationSupervisorError("invalid_command", "unsupported worker command")
    argv = [
        docker,
        "run",
        "--rm",
        "--pull=never",
        "--name",
        container_name,
        "--network",
        profile.network,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={profile.pids_limit}",
        f"--memory={profile.memory}",
        f"--cpus={profile.cpus}",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=512m",
        "--env",
        f"PAPERTRANS_WORKER_IMAGE_DIGEST={profile.image_digest}",
        "--env",
        f"PAPERTRANS_WORKER_BUILD_DIGEST={profile.image_digest}",
        "--env",
        f"PAPERTRANS_WORKER_SOURCE_REVISION={profile.source_revision}",
        "--env",
        f"PAPERTRANS_WORKER_SBOM_SHA256={profile.sbom_sha256}",
        "--env",
        f"PAPERTRANS_WORKER_LOCK_SHA256={profile.lock_sha256}",
    ]
    if profile.font_sha256:
        argv.extend(
            ["--env", f"PAPERTRANS_HARUMI_FONT_SHA256={profile.font_sha256}"]
        )
    if command == "run":
        if request_path is None or source_path is None or output_path is None:
            raise PdfTranslationSupervisorError(
                "invalid_command", "run requires request, source, and output mounts"
            )
        output_path.mkdir(parents=True, exist_ok=False)
        os.chmod(output_path, 0o777)
        argv.extend(["--mount", _mount(request_path, "/input/request.json", readonly=True)])
        argv.extend(["--mount", _mount(source_path, "/input/source.pdf", readonly=True)])
        argv.extend(["--mount", _mount(output_path, "/output", readonly=False)])
        if profile.font_path is not None:
            argv.extend(
                [
                    "--mount",
                    _mount(
                        profile.font_path,
                        "/assets/NotoSansCJKjp-Regular.otf",
                        readonly=True,
                    ),
                    "--env",
                    "PAPERTRANS_HARUMI_FONT=/assets/NotoSansCJKjp-Regular.otf",
                ]
            )
    argv.extend([profile.image, command])
    if command == "run":
        argv.extend(
            [
                "--request",
                "/input/request.json",
                "--source",
                "/input/source.pdf",
                "--output",
                "/output",
            ]
        )
    return argv


def _run_process(
    argv: Sequence[str],
    *,
    timeout: int,
    docker: str,
    container_name: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        subprocess.run(
            [docker, "rm", "-f", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        raise PdfTranslationSupervisorError(
            "worker_timeout", f"isolated worker exceeded {timeout} seconds"
        ) from error


def _one_json_document(value: bytes, *, label: str) -> Mapping[str, Any]:
    if len(value) > 4 * 1024 * 1024:
        raise PdfTranslationSupervisorError("invalid_worker_output", f"{label} is too large")
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PdfTranslationSupervisorError(
            "invalid_worker_output", f"{label} is not one UTF-8 JSON object"
        ) from error
    if not isinstance(parsed, dict):
        raise PdfTranslationSupervisorError(
            "invalid_worker_output", f"{label} must be a JSON object"
        )
    return parsed


def run_container_candidate(
    *,
    output_root: Path,
    slug: str,
    source_pdf: Path,
    request: Mapping[str, Any],
    profile: ContainerWorkerProfile,
    approved_fork_revisions: Sequence[str] = (),
    docker: str | None = None,
) -> Path:
    """Run and publish one network-disabled candidate in a fresh container."""

    if not _SAFE_SLUG.fullmatch(slug):
        raise PdfTranslationSupervisorError("invalid_slug", "slug is not safe")
    profile.validate()
    source_pdf = Path(source_pdf).resolve()
    validated_request = validate_worker_request(request, source_path=source_pdf)
    docker_path = docker or shutil.which("docker")
    if not docker_path:
        raise PdfTranslationSupervisorError("docker_unavailable", "docker is not installed")

    run_id = validated_request["runId"]
    nonce = uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix=f"papertrans-{run_id}-") as temporary:
        temporary_root = Path(temporary)
        request_path = temporary_root / "request.json"
        request_path.write_text(
            json.dumps(validated_request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(request_path, 0o444)
        source_copy = temporary_root / "source.pdf"
        shutil.copyfile(source_pdf, source_copy)
        os.chmod(source_copy, 0o444)

        health_name = f"papertrans-health-{nonce}"
        health_process = _run_process(
            container_command(
                docker_path,
                profile,
                command="health",
                container_name=health_name,
            ),
            timeout=60,
            docker=docker_path,
            container_name=health_name,
        )
        if health_process.returncode != 0:
            raise PdfTranslationSupervisorError(
                "health_failed", "worker health command failed"
            )
        health = validate_backend_health(
            _one_json_document(health_process.stdout, label="health output"),
            approved_fork_revisions=approved_fork_revisions,
            required_outputs=validated_request["outputs"],
        )
        if health["backendId"] != profile.backend_id:
            raise PdfTranslationSupervisorError(
                "health_failed", "worker backend ID differs from the configured profile"
            )

        worker_output = temporary_root / "worker-output"
        run_name = f"papertrans-{run_id}-{nonce}"
        run_process = _run_process(
            container_command(
                docker_path,
                profile,
                command="run",
                container_name=run_name,
                request_path=request_path,
                source_path=source_copy,
                output_path=worker_output,
            ),
            timeout=validated_request["limits"]["deadlineSeconds"] + 15,
            docker=docker_path,
            container_name=run_name,
        )
        events = run_process.stdout
        if run_process.returncode != 0:
            raise PdfTranslationSupervisorError(
                "worker_failed", "isolated worker returned a non-zero status"
            )
        return publish_candidate_run(
            output_root=Path(output_root),
            slug=slug,
            source_pdf=source_pdf,
            staging_dir=worker_output,
            request=validated_request,
            health=health,
            events_ndjson=events,
            approved_fork_revisions=approved_fork_revisions,
            worker_log=run_process.stderr.decode("utf-8", errors="replace")[-64_000:],
        )


def profile_from_files(
    *,
    backend_id: str,
    image: str,
    source_file: Path,
    sbom_file: Path,
    lock_file: Path,
    font_file: Path | None = None,
) -> ContainerWorkerProfile:
    """Create a trusted profile from reviewed local provenance files."""

    return ContainerWorkerProfile(
        backend_id=backend_id,
        image=image,
        source_revision=_sha256_file(Path(source_file)),
        sbom_sha256=_sha256_file(Path(sbom_file)),
        lock_sha256=_sha256_file(Path(lock_file)),
        font_path=Path(font_file).resolve() if font_file else None,
        font_sha256=_sha256_file(Path(font_file)) if font_file else None,
    )


__all__ = [
    "ContainerWorkerProfile",
    "PdfTranslationContractError",
    "PdfTranslationSupervisorError",
    "container_command",
    "make_candidate_request",
    "profile_from_files",
    "run_container_candidate",
]
