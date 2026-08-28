from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_stage(
    path: Path | None,
    name: str,
    started_at: datetime,
    ended_at: datetime,
    details: dict[str, Any] | None = None,
    status: str = "completed",
) -> None:
    if path is None:
        return
    payload: dict[str, Any]
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = {"version": 1, "stages": {}}
    payload.setdefault("stages", {})[name] = {
        "status": status,
        "startedAt": started_at.isoformat(),
        "endedAt": ended_at.isoformat(),
        "durationSeconds": round((ended_at - started_at).total_seconds(), 3),
        "details": details or {},
    }
    completed = [
        value
        for value in payload["stages"].values()
        if value.get("startedAt") and value.get("endedAt")
    ]
    if completed:
        first = min(datetime.fromisoformat(value["startedAt"]) for value in completed)
        last = max(datetime.fromisoformat(value["endedAt"]) for value in completed)
        payload["overall"] = {
            "startedAt": first.isoformat(),
            "endedAt": last.isoformat(),
            "durationSeconds": round((last - first).total_seconds(), 3),
        }
    payload["updatedAt"] = ended_at.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

