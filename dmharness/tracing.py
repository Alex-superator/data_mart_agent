"""Наблюдаемость: каждое событие harness пишется в JSONL-трейс."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class Tracer:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.run_id = uuid.uuid4().hex[:8]
        self.events: list[dict] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def emit(self, kind: str, **data) -> None:
        event = {"ts": round(time.time(), 3), "run_id": self.run_id, "kind": kind, **data}
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
