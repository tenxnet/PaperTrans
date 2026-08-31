"""Read-only inspection of the Web PDF-import admission lock."""

from __future__ import annotations

import json
import math
import os
import stat
import sys
import time
from pathlib import Path


PDF_IMPORT_LOCK_FILENAME = ".papertrans-pdf-import.lock"
PDF_IMPORT_LOCK_SETUP_STALE_SECONDS = 2 * 60


def _read_record(record_path: Path) -> dict[str, object] | None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(record_path, flags)
        record_stat = os.fstat(descriptor)
        if not stat.S_ISREG(record_stat.st_mode) or record_stat.st_size > 4096:
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            descriptor = -1
            raw = source.read(4097)
    except (OSError, UnicodeError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw.encode("utf-8")) > 4096:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    owner = value.get("owner")
    created_at = value.get("createdAt")
    pid = value.get("pid")
    if (
        not isinstance(owner, str)
        or isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(created_at)
        or (
            pid is not None
            and (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0)
        )
    ):
        return None
    return {"owner": owner, "createdAt": created_at, "pid": pid}


def _read_lock(lock_path: Path, lock_stat: os.stat_result) -> dict[str, object] | None:
    if stat.S_ISREG(lock_stat.st_mode):
        # Compatibility with the pre-v0.2 single-file lock format.
        return _read_record(lock_path)
    if not stat.S_ISDIR(lock_stat.st_mode):
        return None

    record: dict[str, object] | None = None
    try:
        with os.scandir(lock_path) as entries:
            for entry_count, entry in enumerate(entries, start=1):
                if entry_count > 8:
                    return None
                entry_stat = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(entry_stat.st_mode) or not entry.name.endswith(".json"):
                    continue
                candidate = _read_record(lock_path / entry.name)
                if (
                    candidate is None
                    or entry.name != f"{candidate['owner']}.json"
                    or record is not None
                ):
                    return None
                record = candidate
    except OSError:
        return None
    return record


def inspect_pdf_import(output_root: Path) -> dict[str, object]:
    """Report whether a detached PDF-import process group may still be active."""

    lock_path = output_root / PDF_IMPORT_LOCK_FILENAME
    try:
        lock_stat = lock_path.lstat()
    except FileNotFoundError:
        return {
            "active": False,
            "state": "idle",
            "lockPath": str(lock_path),
            "detail": "no import lock",
        }
    except OSError as error:
        return {
            "active": True,
            "state": "unknown",
            "lockPath": str(lock_path),
            "detail": f"cannot inspect import lock: {error}",
        }

    record = _read_lock(lock_path, lock_stat)
    try:
        current_lock_stat = lock_path.lstat()
    except OSError as error:
        return {
            "active": True,
            "state": "unknown",
            "lockPath": str(lock_path),
            "detail": f"import lock changed during inspection: {error}",
        }
    if (
        current_lock_stat.st_dev != lock_stat.st_dev
        or current_lock_stat.st_ino != lock_stat.st_ino
    ):
        return {
            "active": True,
            "state": "unknown",
            "lockPath": str(lock_path),
            "detail": "import lock changed during inspection",
        }
    lock_age = max(0.0, time.time() - current_lock_stat.st_mtime)
    if record is None:
        state = "stale" if lock_age >= PDF_IMPORT_LOCK_SETUP_STALE_SECONDS else "unknown"
        return {
            "active": state != "stale",
            "state": state,
            "lockPath": str(lock_path),
            "detail": "invalid import lock",
        }

    pid = record["pid"]
    if pid is None:
        claim_age = max(0.0, time.time() - float(record["createdAt"]) / 1000)
        state = "stale" if claim_age >= PDF_IMPORT_LOCK_SETUP_STALE_SECONDS else "starting"
        return {
            "active": state != "stale",
            "state": state,
            "pid": None,
            "lockPath": str(lock_path),
            "detail": "worker PID has not been recorded" if state == "starting" else "setup lock is stale",
        }

    try:
        os.kill(pid if sys.platform == "win32" else -pid, 0)
    except ProcessLookupError:
        return {
            "active": False,
            "state": "stale",
            "pid": pid,
            "lockPath": str(lock_path),
            "detail": "worker process group has exited",
        }
    except PermissionError:
        pass
    except OSError as error:
        return {
            "active": True,
            "state": "unknown",
            "pid": pid,
            "lockPath": str(lock_path),
            "detail": f"cannot probe worker process group: {error}",
        }
    return {
        "active": True,
        "state": "running",
        "pid": pid,
        "lockPath": str(lock_path),
        "detail": "worker process group is running",
    }
