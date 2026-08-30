import json
import sys
from pathlib import Path

import pymupdf as fitz
import pytest

import papertrans.pdf_translation_supervisor as supervisor
from papertrans.pdf_translation_supervisor import (
    ContainerWorkerProfile,
    PdfTranslationSupervisorError,
    _bounded_process,
    _require_profile_provenance,
    _worker_failure_code,
    container_command,
    make_candidate_request,
)


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def _pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 72), "PaperTrans worker test")
    document.save(path)
    document.close()


def _profile(tmp_path: Path) -> ContainerWorkerProfile:
    font = tmp_path / "font.otf"
    font.write_bytes(b"font")
    import hashlib

    font_hash = hashlib.sha256(font.read_bytes()).hexdigest()
    return ContainerWorkerProfile(
        backend_id="papertrans-harumi",
        image=f"papertrans-harumi@sha256:{HEX_A}",
        source_revision=HEX_B,
        sbom_sha256=HEX_C,
        lock_sha256=HEX_A,
        font_path=font,
        font_sha256=font_hash,
    )


def _provider_secret(tmp_path: Path, *, gateway: str = "papertrans-gateway") -> Path:
    secret = tmp_path / "provider.json"
    secret.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "profileId": "babeldoc-ja-v1",
                "providerId": "openai-compatible",
                "modelId": "translation-model-v1",
                "baseUrl": f"https://{gateway}/v1",
                "apiKey": "test-provider-secret",
                "qps": 1,
            }
        ),
        encoding="utf-8",
    )
    secret.chmod(0o600)
    return secret


def _babel_profile(tmp_path: Path) -> ContainerWorkerProfile:
    gateway = "papertrans-gateway"
    return ContainerWorkerProfile(
        backend_id="papertrans-babeldoc",
        image=f"papertrans-babeldoc@sha256:{HEX_A}",
        source_revision=HEX_B,
        sbom_sha256=HEX_C,
        lock_sha256=HEX_A,
        network="papertrans-translation-private",
        provider_secret_path=_provider_secret(tmp_path, gateway=gateway),
        gateway_container=gateway,
        gateway_image_digest=f"sha256:{HEX_D}",
        gateway_egress_network="papertrans-gateway-egress",
        worker_executable="/opt/venv/bin/papertrans-pdf-worker",
    )


def _health_for_profile(profile: ContainerWorkerProfile) -> dict:
    return {
        "schemaVersion": 1,
        "protocolVersion": 1,
        "backendId": profile.backend_id,
        "adapterVersion": "0.1.0",
        "engineVersion": "1.19.0",
        "dependencies": {"harumi": "1.19.0", "harumi-ai": "0.9.0"},
        "sourceRevision": profile.source_revision,
        "forkRevision": None,
        "buildDigest": profile.image_digest,
        "imageDigest": profile.image_digest,
        "sbomSha256": profile.sbom_sha256,
        "lockSha256": profile.lock_sha256,
        "capabilities": {"outputs": ["translated_mono_pdf"]},
        "ready": True,
        "modelDigests": {},
        "fontDigests": {"noto-jp": profile.font_sha256},
    }


def _provider_translation() -> dict:
    return {
        "profileId": "babeldoc-ja-v1",
        "providerId": "openai-compatible",
        "modelId": "translation-model-v1",
    }


def _gateway_inspection(profile: ContainerWorkerProfile) -> tuple[list, list]:
    network = [
        {
            "Name": profile.network,
            "Internal": True,
            "Driver": "bridge",
            "Scope": "local",
            "Containers": {
                "gateway-id": {"Name": profile.gateway_container},
            },
        }
    ]
    container = [
        {
            "Name": f"/{profile.gateway_container}",
            "Image": profile.gateway_image_digest,
            "State": {"Running": True},
            "HostConfig": {
                "ReadonlyRootfs": True,
                "Privileged": False,
                "CapDrop": ["ALL"],
                "CapAdd": [],
                "SecurityOpt": ["no-new-privileges"],
            },
            "NetworkSettings": {
                "Networks": {
                    profile.network: {},
                    profile.gateway_egress_network: {},
                }
            },
        }
    ]
    return network, container


def _mock_gateway_inspection(
    monkeypatch: pytest.MonkeyPatch,
    network: list,
    container: list,
) -> None:
    def fake_bounded_process(argv, **_kwargs):
        if argv[1:3] == ["network", "inspect"]:
            payload = network
        elif argv[1:3] == ["container", "inspect"]:
            payload = container
        else:  # pragma: no cover - an unexpected subprocess is a test failure
            raise AssertionError(f"unexpected command: {argv}")
        return supervisor._CapturedProcess(
            returncode=0,
            stdout=json.dumps(payload).encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(supervisor, "_bounded_process", fake_bounded_process)


def test_candidate_request_is_source_bound_and_strict(tmp_path: Path):
    source = tmp_path / "source.pdf"
    _pdf(source)
    request = make_candidate_request(source, run_id="pdf-harumi-01")
    assert request["source"]["bytes"] == source.stat().st_size
    assert request["translation"]["targetLanguage"] == "ja"
    assert request["outputs"] == ["translated_mono_pdf"]


def test_container_command_has_hard_isolation_and_fixed_mounts(tmp_path: Path):
    request = tmp_path / "request.json"
    source = tmp_path / "source.pdf"
    request.write_text("{}", encoding="utf-8")
    source.write_bytes(b"%PDF-fixture")
    output = tmp_path / "staging"
    argv = container_command(
        "/usr/local/bin/docker",
        _profile(tmp_path),
        command="run",
        container_name="papertrans-run-01",
        request_path=request,
        source_path=source,
        output_path=output,
        output_limit_bytes=1024 * 1024,
        output_volume="papertrans-output-test",
    )
    joined = " ".join(argv)
    assert "--network none" in joined
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert argv[argv.index("--user") + 1] == "65532:65532"
    assert "dst=/input/request.json,readonly" in joined
    assert "dst=/input/source.pdf,readonly" in joined
    assert (
        "type=volume,src=papertrans-output-test,dst=/output,volume-nocopy"
    ) in argv
    assert f"type=bind,src={output.resolve()},dst=/output" not in joined
    assert str(output.resolve()) not in joined
    tmpfs_mounts = [
        argv[index + 1]
        for index, value in enumerate(argv[:-1])
        if value == "--tmpfs"
    ]
    assert not any(mount.startswith("/output:") for mount in tmpfs_mounts)
    assert (
        "/tmp:rw,noexec,nosuid,nodev,size=512m,uid=65532,gid=65532,mode=0700"
        in tmpfs_mounts
    )
    assert "fsize=1048576:1048576" in argv
    assert "--pull=never" in argv
    assert "--detach" in argv
    assert argv[argv.index("--entrypoint") + 1] == "/bin/sleep"
    assert argv[-2:] == [f"papertrans-harumi@sha256:{HEX_A}", "infinity"]
    assert "--request" not in argv
    assert not any(value in {"sh", "bash", "/bin/sh", "/bin/bash", "-c"} for value in argv)
    assert output.is_dir()
    assert output.stat().st_mode & 0o777 == 0o700


def test_worker_exec_uses_a_fixed_absolute_executable_without_a_shell(tmp_path: Path):
    profile = _profile(tmp_path)

    argv = supervisor.worker_exec_command(
        "docker",
        profile,
        container_name="papertrans-run-01",
    )

    assert argv == [
        "docker",
        "exec",
        "--user",
        "65532:65532",
        "papertrans-run-01",
        "/usr/local/bin/papertrans-harumi-worker",
        "run",
        "--request",
        "/input/request.json",
        "--source",
        "/input/source.pdf",
        "--output",
        "/output",
    ]
    assert not any(value in {"sh", "bash", "/bin/sh", "/bin/bash", "-c"} for value in argv)


def test_worker_executable_must_be_a_fixed_absolute_path(tmp_path: Path):
    profile = _profile(tmp_path)
    relative = ContainerWorkerProfile(
        **{**profile.__dict__, "worker_executable": "papertrans-worker"}
    )

    with pytest.raises(PdfTranslationSupervisorError) as error:
        relative.validate()
    assert error.value.code == "invalid_profile"


def test_container_run_requires_an_ephemeral_output_volume(tmp_path: Path):
    request = tmp_path / "request.json"
    source = tmp_path / "source.pdf"
    request.write_text("{}", encoding="utf-8")
    source.write_bytes(b"%PDF-fixture")

    with pytest.raises(PdfTranslationSupervisorError) as error:
        container_command(
            "/usr/local/bin/docker",
            _profile(tmp_path),
            command="run",
            container_name="papertrans-run-no-volume",
            request_path=request,
            source_path=source,
            output_path=tmp_path / "missing-output-volume",
            output_limit_bytes=1024 * 1024,
        )
    assert error.value.code == "invalid_command"


def test_bounded_output_volume_uses_a_quota_owned_by_the_worker_uid(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[list[str]] = []

    def fake_bounded_process(argv, **_kwargs):
        calls.append(list(argv))
        return supervisor._CapturedProcess(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(supervisor, "_bounded_process", fake_bounded_process)

    volume = supervisor._create_bounded_output_volume(
        "docker",
        nonce="abc123",
        output_limit_bytes=1024 * 1024,
    )

    assert volume == "papertrans-output-abc123"
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:5] == ["docker", "volume", "create", "--driver", "local"]
    assert "org.papertrans.ephemeral-worker-output=1" in argv
    option_values = [
        argv[index + 1]
        for index, value in enumerate(argv[:-1])
        if value == "--opt"
    ]
    assert option_values == [
        "type=tmpfs",
        "device=tmpfs",
        "o=size=9437184,uid=65532,gid=65532,mode=0700,noexec,nosuid,nodev",
    ]


def test_live_output_mount_requires_restricted_tmpfs(
    monkeypatch: pytest.MonkeyPatch,
):
    safe = (
        "129 120 0:56 / /output rw,nosuid,nodev,noexec,relatime master:3 "
        "- tmpfs tmpfs rw,size=9216k,mode=700,uid=65532,gid=65532\n"
    )
    monkeypatch.setattr(
        supervisor,
        "_bounded_process",
        lambda *_args, **_kwargs: supervisor._CapturedProcess(
            returncode=0, stdout=safe.encode("utf-8"), stderr=b""
        ),
    )

    supervisor._verify_keeper_output_mount(
        "docker", container_name="papertrans-run-safe"
    )

    unsafe = safe.replace(",noexec", "")
    monkeypatch.setattr(
        supervisor,
        "_bounded_process",
        lambda *_args, **_kwargs: supervisor._CapturedProcess(
            returncode=0, stdout=unsafe.encode("utf-8"), stderr=b""
        ),
    )
    with pytest.raises(PdfTranslationSupervisorError) as error:
        supervisor._verify_keeper_output_mount(
            "docker", container_name="papertrans-run-unsafe"
        )
    assert error.value.code == "unsafe_output_mount"


def test_run_failure_removes_the_ephemeral_output_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "source.pdf"
    _pdf(source)
    request = make_candidate_request(
        source,
        run_id="pdf-cleanup-01",
        max_output_bytes=1024 * 1024,
    )
    profile = _profile(tmp_path)
    removed_volumes: list[str] = []

    monkeypatch.setattr(
        supervisor,
        "_create_bounded_output_volume",
        lambda *_args, **_kwargs: "papertrans-output-cleanup",
    )

    def fake_run_process(argv, **_kwargs):
        if argv[-1] == "health":
            return supervisor._CapturedProcess(
                returncode=0,
                stdout=json.dumps(_health_for_profile(profile)).encode("utf-8"),
                stderr=b"",
            )
        if argv[-1] == "infinity":
            return supervisor._CapturedProcess(
                returncode=0,
                stdout=b"keeper-id",
                stderr=b"",
            )
        raise PdfTranslationSupervisorError("worker_failed", "simulated failure")

    monkeypatch.setattr(supervisor, "_run_process", fake_run_process)
    monkeypatch.setattr(supervisor, "_verify_keeper_output_mount", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supervisor, "_remove_container", lambda *_args: None)
    monkeypatch.setattr(
        supervisor,
        "_remove_volume",
        lambda _docker, name: removed_volumes.append(name) if name else None,
    )

    with pytest.raises(PdfTranslationSupervisorError) as error:
        supervisor.run_container_candidate(
            output_root=tmp_path / "output",
            slug="cleanup-paper",
            source_pdf=source,
            request=request,
            profile=profile,
            docker="docker",
        )

    assert error.value.code == "worker_failed"
    assert removed_volumes == ["papertrans-output-cleanup"]


def test_secret_bearing_worker_stderr_is_never_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "source.pdf"
    _pdf(source)
    request = make_candidate_request(
        source,
        run_id="pdf-secret-log-01",
        max_output_bytes=1024 * 1024,
    )
    profile = _babel_profile(tmp_path)
    health = _health_for_profile(profile)
    captured_publish: dict = {}
    sentinel = b"provider-secret-sentinel"

    monkeypatch.setattr(supervisor, "_require_gateway_network", lambda *_args: None)
    monkeypatch.setattr(
        supervisor,
        "validate_backend_health",
        lambda *_args, **_kwargs: health,
    )
    monkeypatch.setattr(
        supervisor,
        "_create_bounded_output_volume",
        lambda *_args, **_kwargs: "papertrans-output-secret-log",
    )
    monkeypatch.setattr(
        supervisor,
        "_seed_secret_volume",
        lambda *_args, **_kwargs: "papertrans-secret-log",
    )

    def fake_run_process(argv, **_kwargs):
        if argv[-1] == "health":
            return supervisor._CapturedProcess(
                returncode=0,
                stdout=b"{}",
                stderr=b"",
            )
        if argv[-1] == "infinity":
            return supervisor._CapturedProcess(
                returncode=0,
                stdout=b"keeper-id",
                stderr=b"",
            )
        assert argv[1] == "exec"
        return supervisor._CapturedProcess(
            returncode=0,
            stdout=b"validated-events-placeholder",
            stderr=sentinel,
        )

    def fake_publish_candidate_run(**kwargs):
        captured_publish.update(kwargs)
        return tmp_path / "published-run"

    monkeypatch.setattr(supervisor, "_run_process", fake_run_process)
    monkeypatch.setattr(supervisor, "_verify_keeper_output_mount", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supervisor, "_copy_container_output", lambda *_args: None)
    monkeypatch.setattr(supervisor, "_remove_container", lambda *_args: None)
    monkeypatch.setattr(supervisor, "_remove_volume", lambda *_args: None)
    monkeypatch.setattr(
        supervisor,
        "publish_candidate_run",
        fake_publish_candidate_run,
    )

    supervisor.run_container_candidate(
        output_root=tmp_path / "output",
        slug="secret-log-paper",
        source_pdf=source,
        request=request,
        profile=profile,
        docker="docker",
    )

    assert captured_publish["worker_log"] == ""
    assert sentinel.decode("ascii") not in captured_publish["worker_log"]


def test_secret_bearing_backend_report_is_rejected_before_publication(
    tmp_path: Path,
):
    profile = _babel_profile(tmp_path)
    staging = tmp_path / "worker-output"
    report = staging / "artifacts" / "backend-report.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps({"apiKey": "test-provider-secret"}), encoding="utf-8"
    )

    with pytest.raises(PdfTranslationSupervisorError) as error:
        supervisor._reject_retained_provider_secrets(
            profile,
            staging_dir=staging,
            events=b'{"type":"completed"}\n',
        )
    assert error.value.code == "secret_leak"


def test_babeldoc_health_stays_offline_while_run_uses_bounded_gateway_resources(
    tmp_path: Path,
):
    profile = _babel_profile(tmp_path)
    request = tmp_path / "request.json"
    source = tmp_path / "source.pdf"
    request.write_text("{}", encoding="utf-8")
    source.write_bytes(b"%PDF-fixture")

    health = container_command(
        "/usr/local/bin/docker",
        profile,
        command="health",
        container_name="papertrans-health-01",
    )
    assert health[health.index("--network") + 1] == "none"
    assert "--rm" in health
    assert "--detach" not in health
    assert health[-2:] == [profile.image, "health"]
    assert "/run/secrets" not in " ".join(health)
    health_joined = " ".join(health)
    assert (
        "/opt/papertrans/home/.cache/babeldoc:"
        "rw,noexec,nosuid,nodev,size=134217728,uid=65532,gid=65532,mode=0700"
    ) in health_joined
    assert (
        "/opt/papertrans/home/.cache/pdf2zh_next:"
        "rw,noexec,nosuid,nodev,size=134217728,uid=65532,gid=65532,mode=0700"
    ) in health_joined

    run = container_command(
        "/usr/local/bin/docker",
        profile,
        command="run",
        container_name="papertrans-babeldoc-01",
        request_path=request,
        source_path=source,
        output_path=tmp_path / "babeldoc-output",
        output_limit_bytes=1024 * 1024,
        secret_volume="papertrans-secret-test",
        output_volume="papertrans-output-babeldoc-test",
    )
    joined = " ".join(run)
    assert run[run.index("--network") + 1] == profile.network
    assert "--detach" in run
    assert run[run.index("--entrypoint") + 1] == "/bin/sleep"
    assert run[-2:] == [profile.image, "infinity"]
    assert (
        "type=volume,src=papertrans-secret-test,dst=/run/secrets,"
        "volume-nocopy,readonly"
    ) in run
    assert (
        "type=volume,src=papertrans-output-babeldoc-test,dst=/output,volume-nocopy"
    ) in run
    assert (
        "/opt/papertrans/home/.cache/babeldoc:"
        "rw,noexec,nosuid,nodev,size=134217728,uid=65532,gid=65532,mode=0700"
    ) in run
    assert (
        "/opt/papertrans/home/.cache/pdf2zh_next:"
        "rw,noexec,nosuid,nodev,size=134217728,uid=65532,gid=65532,mode=0700"
    ) in run
    assert (
        "/opt/papertrans/home/.config:"
        "rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532,mode=0700"
    ) in run
    assert str(profile.provider_secret_path) not in joined
    assert f"src={profile.provider_secret_path}" not in joined


def test_provider_secret_is_seeded_via_stdin_into_an_ephemeral_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    profile = _babel_profile(tmp_path)
    calls: list[tuple[list[str], bytes | None]] = []

    def fake_bounded_process(argv, **kwargs):
        calls.append((list(argv), kwargs.get("stdin_data")))
        return supervisor._CapturedProcess(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(supervisor, "_bounded_process", fake_bounded_process)

    volume = supervisor._seed_secret_volume("docker", profile, nonce="abc123")

    assert volume == "papertrans-secret-abc123"
    assert calls[0][0][:3] == ["docker", "volume", "create"]
    seed_argv, seed_stdin = calls[1]
    assert seed_argv[seed_argv.index("--network") + 1] == "none"
    assert "--interactive" in seed_argv
    assert "--cap-drop=ALL" in seed_argv
    assert "--cap-add=CHOWN" in seed_argv
    assert (
        "type=volume,src=papertrans-secret-abc123,dst=/run/secrets,volume-nocopy"
    ) in seed_argv
    assert str(profile.provider_secret_path) not in " ".join(seed_argv)
    assert seed_stdin == profile.provider_secret_path.read_bytes()


def test_reviewed_gateway_runtime_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    profile = _babel_profile(tmp_path)
    network, container = _gateway_inspection(profile)
    _mock_gateway_inspection(monkeypatch, network, container)

    supervisor._require_gateway_network(
        "docker", profile, _provider_translation()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "non-internal",
        "extra-peer",
        "gateway-image",
        "writable-root",
        "privileged",
        "capabilities",
        "capability-addition",
        "missing-no-new-privileges",
        "false-no-new-privileges",
        "extra-security-option",
        "network-bindings",
    ],
)
def test_gateway_runtime_must_match_every_reviewed_isolation_property(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
):
    profile = _babel_profile(tmp_path)
    network, container = _gateway_inspection(profile)
    if mutation == "non-internal":
        network[0]["Internal"] = False
    elif mutation == "extra-peer":
        network[0]["Containers"]["unexpected-id"] = {"Name": "unexpected-peer"}
    elif mutation == "gateway-image":
        container[0]["Image"] = f"sha256:{HEX_C}"
    elif mutation == "writable-root":
        container[0]["HostConfig"]["ReadonlyRootfs"] = False
    elif mutation == "privileged":
        container[0]["HostConfig"]["Privileged"] = True
    elif mutation == "capabilities":
        container[0]["HostConfig"]["CapDrop"] = []
    elif mutation == "capability-addition":
        container[0]["HostConfig"]["CapAdd"] = ["NET_ADMIN"]
    elif mutation == "missing-no-new-privileges":
        container[0]["HostConfig"]["SecurityOpt"] = []
    elif mutation == "false-no-new-privileges":
        container[0]["HostConfig"]["SecurityOpt"] = ["no-new-privileges:false"]
    elif mutation == "extra-security-option":
        container[0]["HostConfig"]["SecurityOpt"] = [
            "no-new-privileges:true",
            "seccomp=unconfined",
        ]
    else:
        container[0]["NetworkSettings"]["Networks"]["unexpected-network"] = {}
    _mock_gateway_inspection(monkeypatch, network, container)

    with pytest.raises(PdfTranslationSupervisorError) as error:
        supervisor._require_gateway_network(
            "docker", profile, _provider_translation()
        )
    assert error.value.code == "unsafe_network"


@pytest.mark.parametrize(
    "value",
    ["no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"],
)
def test_gateway_accepts_docker_canonical_true_no_new_privileges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
):
    profile = _babel_profile(tmp_path)
    network, container = _gateway_inspection(profile)
    container[0]["HostConfig"]["SecurityOpt"] = [value]
    _mock_gateway_inspection(monkeypatch, network, container)

    supervisor._require_gateway_network("docker", profile, _provider_translation())


@pytest.mark.parametrize("field", ["profileId", "providerId", "modelId"])
def test_request_translation_identity_must_match_the_provider_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
):
    profile = _babel_profile(tmp_path)
    translation = _provider_translation()
    translation[field] = "different"
    monkeypatch.setattr(
        supervisor,
        "_bounded_process",
        lambda *_args, **_kwargs: pytest.fail(
            "identity mismatch must fail before Docker inspection"
        ),
    )

    with pytest.raises(PdfTranslationSupervisorError) as error:
        supervisor._require_gateway_network("docker", profile, translation)
    assert error.value.code == "provider_profile_mismatch"


def test_gateway_image_must_be_an_immutable_digest(tmp_path: Path):
    profile = _babel_profile(tmp_path)
    mutable_gateway = ContainerWorkerProfile(
        **{**profile.__dict__, "gateway_image_digest": "papertrans-gateway:latest"}
    )

    with pytest.raises(PdfTranslationSupervisorError) as error:
        mutable_gateway.validate()
    assert error.value.code == "unsafe_network"


def test_offline_profile_rejects_provider_credentials(tmp_path: Path):
    offline = _profile(tmp_path)
    credentialed = ContainerWorkerProfile(
        **{
            **offline.__dict__,
            "provider_secret_path": _provider_secret(tmp_path),
        }
    )

    with pytest.raises(PdfTranslationSupervisorError) as error:
        credentialed.validate()
    assert error.value.code == "invalid_profile"


def test_harumi_profile_is_layout_only_and_can_never_be_promotable(tmp_path: Path):
    profile = _profile(tmp_path)
    assert profile.purpose == "layout_evaluation"
    assert profile.promotion_eligible is False
    promotable = ContainerWorkerProfile(
        **{
            **profile.__dict__,
            "purpose": "semantic_translation",
            "promotion_eligible": True,
        }
    )

    with pytest.raises(PdfTranslationSupervisorError) as error:
        promotable.validate()
    assert error.value.code == "invalid_profile"


@pytest.mark.parametrize("stream_fd", [1, 2])
def test_process_capture_fails_closed_when_either_stream_exceeds_limit(
    stream_fd: int,
):
    with pytest.raises(PdfTranslationSupervisorError) as error:
        _bounded_process(
            [
                sys.executable,
                "-c",
                f"import os; os.write({stream_fd}, b'x' * 8192)",
            ],
            timeout=5,
            stdout_limit=1024,
            stderr_limit=1024,
            label="test worker",
        )
    assert error.value.code == "worker_output_limit"


def _matching_health(profile: ContainerWorkerProfile) -> dict:
    return {
        "backendId": profile.backend_id,
        "sourceRevision": profile.source_revision,
        "sbomSha256": profile.sbom_sha256,
        "lockSha256": profile.lock_sha256,
        "imageDigest": profile.image_digest,
        "buildDigest": profile.image_digest,
        "fontDigests": {"noto-cjk-ja": profile.font_sha256},
    }


@pytest.mark.parametrize(
    "field",
    [
        "backendId",
        "sourceRevision",
        "sbomSha256",
        "lockSha256",
        "imageDigest",
        "buildDigest",
    ],
)
def test_health_must_match_every_reviewed_profile_provenance_field(
    tmp_path: Path, field: str
):
    profile = _profile(tmp_path)
    health = _matching_health(profile)
    health[field] = "different" if field == "backendId" else f"sha256:{HEX_D}"
    if field in {"sourceRevision", "sbomSha256", "lockSha256"}:
        health[field] = HEX_D

    with pytest.raises(PdfTranslationSupervisorError) as error:
        _require_profile_provenance(health, profile)
    assert error.value.code == "health_provenance_mismatch"


def test_health_must_contain_the_reviewed_font_digest(tmp_path: Path):
    profile = _profile(tmp_path)
    health = _matching_health(profile)
    health["fontDigests"] = {"noto-cjk-ja": HEX_B}

    with pytest.raises(PdfTranslationSupervisorError) as error:
        _require_profile_provenance(health, profile)
    assert error.value.code == "health_provenance_mismatch"


def test_worker_failure_exposes_only_a_validated_stable_event_code():
    run_id = "pdf-harumi-01"
    events = b"\n".join(
        json.dumps(event).encode("utf-8")
        for event in (
            {
                "schemaVersion": 1,
                "runId": run_id,
                "sequence": 1,
                "time": "2026-08-30T00:00:00Z",
                "type": "started",
            },
            {
                "schemaVersion": 1,
                "runId": run_id,
                "sequence": 2,
                "time": "2026-08-30T00:00:01Z",
                "type": "failed",
                "code": "translation_provider_failed",
                "message": "untrusted details that must not become the host error code",
            },
        )
    )
    assert _worker_failure_code(events, run_id) == "translation_provider_failed"
    assert _worker_failure_code(b"raw worker traceback", run_id) == "worker_failed"


def test_mutable_images_and_networked_profiles_are_rejected(tmp_path: Path):
    profile = _profile(tmp_path)
    mutable = ContainerWorkerProfile(
        **{**profile.__dict__, "image": "papertrans-harumi:latest"}
    )
    with pytest.raises(PdfTranslationSupervisorError, match="immutable"):
        mutable.validate()
    networked = ContainerWorkerProfile(
        **{**profile.__dict__, "network": "bridge"}
    )
    with pytest.raises(PdfTranslationSupervisorError) as error:
        networked.validate()
    assert error.value.code == "unsafe_network"
