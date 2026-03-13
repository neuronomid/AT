import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Journal:
    """Appends structured decision records to a JSONL journal."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        payload = {"recorded_at": datetime.now(timezone.utc).isoformat(), **event}
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str))
            handle.write("\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                records.append(json.loads(stripped))
        return records
