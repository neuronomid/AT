import json
from pathlib import Path

from agents.reviewer import ReviewerAgent
from app.config import get_settings
from infra.logging import configure_logging, get_logger
from memory.journal import Journal
from memory.lessons import LessonStore


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    journal = Journal(settings.journal_path)
    lesson_store = LessonStore(settings.lessons_path)
    reviewer = ReviewerAgent()

    summary = reviewer.summarize_journal(journal.read_all())
    inserted = lesson_store.add_many(summary.lessons)

    output_path = Path(settings.review_summary_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")

    logger.info(
        "review_summary_written path=%s total_records=%s trade_reviews=%s lessons_added=%s",
        output_path,
        summary.total_records,
        summary.trade_reviews,
        inserted,
    )
    print(json.dumps(summary.model_dump(mode="json"), indent=2))
