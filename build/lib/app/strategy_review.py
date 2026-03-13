import asyncio
from pathlib import Path

from agents.strategy_advisor import StrategyAdvisor
from app.config import get_settings
from data.schemas import BacktestReport, ReviewSummary
from infra.logging import configure_logging, get_logger
from memory.lessons import LessonStore


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for the strategy advisor.")

    review_path = Path(settings.review_summary_path)
    backtest_path = Path(settings.backtest_report_path)
    if not review_path.exists():
        raise RuntimeError(f"Review summary file was not found: {review_path}")
    if not backtest_path.exists():
        raise RuntimeError(f"Backtest report file was not found: {backtest_path}")

    review_summary = ReviewSummary.model_validate_json(review_path.read_text(encoding="utf-8"))
    backtest_report = BacktestReport.model_validate_json(backtest_path.read_text(encoding="utf-8"))
    lessons = LessonStore(settings.lessons_path).read_all()

    advisor = StrategyAdvisor(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    advice = await advisor.advise(
        review_summary=review_summary,
        backtest_report=backtest_report,
        lessons=lessons,
    )

    output_path = Path(settings.strategy_advice_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(advice.raw_response, encoding="utf-8")

    logger.info(
        "strategy_advice_written path=%s model=%s recommendations=%s",
        output_path,
        advice.model,
        len(advice.recommendations),
    )
    print(advice.raw_response)


def main() -> None:
    asyncio.run(run())
