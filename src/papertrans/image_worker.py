"""Isolated, resource-bounded normalization for untrusted arXiv images."""

from __future__ import annotations

import os
import stat
import sys
import threading
from pathlib import Path

from .arxiv_html import (
    ARXIV_IMAGE_WORKER_CPU_SECONDS,
    ARXIV_IMAGE_WORKER_MEMORY_BYTES,
    ARXIV_IMAGE_WORKER_MEMORY_POLL_SECONDS,
    ARXIV_IMAGE_WORKER_TIMEOUT_SECONDS,
    ARXIV_MAX_ASSET_BYTES,
    ArxivAcquisitionLimitError,
    _normalize_passive_image_payload,
)
from .docling_contract import DoclingResourceLimitError
from .docling_resources import load_resource_module, temporary_process_resource_limits
from .docling_worker_runtime import load_psutil_module


INVALID_IMAGE_EXIT_CODE = 2
RESOURCE_LIMIT_EXIT_CODE = 3


def _start_memory_watchdog() -> tuple[threading.Event, threading.Thread]:
    """Monitor RSS from inside the worker where process inspection is reliable."""

    stop = threading.Event()
    try:
        heartbeat_fd = int(
            os.environ.pop("PAPERTRANS_IMAGE_HEARTBEAT_FD", "")
        )
        if not stat.S_ISFIFO(os.fstat(heartbeat_fd).st_mode):
            raise ValueError("image worker heartbeat must be a pipe")
        os.set_blocking(heartbeat_fd, False)
        process = load_psutil_module().Process(os.getpid())
        initial_resident_bytes = int(process.memory_info().rss)
        os.write(
            heartbeat_fd,
            f"{initial_resident_bytes}\n".encode("ascii"),
        )
        if initial_resident_bytes > ARXIV_IMAGE_WORKER_MEMORY_BYTES:
            os._exit(RESOURCE_LIMIT_EXIT_CODE)
    except BaseException:
        os._exit(RESOURCE_LIMIT_EXIT_CODE)

    def monitor() -> None:
        try:
            while not stop.wait(ARXIV_IMAGE_WORKER_MEMORY_POLL_SECONDS):
                resident_bytes = int(process.memory_info().rss)
                os.write(heartbeat_fd, f"{resident_bytes}\n".encode("ascii"))
                if resident_bytes > ARXIV_IMAGE_WORKER_MEMORY_BYTES:
                    os._exit(RESOURCE_LIMIT_EXIT_CODE)
        except BaseException:
            os._exit(RESOURCE_LIMIT_EXIT_CODE)
        finally:
            try:
                os.close(heartbeat_fd)
            except OSError:
                pass

    thread = threading.Thread(
        target=monitor,
        name="papertrans-image-memory",
        daemon=True,
    )
    thread.start()
    return stop, thread


def _read_bounded_regular_file(path: Path, *, max_bytes: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ValueError("image worker input must be a regular file")
    if info.st_size > max_bytes:
        raise ArxivAcquisitionLimitError(
            f"image worker input exceeds {max_bytes} bytes"
        )
    with path.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ArxivAcquisitionLimitError(
            f"image worker input exceeds {max_bytes} bytes"
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        return INVALID_IMAGE_EXIT_CODE
    input_path = Path(arguments[0])
    output_path = Path(arguments[1])
    watchdog_stop, watchdog = _start_memory_watchdog()
    try:
        with temporary_process_resource_limits(
            ARXIV_IMAGE_WORKER_TIMEOUT_SECONDS,
            load_resource=load_resource_module,
            max_memory_bytes=ARXIV_IMAGE_WORKER_MEMORY_BYTES,
            max_cpu_seconds=ARXIV_IMAGE_WORKER_CPU_SECONDS,
            max_output_file_bytes=ARXIV_MAX_ASSET_BYTES,
        ):
            payload = _read_bounded_regular_file(
                input_path,
                max_bytes=ARXIV_MAX_ASSET_BYTES,
            )
            normalized = _normalize_passive_image_payload(payload)
            descriptor = os.open(
                output_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(normalized)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                output_path.unlink(missing_ok=True)
                raise
    except (ArxivAcquisitionLimitError, DoclingResourceLimitError):
        output_path.unlink(missing_ok=True)
        return RESOURCE_LIMIT_EXIT_CODE
    except (OSError, RuntimeError, ValueError):
        output_path.unlink(missing_ok=True)
        return INVALID_IMAGE_EXIT_CODE
    finally:
        watchdog_stop.set()
        watchdog.join(timeout=0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
