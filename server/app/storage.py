from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JsonMatchStorage:
    def __init__(self, history_file: Path) -> None:
        self._history_file = history_file
        self._lock = threading.Lock()
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._history_file.exists():
            self._history_file.write_text("[]", encoding="utf-8")

    def _read_all(self) -> list[dict[str, Any]]:
        try:
            content = self._history_file.read_text(encoding="utf-8").strip()
            if not content:
                return []
            payload = json.loads(content)
            if isinstance(payload, list):
                return payload
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_all(self, records: list[dict[str, Any]]) -> None:
        self._history_file.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_match(self, record: dict[str, Any]) -> None:
        with self._lock:
            records = self._read_all()
            records.append(record)
            self._write_all(records)

    def list_matches(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            records = self._read_all()

        ordered = list(reversed(records))
        if limit is not None and limit > 0:
            return ordered[:limit]
        return ordered

    def get_match(self, match_id: str) -> dict[str, Any] | None:
        with self._lock:
            records = self._read_all()
        for record in records:
            if record.get("match_id") == match_id:
                return record
        return None
