import json

from memory.journal import Journal
from memory.lessons import LessonStore
from data.schemas import LessonRecord


def test_journal_writes_jsonl_record(tmp_path) -> None:
    journal_path = tmp_path / "decision_journal.jsonl"
    journal = Journal(str(journal_path))
    journal.record({"event": "decision", "action": "buy"})

    lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "decision"
    assert payload["action"] == "buy"


def test_journal_reads_back_records(tmp_path) -> None:
    journal = Journal(str(tmp_path / "decision_journal.jsonl"))
    journal.record({"event": "decision", "action": "buy"})

    records = journal.read_all()
    assert len(records) == 1
    assert records[0]["action"] == "buy"


def test_lesson_store_dedupes_by_message(tmp_path) -> None:
    store = LessonStore(str(tmp_path / "lessons.jsonl"))
    lesson = LessonRecord(
        lesson_id="lesson-1",
        category="trade_review",
        message="Tighten the spread filter.",
        confidence=0.7,
        source="review-1",
    )

    first = store.add(lesson)
    second = store.add(lesson.model_copy(update={"lesson_id": "lesson-2"}))

    assert first is True
    assert second is False
    assert len(store.read_all()) == 1
