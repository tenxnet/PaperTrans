"""Foreground supervisor for the local PaperTrans Web and MCP services."""

from __future__ import annotations

import contextlib
import fcntl
import http.client
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Sequence

from . import __release__
from .local_setup import (
    LocalPaths,
    SetupError,
    atomic_write_json,
    ensure_private_directory,
    pnpm_command,
    sanitized_environment,
)


LOOPBACK_HOST = "127.0.0.1"


class RuntimeFailure(RuntimeError):
    """A supervised service failed or could not be started safely."""


class AlreadyRunning(RuntimeFailure):
    """Another launcher owns the runtime lock."""


class StartupCancelled(RuntimeFailure):
    """A shutdown signal arrived while a service was becoming ready."""


@dataclass(frozen=True)
class RuntimeOptions:
    web_port: int = 3000
    mcp_port: int = 8000
    startup_timeout: float = 90.0
    shutdown_timeout: float = 8.0
    dev: bool = False
    no_browser: bool = False

    def validate(self) -> None:
        for label, value in (("Web", self.web_port), ("MCP", self.mcp_port)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
                raise RuntimeFailure(f"{label} port must be an integer from 1 to 65535")
        if self.web_port == self.mcp_port:
            raise RuntimeFailure("Web and MCP ports must be different")
        if not 1 <= self.startup_timeout <= 600:
            raise RuntimeFailure("startup timeout must be between 1 and 600 seconds")
        if not 1 <= self.shutdown_timeout <= 60:
            raise RuntimeFailure("shutdown timeout must be between 1 and 60 seconds")


@dataclass
class ChildService:
    name: str
    process: subprocess.Popen[bytes]
    log: BinaryIO
    log_path: Path


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((LOOPBACK_HOST, port))
        except OSError:
            return False
    return True


def _safe_log(path: Path) -> BinaryIO:
    ensure_private_directory(path.parent)
    if path.is_symlink():
        raise RuntimeFailure(f"refusing to write through a log symlink: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise RuntimeFailure(f"could not open service log {path}: {error}") from error
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "wb", buffering=0)


@contextlib.contextmanager
def runtime_lock(paths: LocalPaths) -> Iterator[int]:
    ensure_private_directory(paths.runtime_root)
    lock_path = paths.runtime_root / "papertrans.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise RuntimeFailure(f"could not open runtime lock: {error}") from error
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AlreadyRunning("PaperTrans is already running in this data directory") from error
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield descriptor
    finally:
        os.close(descriptor)


def ensure_runtime_available(paths: LocalPaths) -> None:
    """Reject an already-running supervisor before setup mutates dependencies."""

    ensure_private_directory(paths.runtime_root)
    lock_path = paths.runtime_root / "papertrans.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise RuntimeFailure(f"could not open runtime lock: {error}") from error
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AlreadyRunning("PaperTrans is already running in this data directory") from error
    finally:
        os.close(descriptor)


def probe_mcp(port: int, timeout: float = 1.0) -> bool:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "papertrans-readiness",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "papertrans-launcher", "version": __release__},
            },
        }
    ).encode("utf-8")
    connection = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=timeout)
    try:
        connection.request(
            "POST",
            "/mcp",
            body=body,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        payload = response.read(65_536)
        return (
            response.status == 200
            and b"jsonrpc" in payload
            and b"papertrans" in payload.lower()
        )
    except (OSError, http.client.HTTPException, TimeoutError):
        return False
    finally:
        connection.close()


def probe_web(port: int, timeout: float = 1.0) -> bool:
    connection = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=timeout)
    try:
        connection.request("GET", "/api/system/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            response.read(1024)
            return False
        payload = json.loads(response.read(65_536))
        return (
            isinstance(payload, dict)
            and payload.get("service") == "papertrans"
            and payload.get("ready") is True
        )
    except (OSError, ValueError, http.client.HTTPException, TimeoutError):
        return False
    finally:
        connection.close()


def _wait_until_ready(
    child: ChildService,
    probe: Callable[[], bool],
    *,
    timeout: float,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled is not None and cancelled():
            raise StartupCancelled("startup interrupted")
        returncode = child.process.poll()
        if returncode is not None:
            raise RuntimeFailure(
                f"{child.name} exited during startup with code {returncode}; see {child.log_path}"
            )
        if probe():
            return
        time.sleep(0.2)
    raise RuntimeFailure(f"{child.name} did not become ready; see {child.log_path}")


def _terminate_process_group(child: ChildService, timeout: float) -> None:
    # Always signal the process group we created, even if its leader has
    # already exited: pnpm or another wrapper may have left a descendant alive.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.process.pid, signal.SIGTERM)
    try:
        child.process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.process.wait(timeout=2)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(child.process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.process.pid, signal.SIGKILL)


def _fixed_browser_command(url: str) -> tuple[str, ...] | None:
    if sys.platform == "darwin":
        executable = shutil.which("open")
        return (str(Path(executable).resolve()), url) if executable else None
    if sys.platform.startswith("linux"):
        executable = shutil.which("xdg-open")
        return (str(Path(executable).resolve()), url) if executable else None
    return None


def open_browser(url: str) -> bool:
    command = _fixed_browser_command(url)
    if command is None:
        return False
    try:
        subprocess.Popen(
            command,
            cwd="/",
            env=sanitized_environment(runtime=True),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            shell=False,
        )
    except OSError:
        return False
    return True


class LocalSupervisor:
    def __init__(self, paths: LocalPaths, options: RuntimeOptions):
        options.validate()
        self.paths = paths
        self.options = options
        self.children: list[ChildService] = []
        self.stop_requested = threading.Event()
        self._previous_handlers: dict[int, signal.Handlers] = {}

    @property
    def web_url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.options.web_port}/"

    @property
    def mcp_url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.options.mcp_port}/mcp"

    def _environment(self) -> dict[str, str]:
        environment = sanitized_environment(runtime=True)
        environment.update(
            {
                "PAPERTRANS_CLI": str((Path(sys.executable).parent / "papertrans").resolve()),
                "PAPERTRANS_DATA_ROOT": str(self.paths.data_root),
                "PAPERTRANS_DOCLING_ARTIFACTS_PATH": str(self.paths.model_root),
                "PAPERTRANS_MCP_HOST": LOOPBACK_HOST,
                "PAPERTRANS_MCP_PORT": str(self.options.mcp_port),
                "PAPERTRANS_OUTPUT_ROOT": str(self.paths.output_root),
                "PAPERTRANS_REPO_ROOT": str(self.paths.repo_root),
                "PAPERTRANS_VERSION": __release__,
            }
        )
        return environment

    def _spawn(self, name: str, argv: Sequence[str]) -> ChildService:
        if not argv or not Path(argv[0]).is_absolute():
            raise RuntimeFailure(f"internal error: {name} executable must be absolute")
        log_path = self.paths.log_root / f"{name}.log"
        log = _safe_log(log_path)
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=self.paths.repo_root,
                env=self._environment(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                shell=False,
            )
        except OSError as error:
            log.close()
            raise RuntimeFailure(f"could not start {name}: {error}") from error
        child = ChildService(name, process, log, log_path)
        self.children.append(child)
        return child

    def _install_signal_handlers(self) -> None:
        def request_stop(_signum: int, _frame: object) -> None:
            self.stop_requested.set()

        signums = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            signums.append(signal.SIGHUP)
        for signum in signums:
            self._previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)

    def _restore_signal_handlers(self) -> None:
        for signum, handler in self._previous_handlers.items():
            signal.signal(signum, handler)
        self._previous_handlers.clear()

    def _write_state(self) -> None:
        atomic_write_json(
            self.paths.runtime_root / "runtime-state.json",
            {
                "schemaVersion": 1,
                "launcherPid": os.getpid(),
                "mcpPid": next(child.process.pid for child in self.children if child.name == "mcp"),
                "mcpUrl": self.mcp_url,
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "version": __release__,
                "webPid": next(child.process.pid for child in self.children if child.name == "web"),
                "webUrl": self.web_url,
            },
        )

    def _mcp_argv(self) -> tuple[str, ...]:
        return (
            os.path.abspath(sys.executable),
            "-m",
            "papertrans.mcp_server",
            "--transport",
            "streamable-http",
            "--host",
            LOOPBACK_HOST,
            "--port",
            str(self.options.mcp_port),
            "--repo-root",
            str(self.paths.repo_root),
            "--output-root",
            str(self.paths.output_root),
        )

    def _web_argv(self) -> tuple[str, ...]:
        script = "dev" if self.options.dev else "start"
        return (
            *pnpm_command(),
            script,
            "--hostname",
            LOOPBACK_HOST,
            "--port",
            str(self.options.web_port),
        )

    def _check_ports(self) -> None:
        for label, port in (("MCP", self.options.mcp_port), ("Web", self.options.web_port)):
            if not port_available(port):
                raise RuntimeFailure(
                    f"{label} port {port} is already in use; PaperTrans will not stop or replace it"
                )

    def _shutdown(self) -> None:
        for child in reversed(self.children):
            _terminate_process_group(child, self.options.shutdown_timeout)
        for child in self.children:
            child.log.close()
        self.children.clear()
        state_path = self.paths.runtime_root / "runtime-state.json"
        with contextlib.suppress(FileNotFoundError):
            if state_path.is_symlink():
                raise RuntimeFailure(f"refusing to remove runtime state symlink: {state_path}")
            state_path.unlink()

    def run(self) -> int:
        ensure_private_directory(self.paths.runtime_root)
        ensure_private_directory(self.paths.log_root)
        with runtime_lock(self.paths):
            self._check_ports()
            self._install_signal_handlers()
            try:
                mcp = self._spawn("mcp", self._mcp_argv())
                print(f"[PaperTrans] Starting MCP on {self.mcp_url}", flush=True)
                _wait_until_ready(
                    mcp,
                    lambda: probe_mcp(self.options.mcp_port),
                    timeout=self.options.startup_timeout,
                    cancelled=self.stop_requested.is_set,
                )

                web = self._spawn("web", self._web_argv())
                print(f"[PaperTrans] Starting Web on {self.web_url}", flush=True)
                _wait_until_ready(
                    web,
                    lambda: probe_web(self.options.web_port),
                    timeout=self.options.startup_timeout,
                    cancelled=self.stop_requested.is_set,
                )
                self._write_state()
                print(f"[PaperTrans] Ready: {self.web_url}", flush=True)
                print("[PaperTrans] Press Ctrl-C to stop Web and MCP.", flush=True)
                if not self.options.no_browser and not open_browser(self.web_url):
                    print("[PaperTrans] Could not open a browser automatically; use the URL above.", flush=True)

                while not self.stop_requested.wait(0.25):
                    for child in self.children:
                        returncode = child.process.poll()
                        if returncode is not None:
                            raise RuntimeFailure(
                                f"{child.name} exited unexpectedly with code {returncode}; "
                                f"see {child.log_path}"
                            )
                return 0
            except StartupCancelled:
                return 130
            finally:
                try:
                    self._shutdown()
                finally:
                    self._restore_signal_handlers()


def read_runtime_state(paths: LocalPaths) -> dict[str, object] | None:
    state_path = paths.runtime_root / "runtime-state.json"
    try:
        info = state_path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            return None
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
