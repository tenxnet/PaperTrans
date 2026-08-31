"""Bounded artifact I/O and process-tree supervision for the Docling worker."""

from __future__ import annotations

import importlib
import json
import os
import stat
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from .docling_contract import (
    DoclingResourceLimitError,
    DoclingUnavailableError,
    DoclingWorkerError,
)


def write_bounded_json_atomic(
    path: Path,
    value: Any,
    *,
    max_bytes: int | None,
    limit_message: str | None = None,
) -> None:
    """Stream JSON to a temporary file and publish it only when fully bounded."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            encoder = json.JSONEncoder(
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            written = 0
            for chunk in encoder.iterencode(value):
                encoded_size = len(chunk.encode("utf-8"))
                if max_bytes is not None and written + encoded_size + 1 > max_bytes:
                    raise DoclingResourceLimitError(
                        limit_message
                        or f"JSON artifact exceeds {max_bytes} bytes: {path.name}"
                    )
                handle.write(chunk)
                written += encoded_size
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_bounded_worker_json(output_path: Path, *, max_bytes: int) -> Any:
    """Read a regular worker JSON file only after checking its byte ceiling."""

    try:
        output_stat = output_path.lstat()
        if not stat.S_ISREG(output_stat.st_mode):
            raise DoclingWorkerError("Docling worker output is not a regular file")
        if output_stat.st_size > max_bytes:
            raise DoclingResourceLimitError(
                "Docling worker JSON exceeds the output limit "
                f"({output_stat.st_size} > {max_bytes})"
            )
        payload = output_path.read_bytes()
    except (DoclingResourceLimitError, DoclingWorkerError):
        raise
    except OSError as error:
        raise DoclingWorkerError("Docling worker output cannot be read") from error
    if len(payload) > max_bytes:
        raise DoclingResourceLimitError(
            f"Docling worker JSON exceeds {max_bytes} bytes"
        )
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DoclingWorkerError("Docling worker output is not valid JSON") from error


def load_psutil_module() -> Any:
    """Load the mandatory process-tree inspection dependency."""

    try:
        return importlib.import_module("psutil")
    except (ImportError, ModuleNotFoundError) as error:
        raise DoclingUnavailableError(
            "PaperTrans process supervision requires psutil; reinstall PaperTrans."
        ) from error


def terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    psutil_module: Any,
    tracked_processes: Sequence[Any] = (),
    timeout: float = 2.0,
) -> None:
    """Terminate a worker and every descendant observed by the RSS monitor."""

    psutil = psutil_module
    candidates: dict[int, Any] = {}
    for candidate in tracked_processes:
        try:
            candidates[int(candidate.pid)] = candidate
        except (AttributeError, TypeError, ValueError):
            continue
    if process.poll() is None:
        try:
            root = psutil.Process(process.pid)
            for candidate in [*root.children(recursive=True), root]:
                candidates[int(candidate.pid)] = candidate
        except psutil.NoSuchProcess:
            pass
    ordered = list(candidates.values())
    for candidate in reversed(ordered):
        with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            candidate.terminate()
    if ordered:
        _gone, alive = psutil.wait_procs(ordered, timeout=timeout)
        for candidate in alive:
            with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                candidate.kill()
        if alive:
            psutil.wait_procs(alive, timeout=1)
    if process.poll() is None:
        with suppress(OSError):
            process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=1)


def run_supervised_command(
    command: Sequence[str],
    *,
    max_memory_bytes: int,
    memory_poll_seconds: float,
    psutil_module: Any,
    terminate_tree: Callable[..., None],
    worker_label: str = "Docling",
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run one worker attempt with wall-clock and aggregate-RSS supervision."""

    timeout = float(kwargs.pop("timeout"))
    check = bool(kwargs.pop("check", False))
    psutil = psutil_module
    process = subprocess.Popen(
        command,
        # Inherit the semantic pipeline's process group so the Web supervisor's
        # outer kill reaches this worker too. Direct CLI cleanup uses psutil's
        # recursive process-tree API instead of a separate session.
        start_new_session=False,
        close_fds=True,
        shell=False,
        **kwargs,
    )
    monitor_stop = threading.Event()
    tracked: dict[int, Any] = {}
    memory_failure: list[str] = []

    def monitor_memory() -> None:
        while not monitor_stop.is_set():
            try:
                root = psutil.Process(process.pid)
                candidates = [root, *root.children(recursive=True)]
                resident_bytes = 0
                for candidate in candidates:
                    tracked[int(candidate.pid)] = candidate
                    try:
                        resident_bytes += int(candidate.memory_info().rss)
                    except psutil.NoSuchProcess:
                        continue
                if resident_bytes > max_memory_bytes:
                    memory_failure.append(
                        f"{worker_label} worker resident memory exceeds the job limit "
                        f"({resident_bytes} > {max_memory_bytes})"
                    )
                    for candidate in reversed(candidates):
                        with suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                            candidate.kill()
                    with suppress(OSError):
                        process.kill()
                    return
            except psutil.NoSuchProcess:
                return
            except (psutil.AccessDenied, PermissionError):
                # A short-lived child can exit between Process(pid) and the
                # platform-specific RSS query. Reap it before treating access
                # denial as a supervision failure; a still-running worker must
                # continue to fail closed.
                if process.poll() is not None:
                    return
                monitor_stop.wait(memory_poll_seconds)
                if process.poll() is not None:
                    return
                memory_failure.append(
                    f"PaperTrans could not inspect {worker_label} worker memory"
                )
                with suppress(OSError):
                    process.kill()
                return
            except Exception as error:
                memory_failure.append(
                    f"PaperTrans {worker_label} memory supervision failed "
                    f"({type(error).__name__})"
                )
                with suppress(OSError):
                    process.kill()
                return
            monitor_stop.wait(memory_poll_seconds)

    monitor = threading.Thread(
        target=monitor_memory,
        name="papertrans-docling-memory",
        daemon=True,
    )
    monitor.start()
    try:
        returncode = process.wait(timeout=timeout)
    except BaseException:
        monitor_stop.set()
        monitor.join(timeout=1)
        terminate_tree(process, tracked_processes=list(tracked.values()))
        raise
    finally:
        monitor_stop.set()
        monitor.join(timeout=1)
    terminate_tree(
        process,
        tracked_processes=list(tracked.values()),
        timeout=0.5,
    )
    if memory_failure:
        raise DoclingResourceLimitError(memory_failure[0])
    if check and returncode:
        raise subprocess.CalledProcessError(returncode, command)
    return subprocess.CompletedProcess(command, returncode)
