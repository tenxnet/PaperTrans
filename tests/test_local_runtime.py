from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from papertrans import local_runtime
from papertrans.local_setup import LocalPaths


class _ReadinessHandler(BaseHTTPRequestHandler):
    server_version = "PaperTransTest"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/system/health":
            payload = json.dumps({"service": "papertrans", "ready": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.path == "/mcp":
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": "test", "result": {"serverInfo": {"name": "papertrans"}}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class _GenericHandler(_ReadinessHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _serve(handler):
    server = ThreadingHTTPServer((local_runtime.LOOPBACK_HOST, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _paths(tmp_path: Path) -> LocalPaths:
    repo = tmp_path / "repo"
    repo.mkdir()
    return LocalPaths.create(repo)


@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_runtime_options_reject_invalid_ports(port: int) -> None:
    with pytest.raises(local_runtime.RuntimeFailure, match="port"):
        local_runtime.RuntimeOptions(web_port=port).validate()


def test_runtime_options_reject_same_ports() -> None:
    with pytest.raises(local_runtime.RuntimeFailure, match="must be different"):
        local_runtime.RuntimeOptions(web_port=8123, mcp_port=8123).validate()


def test_port_available_does_not_replace_existing_listener() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((local_runtime.LOOPBACK_HOST, 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        assert local_runtime.port_available(port) is False
    finally:
        listener.close()
    assert local_runtime.port_available(port) is True


def test_protocol_readiness_rejects_generic_http_service() -> None:
    good, good_thread = _serve(_ReadinessHandler)
    generic, generic_thread = _serve(_GenericHandler)
    try:
        assert local_runtime.probe_web(good.server_port)
        assert local_runtime.probe_mcp(good.server_port)
        assert not local_runtime.probe_mcp(generic.server_port)
    finally:
        good.shutdown()
        generic.shutdown()
        good.server_close()
        generic.server_close()
        good_thread.join(timeout=2)
        generic_thread.join(timeout=2)


def test_runtime_lock_rejects_second_launcher(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    with local_runtime.runtime_lock(paths):
        with pytest.raises(local_runtime.AlreadyRunning):
            with local_runtime.runtime_lock(paths):
                pass


def test_runtime_environment_is_local_offline_and_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setenv("NODE_OPTIONS", "--require=/tmp/evil.js")
    monkeypatch.setenv("HF_TOKEN", "sentinel-secret")
    supervisor = local_runtime.LocalSupervisor(paths, local_runtime.RuntimeOptions())

    environment = supervisor._environment()

    assert environment["PAPERTRANS_MCP_HOST"] == "127.0.0.1"
    assert environment["PAPERTRANS_REPO_ROOT"] == str(paths.repo_root)
    assert environment["PAPERTRANS_DOCLING_ARTIFACTS_PATH"] == str(paths.model_root)
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert "NODE_OPTIONS" not in environment
    assert "HF_TOKEN" not in environment
    assert "sentinel-secret" not in json.dumps(environment)


def test_service_argv_is_fixed_loopback_and_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(local_runtime, "pnpm_command", lambda: ("/usr/bin/pnpm",))
    supervisor = local_runtime.LocalSupervisor(
        paths,
        local_runtime.RuntimeOptions(web_port=3210, mcp_port=8765),
    )

    mcp = supervisor._mcp_argv()
    web = supervisor._web_argv()

    assert Path(mcp[0]).is_absolute()
    assert mcp[mcp.index("--host") + 1] == "127.0.0.1"
    assert mcp[mcp.index("--port") + 1] == "8765"
    assert web == ("/usr/bin/pnpm", "start", "--hostname", "127.0.0.1", "--port", "3210")


def test_owned_process_group_is_terminated_and_reaped(tmp_path: Path) -> None:
    log_path = tmp_path / "child.log"
    log = log_path.open("wb")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    child = local_runtime.ChildService("test", process, log, log_path)
    local_runtime._terminate_process_group(child, timeout=1)
    log.close()

    assert process.poll() is not None


def test_runtime_state_rejects_symlink(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.runtime_root.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"ready":true}\n', encoding="utf-8")
    (paths.runtime_root / "runtime-state.json").symlink_to(outside)

    assert local_runtime.read_runtime_state(paths) is None


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX SIGHUP is unavailable")
def test_terminal_hangup_requests_supervised_shutdown(tmp_path: Path) -> None:
    supervisor = local_runtime.LocalSupervisor(_paths(tmp_path), local_runtime.RuntimeOptions())
    supervisor._install_signal_handlers()
    try:
        handler = signal.getsignal(signal.SIGHUP)
        assert callable(handler)
        handler(signal.SIGHUP, None)
        assert supervisor.stop_requested.is_set()
    finally:
        supervisor._restore_signal_handlers()
