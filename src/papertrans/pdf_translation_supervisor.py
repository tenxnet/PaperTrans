from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .pdf_translation_worker import (
    MAX_DEADLINE_SECONDS,
    MAX_OUTPUT_BYTES,
    PdfTranslationContractError,
    inspect_pdf,
    publish_candidate_run,
    validate_backend_health,
    validate_ndjson_events,
    validate_worker_request,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REFERENCE = re.compile(
    r"^(?:(?:[A-Za-z0-9._-]+(?::[0-9]+)?/)*[A-Za-z0-9._-]+@)?"
    r"(?P<digest>sha256:[0-9a-f]{64})$"
)
_SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SAFE_DOCKER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_PROTOCOL_STDOUT_BYTES = 4 * 1024 * 1024
_MAX_DIAGNOSTIC_STDERR_BYTES = 256 * 1024
_OUTPUT_METADATA_ALLOWANCE_BYTES = 8 * 1024 * 1024


class PdfTranslationSupervisorError(RuntimeError):
    """A launch, timeout, or trusted worker configuration failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _CapturedProcess:
    returncode: int
    stdout: bytes
    stderr: bytes


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
    provider_secret_path: Path | None = None
    gateway_container: str | None = None
    gateway_image_digest: str | None = None
    gateway_egress_network: str | None = None
    translation_cache_bytes: int = 256 * 1024 * 1024
    purpose: str = "layout_evaluation"
    promotion_eligible: bool = False
    worker_executable: str = "/usr/local/bin/papertrans-harumi-worker"

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
        if not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", self.purpose):
            raise PdfTranslationSupervisorError(
                "invalid_profile", "worker purpose is not a stable identifier"
            )
        if not isinstance(self.promotion_eligible, bool):
            raise PdfTranslationSupervisorError(
                "invalid_profile", "promotion eligibility must be an explicit boolean"
            )
        if self.promotion_eligible and self.purpose != "semantic_translation":
            raise PdfTranslationSupervisorError(
                "invalid_profile", "only semantic translation runs may be promotable"
            )
        if self.promotion_eligible and "harumi" in self.backend_id.lower():
            raise PdfTranslationSupervisorError(
                "invalid_profile", "the Harumi placeholder adapter is never promotable"
            )
        if not re.fullmatch(r"/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+", self.worker_executable):
            raise PdfTranslationSupervisorError(
                "invalid_profile", "worker executable must be a fixed absolute path"
            )
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
        if self.network == "none":
            if (
                self.provider_secret_path is not None
                or self.gateway_container is not None
                or self.gateway_image_digest is not None
                or self.gateway_egress_network is not None
            ):
                raise PdfTranslationSupervisorError(
                    "invalid_profile",
                    "an offline profile may not receive provider credentials",
                )
            return
        if "babeldoc" not in self.backend_id.lower():
            raise PdfTranslationSupervisorError(
                "unsafe_network", "only the reviewed BabelDOC adapter may use a gateway"
            )
        if (
            not _SAFE_DOCKER_NAME.fullmatch(self.network)
            or self.network in {"bridge", "default", "host", "none"}
            or not isinstance(self.gateway_container, str)
            or not _SAFE_DOCKER_NAME.fullmatch(self.gateway_container)
            or not isinstance(self.gateway_image_digest, str)
            or not _DIGEST.fullmatch(self.gateway_image_digest)
            or not isinstance(self.gateway_egress_network, str)
            or not _SAFE_DOCKER_NAME.fullmatch(self.gateway_egress_network)
            or self.gateway_egress_network
            in {self.network, "bridge", "default", "host", "none"}
        ):
            raise PdfTranslationSupervisorError(
                "unsafe_network", "gateway network and container names are not trusted"
            )
        if (
            not isinstance(self.translation_cache_bytes, int)
            or isinstance(self.translation_cache_bytes, bool)
            or not 16 * 1024 * 1024
            <= self.translation_cache_bytes
            <= 1024 * 1024 * 1024
        ):
            raise PdfTranslationSupervisorError(
                "invalid_profile", "translation cache tmpfs size is outside policy"
            )
        _provider_profile(self.provider_secret_path, self.gateway_container)


def _provider_profile(path: Path | None, gateway_container: str) -> Mapping[str, Any]:
    """Validate the trusted provider secret without exposing its contents."""

    if path is None:
        raise PdfTranslationSupervisorError(
            "invalid_profile", "a BabelDOC gateway profile requires a provider secret"
        )
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
        if (
            not candidate.is_absolute()
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > 16 * 1024
            or metadata.st_mode & 0o077
        ):
            raise ValueError
        raw = candidate.read_bytes()

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        profile = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise PdfTranslationSupervisorError(
            "invalid_profile", "provider secret is absent, unsafe, or malformed"
        ) from error
    expected = {
        "schemaVersion",
        "profileId",
        "providerId",
        "modelId",
        "baseUrl",
        "apiKey",
        "qps",
    }
    if not isinstance(profile, dict) or set(profile) != expected:
        raise PdfTranslationSupervisorError(
            "invalid_profile", "provider secret schema is not exact"
        )
    parsed = urlsplit(profile.get("baseUrl")) if isinstance(profile.get("baseUrl"), str) else None
    if (
        profile.get("schemaVersion") != 1
        or parsed is None
        or parsed.scheme not in {"http", "https"}
        or parsed.hostname != gateway_container
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not isinstance(profile.get("apiKey"), str)
        or not 16 <= len(profile["apiKey"]) <= 4096
        or any(character in profile["apiKey"] for character in "\r\n\x00")
    ):
        raise PdfTranslationSupervisorError(
            "unsafe_network", "provider secret must target only the reviewed gateway"
        )
    return profile


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
    output_role: str = "translated_mono_pdf",
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
        "outputs": [output_role],
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


def _volume_mount(name: str, destination: str, *, readonly: bool) -> str:
    if not _SAFE_DOCKER_NAME.fullmatch(name):
        raise PdfTranslationSupervisorError(
            "invalid_command", "ephemeral secret volume name is unsafe"
        )
    suffix = ",readonly" if readonly else ""
    return f"type=volume,src={name},dst={destination},volume-nocopy{suffix}"


def container_command(
    docker: str,
    profile: ContainerWorkerProfile,
    *,
    command: str,
    container_name: str,
    request_path: Path | None = None,
    source_path: Path | None = None,
    output_path: Path | None = None,
    output_limit_bytes: int | None = None,
    secret_volume: str | None = None,
    output_volume: str | None = None,
) -> list[str]:
    """Return a shell-free, capability-dropped one-job container argv."""

    profile.validate()
    if command not in {"health", "run"}:
        raise PdfTranslationSupervisorError("invalid_command", "unsupported worker command")
    effective_network = "none" if command == "health" else profile.network
    argv = [
        docker,
        "run",
        "--pull=never",
        "--name",
        container_name,
        "--network",
        effective_network,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        "65532:65532",
        f"--pids-limit={profile.pids_limit}",
        f"--memory={profile.memory}",
        f"--cpus={profile.cpus}",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=512m,uid=65532,gid=65532,mode=0700",
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
    if command == "health":
        argv.insert(2, "--rm")
    if "babeldoc" in profile.backend_id.lower():
        babeldoc_cache_bytes = profile.translation_cache_bytes // 2
        pdf2zh_cache_bytes = profile.translation_cache_bytes - babeldoc_cache_bytes
        argv.extend(
            [
                "--tmpfs",
                "/opt/papertrans/home/.config:rw,noexec,nosuid,nodev,size=16m,"
                "uid=65532,gid=65532,mode=0700",
                "--tmpfs",
                "/opt/papertrans/home/.cache/babeldoc:"
                f"rw,noexec,nosuid,nodev,size={babeldoc_cache_bytes},"
                "uid=65532,gid=65532,mode=0700",
                "--tmpfs",
                "/opt/papertrans/home/.cache/pdf2zh_next:"
                f"rw,noexec,nosuid,nodev,size={pdf2zh_cache_bytes},"
                "uid=65532,gid=65532,mode=0700",
            ]
        )
    if profile.font_sha256:
        argv.extend(
            ["--env", f"PAPERTRANS_HARUMI_FONT_SHA256={profile.font_sha256}"]
        )
    if command == "run":
        if (
            request_path is None
            or source_path is None
            or output_path is None
            or output_limit_bytes is None
            or output_volume is None
        ):
            raise PdfTranslationSupervisorError(
                "invalid_command", "run requires request, source, output, and byte limit"
            )
        if not 0 < output_limit_bytes <= MAX_OUTPUT_BYTES:
            raise PdfTranslationSupervisorError(
                "invalid_command", "run output byte limit is outside the hard maximum"
            )
        output_path.mkdir(parents=True, exist_ok=False, mode=0o700)
        argv.extend(
            [
                "--mount",
                _volume_mount(output_volume, "/output", readonly=False),
                "--ulimit",
                f"fsize={output_limit_bytes}:{output_limit_bytes}",
            ]
        )
        argv.extend(["--mount", _mount(request_path, "/input/request.json", readonly=True)])
        argv.extend(["--mount", _mount(source_path, "/input/source.pdf", readonly=True)])
        if profile.font_path is not None:
            argv.extend(
                [
                    "--mount",
                    _mount(
                        profile.font_path,
                        "/assets/NotoSansJP-wght.ttf",
                        readonly=True,
                    ),
                    "--env",
                    "PAPERTRANS_HARUMI_FONT=/assets/NotoSansJP-wght.ttf",
                ]
            )
        if profile.provider_secret_path is not None:
            if secret_volume is None:
                raise PdfTranslationSupervisorError(
                    "invalid_command", "BabelDOC run requires an ephemeral secret volume"
                )
            argv.extend(
                [
                    "--mount",
                    _volume_mount(
                        secret_volume,
                        "/run/secrets",
                        readonly=True,
                    ),
                ]
            )
    if command == "health":
        argv.extend([profile.image, "health"])
    else:
        # Keep the sandbox alive after the worker process exits so quota-backed
        # tmpfs volume contents can be copied while still mounted.
        argv.extend(["--detach", "--entrypoint", "/bin/sleep"])
        argv.extend([profile.image, "infinity"])
    return argv


def worker_exec_command(
    docker: str,
    profile: ContainerWorkerProfile,
    *,
    container_name: str,
) -> list[str]:
    """Return the shell-free command that runs the adapter inside its keeper."""

    profile.validate()
    if not _SAFE_DOCKER_NAME.fullmatch(container_name):
        raise PdfTranslationSupervisorError(
            "invalid_command", "worker container name is unsafe"
        )
    return [
        docker,
        "exec",
        "--user",
        "65532:65532",
        container_name,
        profile.worker_executable,
        "run",
        "--request",
        "/input/request.json",
        "--source",
        "/input/source.pdf",
        "--output",
        "/output",
    ]


def _bounded_process(
    argv: Sequence[str],
    *,
    timeout: int,
    stdout_limit: int,
    stderr_limit: int,
    label: str,
    stdin_data: bytes | None = None,
) -> _CapturedProcess:
    """Run argv without allowing either captured stream to grow without bound."""

    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise PdfTranslationSupervisorError(
            "process_failed", f"{label} did not expose bounded output pipes"
        )

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()

    def drain(stream: Any, name: str, limit: int) -> None:
        total = 0
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total <= limit:
                buffers[name].extend(chunk)
            else:
                overflow.set()

    readers = (
        threading.Thread(
            target=drain,
            args=(process.stdout, "stdout", stdout_limit),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, "stderr", stderr_limit),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    writer: threading.Thread | None = None
    if stdin_data is not None:
        if process.stdin is None:
            process.kill()
            raise PdfTranslationSupervisorError(
                "process_failed", f"{label} did not expose its requested input pipe"
            )

        def feed() -> None:
            try:
                process.stdin.write(stdin_data)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()

    deadline = time.monotonic() + timeout
    timed_out = False
    while process.poll() is None:
        if overflow.is_set():
            process.kill()
            break
        if time.monotonic() >= deadline:
            timed_out = True
            process.kill()
            break
        time.sleep(0.05)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    for reader in readers:
        reader.join(timeout=5)
    if writer is not None:
        writer.join(timeout=5)

    if timed_out:
        raise PdfTranslationSupervisorError(
            "worker_timeout", f"{label} exceeded {timeout} seconds"
        )
    if overflow.is_set():
        raise PdfTranslationSupervisorError(
            "worker_output_limit", f"{label} exceeded its captured output limit"
        )
    return _CapturedProcess(
        returncode=int(process.returncode),
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
    )


def _remove_container(docker: str, container_name: str) -> None:
    try:
        subprocess.run(
            [docker, "rm", "-f", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _remove_volume(docker: str, volume_name: str | None) -> None:
    if volume_name is None:
        return
    try:
        subprocess.run(
            [docker, "volume", "rm", "-f", volume_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _create_bounded_output_volume(
    docker: str,
    *,
    nonce: str,
    output_limit_bytes: int,
) -> str:
    """Create a persistent-until-copied tmpfs volume with an aggregate quota."""

    volume_name = f"papertrans-output-{nonce}"
    volume_bytes = output_limit_bytes + _OUTPUT_METADATA_ALLOWANCE_BYTES
    result = _bounded_process(
        [
            docker,
            "volume",
            "create",
            "--driver",
            "local",
            "--label",
            "org.papertrans.ephemeral-worker-output=1",
            "--opt",
            "type=tmpfs",
            "--opt",
            "device=tmpfs",
            "--opt",
            f"o=size={volume_bytes},uid=65532,gid=65532,mode=0700,"
            "noexec,nosuid,nodev",
            volume_name,
        ],
        timeout=20,
        stdout_limit=16 * 1024,
        stderr_limit=64 * 1024,
        label="bounded worker output volume creation",
    )
    if result.returncode != 0:
        _remove_volume(docker, volume_name)
        raise PdfTranslationSupervisorError(
            "output_staging_failed", "could not create the bounded output volume"
        )
    return volume_name


def _seed_secret_volume(
    docker: str,
    profile: ContainerWorkerProfile,
    *,
    nonce: str,
) -> str:
    """Copy one provider profile through stdin into an ephemeral Docker volume."""

    if profile.provider_secret_path is None:
        raise PdfTranslationSupervisorError(
            "invalid_profile", "provider secret path is missing"
        )
    secret = profile.provider_secret_path.read_bytes()
    volume_name = f"papertrans-secret-{nonce}"
    created = _bounded_process(
        [
            docker,
            "volume",
            "create",
            "--label",
            "org.papertrans.ephemeral-provider-secret=1",
            volume_name,
        ],
        timeout=20,
        stdout_limit=16 * 1024,
        stderr_limit=64 * 1024,
        label="provider secret volume creation",
    )
    if created.returncode != 0:
        raise PdfTranslationSupervisorError(
            "secret_staging_failed", "could not create an ephemeral provider secret volume"
        )
    script = (
        "import os,sys;"
        "d=sys.stdin.buffer.read(16385);"
        "assert 0<len(d)<=16384;"
        "p='/run/secrets/papertrans-provider.json';"
        "f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400);"
        "os.write(f,d);os.fsync(f);os.fchmod(f,0o400);os.fchown(f,65532,65532);os.close(f)"
    )
    try:
        seeded = _bounded_process(
            [
                docker,
                "run",
                "--rm",
                "--interactive",
                "--pull=never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop=ALL",
                "--cap-add=CHOWN",
                "--security-opt=no-new-privileges",
                "--user",
                "0:0",
                "--pids-limit=32",
                "--memory=128m",
                "--cpus=0.25",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m",
                "--mount",
                _volume_mount(volume_name, "/run/secrets", readonly=False),
                "--entrypoint",
                "/usr/local/bin/python",
                profile.image,
                "-c",
                script,
            ],
            timeout=30,
            stdout_limit=16 * 1024,
            stderr_limit=64 * 1024,
            label="provider secret staging",
            stdin_data=secret,
        )
        if seeded.returncode != 0:
            raise PdfTranslationSupervisorError(
                "secret_staging_failed", "could not stage the provider secret as UID 65532"
            )
        return volume_name
    except BaseException:
        _remove_volume(docker, volume_name)
        raise


def _run_process(
    argv: Sequence[str],
    *,
    timeout: int,
    docker: str,
    container_name: str,
) -> _CapturedProcess:
    try:
        return _bounded_process(
            argv,
            timeout=timeout,
            stdout_limit=_MAX_PROTOCOL_STDOUT_BYTES,
            stderr_limit=_MAX_DIAGNOSTIC_STDERR_BYTES,
            label="isolated worker",
        )
    except (OSError, PdfTranslationSupervisorError):
        _remove_container(docker, container_name)
        raise


def _copy_container_output(
    docker: str,
    container_name: str,
    destination: Path,
) -> None:
    result = _bounded_process(
        [docker, "cp", f"{container_name}:/output/.", str(destination)],
        timeout=120,
        stdout_limit=64 * 1024,
        stderr_limit=64 * 1024,
        label="worker artifact copy",
    )
    if result.returncode != 0:
        raise PdfTranslationSupervisorError(
            "artifact_copy_failed", "could not copy bounded worker artifacts"
        )


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


def _mountinfo_path(value: str) -> str:
    """Decode the restricted octal escapes used by /proc/*/mountinfo."""

    return (
        value.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def _verify_keeper_output_mount(
    docker: str,
    *,
    container_name: str,
) -> None:
    """Verify the live keeper sees /output as the reviewed restricted tmpfs."""

    result = _bounded_process(
        [
            docker,
            "exec",
            "--user",
            "65532:65532",
            container_name,
            "/bin/cat",
            "/proc/self/mountinfo",
        ],
        timeout=20,
        stdout_limit=1024 * 1024,
        stderr_limit=64 * 1024,
        label="worker output mount verification",
    )
    if result.returncode != 0:
        raise PdfTranslationSupervisorError(
            "unsafe_output_mount", "could not inspect the live worker output mount"
        )
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PdfTranslationSupervisorError(
            "unsafe_output_mount", "worker mount metadata is not UTF-8"
        ) from error

    matches: list[tuple[set[str], str, set[str]]] = []
    for line in text.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or len(fields) < separator + 4:
            continue
        if _mountinfo_path(fields[4]) != "/output":
            continue
        matches.append(
            (
                set(fields[5].split(",")),
                fields[separator + 1],
                set(fields[separator + 3].split(",")),
            )
        )
    if len(matches) != 1:
        raise PdfTranslationSupervisorError(
            "unsafe_output_mount", "worker must have exactly one /output mount"
        )
    mount_options, filesystem, super_options = matches[0]
    effective_options = mount_options | super_options
    if (
        filesystem != "tmpfs"
        or not {"rw", "noexec", "nosuid", "nodev"}.issubset(effective_options)
        or "uid=65532" not in super_options
        or "gid=65532" not in super_options
        or "mode=700" not in super_options
    ):
        raise PdfTranslationSupervisorError(
            "unsafe_output_mount",
            "live /output mount lacks the reviewed tmpfs restrictions",
        )


def _contains_secret(path: Path, patterns: Sequence[bytes]) -> bool:
    longest = max(len(pattern) for pattern in patterns)
    retained = b""
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                candidate = retained + chunk
                if any(pattern in candidate for pattern in patterns):
                    return True
                retained = candidate[-(longest - 1) :] if longest > 1 else b""
    except OSError as error:
        raise PdfTranslationSupervisorError(
            "secret_scan_failed", "could not scan a worker artifact before publication"
        ) from error


def _reject_retained_provider_secrets(
    profile: ContainerWorkerProfile,
    *,
    staging_dir: Path,
    events: bytes,
) -> None:
    """Reject accidental literal credential retention before any host publication."""

    if profile.provider_secret_path is None:
        return
    provider = _provider_profile(
        profile.provider_secret_path, profile.gateway_container or ""
    )
    api_key = str(provider["apiKey"]).encode("utf-8")
    raw_profile = profile.provider_secret_path.read_bytes()
    patterns = tuple(dict.fromkeys((api_key, raw_profile)))
    if any(pattern in events for pattern in patterns):
        raise PdfTranslationSupervisorError(
            "secret_leak", "worker protocol attempted to retain provider credentials"
        )
    try:
        paths = tuple(Path(staging_dir).rglob("*"))
    except OSError as error:
        raise PdfTranslationSupervisorError(
            "secret_scan_failed", "could not enumerate worker artifacts before publication"
        ) from error
    for path in paths:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise PdfTranslationSupervisorError(
                "secret_scan_failed", "could not inspect a worker artifact before publication"
            ) from error
        if stat.S_ISREG(metadata.st_mode) and _contains_secret(path, patterns):
            raise PdfTranslationSupervisorError(
                "secret_leak", "a worker artifact attempted to retain provider credentials"
            )


def _numeric_non_root_user(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(
        r"[1-9][0-9]*(?::[1-9][0-9]*)?", value
    ) is not None


def _gateway_runtime_config_is_reviewed(
    gateway: Mapping[str, Any],
    *,
    image_config: Mapping[str, Any],
    profile: ContainerWorkerProfile,
) -> bool:
    """Reject runtime overrides that can retain or redirect paper credentials."""

    runtime_config = gateway.get("Config")
    host_config = gateway.get("HostConfig")
    network_settings = gateway.get("NetworkSettings")
    if (
        not isinstance(runtime_config, dict)
        or not isinstance(host_config, dict)
        or not isinstance(network_settings, dict)
    ):
        return False
    if not _numeric_non_root_user(image_config.get("User")):
        return False
    reviewed_fields = (
        "User",
        "Entrypoint",
        "Cmd",
        "Env",
        "WorkingDir",
        "ExposedPorts",
        "Volumes",
    )
    if any(
        runtime_config.get(field) != image_config.get(field)
        for field in reviewed_fields
    ):
        return False
    runtime_ports = network_settings.get("Ports")
    exposed_ports = image_config.get("ExposedPorts")
    expected_port_names = set(exposed_ports) if isinstance(exposed_ports, dict) else set()
    ports_are_unpublished = bool(
        isinstance(runtime_ports, dict)
        and (
            not runtime_ports
            or (
                set(runtime_ports) == expected_port_names
                and all(binding is None for binding in runtime_ports.values())
            )
        )
    )
    if (
        gateway.get("Mounts") not in (None, [])
        or bool(host_config.get("Binds"))
        or bool(host_config.get("VolumesFrom"))
        or bool(host_config.get("Devices"))
        or bool(host_config.get("DeviceRequests"))
        or bool(host_config.get("DeviceCgroupRules"))
        or bool(host_config.get("ExtraHosts"))
        or bool(host_config.get("Links"))
        or bool(host_config.get("PortBindings"))
        or host_config.get("PublishAllPorts") is not False
        or host_config.get("PidMode") not in (None, "")
        or host_config.get("IpcMode") not in (None, "", "private")
        or host_config.get("UTSMode") not in (None, "")
        or host_config.get("UsernsMode") not in (None, "")
        or host_config.get("CgroupnsMode") not in (None, "", "private")
        or host_config.get("NetworkMode")
        not in (None, "", profile.network, profile.gateway_egress_network)
        or bool(host_config.get("Dns"))
        or bool(host_config.get("DnsOptions"))
        or bool(host_config.get("DnsSearch"))
        or not ports_are_unpublished
    ):
        return False
    state = gateway.get("State")
    return bool(
        isinstance(state, dict)
        and state.get("Running") is True
        and state.get("Paused") in (None, False)
        and state.get("Restarting") in (None, False)
    )


def _require_gateway_network(
    docker: str,
    profile: ContainerWorkerProfile,
    translation: Mapping[str, Any],
) -> None:
    """Require an internal one-gateway Docker network before exposing a secret."""

    if profile.network == "none":
        return
    provider = _provider_profile(profile.provider_secret_path, profile.gateway_container or "")
    for request_field, provider_field in (
        ("profileId", "profileId"),
        ("providerId", "providerId"),
        ("modelId", "modelId"),
    ):
        if translation.get(request_field) != provider.get(provider_field):
            raise PdfTranslationSupervisorError(
                "provider_profile_mismatch",
                "request translation identity differs from the trusted provider profile",
            )
    result = _bounded_process(
        [docker, "network", "inspect", profile.network],
        timeout=20,
        stdout_limit=256 * 1024,
        stderr_limit=64 * 1024,
        label="gateway network inspection",
    )
    if result.returncode != 0:
        raise PdfTranslationSupervisorError(
            "gateway_network_unavailable", "private translation network is unavailable"
        )
    try:
        inspected = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PdfTranslationSupervisorError(
            "gateway_network_unavailable", "private translation network metadata is invalid"
        ) from error
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise PdfTranslationSupervisorError(
            "gateway_network_unavailable", "private translation network metadata is ambiguous"
        )
    network = inspected[0]
    containers = network.get("Containers")
    if (
        network.get("Name") != profile.network
        or network.get("Internal") is not True
        or network.get("Driver") != "bridge"
        or network.get("Scope") != "local"
        or not isinstance(containers, dict)
    ):
        raise PdfTranslationSupervisorError(
            "unsafe_network", "translation network is not a local internal bridge"
        )
    attached_names = {
        str(item.get("Name", "")).lstrip("/")
        for item in containers.values()
        if isinstance(item, dict)
    }
    if attached_names != {profile.gateway_container}:
        raise PdfTranslationSupervisorError(
            "unsafe_network", "translation network contains an unreviewed peer"
        )
    container_result = _bounded_process(
        [docker, "container", "inspect", profile.gateway_container or ""],
        timeout=20,
        stdout_limit=256 * 1024,
        stderr_limit=64 * 1024,
        label="translation gateway inspection",
    )
    if container_result.returncode != 0:
        raise PdfTranslationSupervisorError(
            "gateway_network_unavailable", "translation gateway is unavailable"
        )
    try:
        inspected_containers = json.loads(container_result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PdfTranslationSupervisorError(
            "gateway_network_unavailable", "translation gateway metadata is invalid"
        ) from error
    if (
        not isinstance(inspected_containers, list)
        or len(inspected_containers) != 1
        or not isinstance(inspected_containers[0], dict)
    ):
        raise PdfTranslationSupervisorError(
            "gateway_network_unavailable", "translation gateway identity is ambiguous"
        )
    gateway = inspected_containers[0]
    host_config = gateway.get("HostConfig")
    gateway_networks = gateway.get("NetworkSettings", {}).get("Networks")
    security_options = set(host_config.get("SecurityOpt") or []) if isinstance(
        host_config, dict
    ) else set()
    no_new_privileges = {
        "no-new-privileges",
        "no-new-privileges:true",
        "no-new-privileges=true",
    }
    image_result = _bounded_process(
        [docker, "image", "inspect", profile.gateway_image_digest or ""],
        timeout=20,
        stdout_limit=256 * 1024,
        stderr_limit=64 * 1024,
        label="translation gateway image inspection",
    )
    if image_result.returncode != 0:
        raise PdfTranslationSupervisorError(
            "gateway_network_unavailable",
            "reviewed translation gateway image is unavailable",
        )
    try:
        inspected_images = json.loads(image_result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PdfTranslationSupervisorError(
            "gateway_network_unavailable",
            "translation gateway image metadata is invalid",
        ) from error
    if (
        not isinstance(inspected_images, list)
        or len(inspected_images) != 1
        or not isinstance(inspected_images[0], dict)
        or inspected_images[0].get("Id") != profile.gateway_image_digest
        or not isinstance(inspected_images[0].get("Config"), dict)
    ):
        raise PdfTranslationSupervisorError(
            "unsafe_network", "translation gateway image identity is ambiguous"
        )
    if (
        str(gateway.get("Name", "")).lstrip("/") != profile.gateway_container
        or gateway.get("Image") != profile.gateway_image_digest
        or not isinstance(host_config, dict)
        or host_config.get("ReadonlyRootfs") is not True
        or host_config.get("Privileged") is not False
        or "ALL" not in set(host_config.get("CapDrop") or [])
        or bool(host_config.get("CapAdd"))
        or len(security_options & no_new_privileges) != 1
        or bool(security_options - no_new_privileges)
        or not isinstance(gateway_networks, dict)
        or set(gateway_networks)
        != {profile.network, profile.gateway_egress_network}
        or not _gateway_runtime_config_is_reviewed(
            gateway,
            image_config=inspected_images[0]["Config"],
            profile=profile,
        )
    ):
        raise PdfTranslationSupervisorError(
            "unsafe_network", "translation gateway runtime differs from reviewed isolation"
        )


def _require_profile_provenance(
    health: Mapping[str, Any], profile: ContainerWorkerProfile
) -> None:
    expected = {
        "backendId": profile.backend_id,
        "sourceRevision": profile.source_revision,
        "sbomSha256": profile.sbom_sha256,
        "lockSha256": profile.lock_sha256,
        "imageDigest": profile.image_digest,
        "buildDigest": profile.image_digest,
    }
    mismatches = [field for field, value in expected.items() if health.get(field) != value]
    if mismatches:
        raise PdfTranslationSupervisorError(
            "health_provenance_mismatch",
            "worker health differs from reviewed profile provenance: "
            + ", ".join(sorted(mismatches)),
        )
    if profile.font_sha256 is not None:
        font_digests = health.get("fontDigests")
        if not isinstance(font_digests, dict) or profile.font_sha256 not in set(
            font_digests.values()
        ):
            raise PdfTranslationSupervisorError(
                "health_provenance_mismatch",
                "worker health does not contain the reviewed font digest",
            )


def _worker_failure_code(events: bytes, run_id: str) -> str:
    try:
        validated = validate_ndjson_events(events, run_id=run_id, require_terminal=True)
    except PdfTranslationContractError:
        return "worker_failed"
    terminal = validated[-1]
    if terminal["type"] == "failed":
        return str(terminal["code"])
    return "worker_failed"


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
    _require_gateway_network(
        docker_path,
        profile,
        validated_request["translation"],
    )

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
        _require_profile_provenance(health, profile)

        worker_output = temporary_root / "worker-output"
        run_name = f"papertrans-{run_id}-{nonce}"
        secret_volume: str | None = None
        output_volume: str | None = None
        try:
            output_volume = _create_bounded_output_volume(
                docker_path,
                nonce=nonce,
                output_limit_bytes=validated_request["limits"]["maxOutputBytes"],
            )
            if profile.provider_secret_path is not None:
                secret_volume = _seed_secret_volume(
                    docker_path,
                    profile,
                    nonce=nonce,
                )
            keeper_process = _run_process(
                container_command(
                    docker_path,
                    profile,
                    command="run",
                    container_name=run_name,
                    request_path=request_path,
                    source_path=source_copy,
                    output_path=worker_output,
                    output_limit_bytes=validated_request["limits"]["maxOutputBytes"],
                    secret_volume=secret_volume,
                    output_volume=output_volume,
                ),
                timeout=60,
                docker=docker_path,
                container_name=run_name,
            )
            if keeper_process.returncode != 0:
                raise PdfTranslationSupervisorError(
                    "worker_start_failed", "isolated worker keeper failed to start"
                )
            _verify_keeper_output_mount(
                docker_path,
                container_name=run_name,
            )
            run_process = _run_process(
                worker_exec_command(
                    docker_path,
                    profile,
                    container_name=run_name,
                ),
                timeout=validated_request["limits"]["deadlineSeconds"] + 15,
                docker=docker_path,
                container_name=run_name,
            )
            events = run_process.stdout
            if run_process.returncode != 0:
                failure_code = _worker_failure_code(events, run_id)
                raise PdfTranslationSupervisorError(
                    failure_code,
                    f"isolated worker failed with stable code {failure_code}",
                )
            _copy_container_output(docker_path, run_name, worker_output)
            _reject_retained_provider_secrets(
                profile,
                staging_dir=worker_output,
                events=events,
            )
        finally:
            _remove_container(docker_path, run_name)
            _remove_volume(docker_path, secret_volume)
            _remove_volume(docker_path, output_volume)
        return publish_candidate_run(
            output_root=Path(output_root),
            slug=slug,
            source_pdf=source_pdf,
            staging_dir=worker_output,
            request=validated_request,
            health=health,
            events_ndjson=events,
            approved_fork_revisions=approved_fork_revisions,
            # Networked engines may write provider payloads or credentials to
            # stderr despite adapter log suppression. Never persist that
            # stream for a secret-bearing run.
            worker_log=(
                ""
                if profile.provider_secret_path is not None
                else run_process.stderr.decode("utf-8", errors="replace")[-64_000:]
            ),
            run_purpose=profile.purpose,
            promotion_eligible=profile.promotion_eligible,
        )


def profile_from_files(
    *,
    backend_id: str,
    image: str,
    source_file: Path,
    sbom_file: Path,
    lock_file: Path,
    font_file: Path | None = None,
    network: str = "none",
    provider_secret_file: Path | None = None,
    gateway_container: str | None = None,
    gateway_image_digest: str | None = None,
    gateway_egress_network: str | None = None,
    purpose: str = "layout_evaluation",
    promotion_eligible: bool = False,
    worker_executable: str = "/usr/local/bin/papertrans-harumi-worker",
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
        network=network,
        provider_secret_path=(
            Path(provider_secret_file).resolve() if provider_secret_file else None
        ),
        gateway_container=gateway_container,
        gateway_image_digest=gateway_image_digest,
        gateway_egress_network=gateway_egress_network,
        purpose=purpose,
        promotion_eligible=promotion_eligible,
        worker_executable=worker_executable,
    )


__all__ = [
    "ContainerWorkerProfile",
    "PdfTranslationContractError",
    "PdfTranslationSupervisorError",
    "container_command",
    "make_candidate_request",
    "profile_from_files",
    "run_container_candidate",
    "worker_exec_command",
]
