import json
from pathlib import Path

from data.schemas import LessonRecord


class LessonStore:
    """Stores recurring success and failure patterns as JSONL records."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._known_messages = self._load_known_messages()

    def add(self, lesson: LessonRecord) -> bool:
        if lesson.message in self._known_messages:
            return False
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(lesson.model_dump_json())
            handle.write("\n")
        self._known_messages.add(lesson.message)
        return True

    def add_many(self, lessons: list[LessonRecord]) -> int:
        inserted = 0
        for lesson in lessons:
            if self.add(lesson):
                inserted += 1
        return inserted

    def read_all(self) -> list[dict[str, object]]:
        if not self._path.exists():
            return []
        with self._path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _load_known_messages(self) -> set[str]:
        if not self._path.exists():
            return set()
        messages: set[str] = set()
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                payload = json.loads(stripped)
                message = payload.get("message")
                if isinstance(message, str):
                    messages.add(message)
        return messages
