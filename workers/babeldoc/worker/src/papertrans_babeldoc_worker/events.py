from __future__ import annotations

import json
import sys
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import TextIO

from .constants import PROTOCOL_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventEmitter:
    def __init__(self, run_id: str, stream: TextIO | None = None):
        self.run_id = run_id
        self.stream = stream if stream is not None else sys.stdout
        self.sequence = 0

    def emit(self, event_type: str, **fields: Any) -> None:
        allowed = {"started", "stage", "progress", "warning", "artifact", "completed", "failed"}
        if event_type not in allowed:
            raise ValueError("unsupported event type")
        self.sequence += 1
        event = {
            "schemaVersion": PROTOCOL_VERSION,
            "runId": self.run_id,
            "sequence": self.sequence,
            "time": _now(),
            "type": event_type,
            **fields,
        }
        self.stream.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        self.stream.flush()
