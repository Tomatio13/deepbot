from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LocalEventStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.incident_path = self.state_dir / "incidents.jsonl"
        self.dead_letter_path = self.state_dir / "dead-letter.jsonl"

    def append_incident(self, payload: dict[str, Any]) -> None:
        self._append(self.incident_path, payload)

    def append_dead_letter(self, payload: dict[str, Any]) -> None:
        self._append(self.dead_letter_path, payload)

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
