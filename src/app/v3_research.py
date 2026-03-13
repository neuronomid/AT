from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agents.strategy_advisor import StrategyAdvisor
from app.config import get_settings
from brokers.alpaca.historical import AlpacaHistoricalCryptoService
from control_plane.models import PolicyVersionRecord
from control_plane.policies import (
    DEFAULT_POLICY_DEFINITIONS,
    build_analyst_agent,
    build_hmm_v3_policy,
)
from data.schemas import BacktestReport, ReviewSummary
from evaluation.backtest import HistoricalBacktester, SimulationResult
from evaluation.challenger import Challenger
from evaluation.hmm_refinement import HMMStrategyRefiner
from evaluation.reporting import render_backtest_report_markdown, render_comparison_markdown
from infra.logging import configure_logging, get_logger
from memory.lessons import LessonStore
from risk.policy import RiskPolicy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ETH/USD V3 HMM research cycle.")
    parser.add_argument("--fetch-start", default="2025-11-21", help="Warmup fetch start date in YYYY-MM-DD.")
    parser.add_argument("--report-start", default="2025-12-11", help="Report start date in YYYY-MM-DD.")
    parser.add_argument("--report-end", default="2026-03-11", help="Report end date in YYYY-MM-DD.")
    parser.add_argument("--output-dir", default="var/research/v3", help="Directory for V3 reports and artifacts.")
    return parser.parse_args()


async def run() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
        raise RuntimeError("Alpaca credentials are required for the V3 historical replay.")
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for the V3 strategy advisor.")

    fetch_start = _parse_date(args.fetch_start)
    report_start = _parse_date(args.report_start)
    report_end = _parse_date(args.report_end)
    fetch_end = report_end + timedelta(days=1)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    history_service = AlpacaHistoricalCryptoService(
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    lesson_store = LessonStore(settings.lessons_path)
    advisor = StrategyAdvisor(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    try:
        logger.info(
            "v3_research_fetching_bars symbol=%s start=%s end=%s",
            settings.trading_symbol,
            fetch_start.isoformat(),
            fetch_end.isoformat(),
        )
        bars = await history_service.fetch_bars(
            symbol=settings.trading_symbol,
            timeframe=settings.backtest_timeframe,
            location=settings.backtest_location,
            start=fetch_start,
            end=fetch_end,
        )
        if not bars:
            raise RuntimeError("No historical bars were returned for the V3 replay window.")
    finally:
        await history_service.aclose()

    evaluation_start_index = _evaluation_start_index(bars, report_start)
    report_end_index = _report_end_index(bars, report_end)
    if report_end_index < evaluation_start_index:
        raise RuntimeError("The report end landed before the evaluation start after fetching bars.")
    bars = bars[: report_end_index + 1]

    backtester = HistoricalBacktester(
        symbol=settings.trading_symbol,
        starting_cash_usd=settings.backtest_starting_cash_usd,
        risk_policy=_build_risk_policy(settings),
    )

    v2_baseline_policy = _v2_baseline_policy()
    v3_policy = build_hmm_v3_policy(
        version="v3.0",
        notes="Initial V3 HMM regime strategy for the ETH/USD 90-day replay.",
    )
    v2_result = backtester.simulate(
        bars=bars,
        policy=build_analyst_agent(v2_baseline_policy),
        evaluation_start_index=evaluation_start_index,
    )
    v3_result = backtester.simulate(
        bars=bars,
        policy=build_analyst_agent(v3_policy),
        evaluation_start_index=evaluation_start_index,
    )
    v3_report = _build_report(
        bars=bars,
        evaluation_start_index=evaluation_start_index,
        baseline_policy=v2_baseline_policy,
        candidate_policy=v3_policy,
        baseline_result=v2_result,
        candidate_result=v3_result,
        settings=settings,
    )
    _write_report_bundle(
        output_dir=output_dir,
        stem="v3.0",
        report=v3_report,
        baseline_label=v2_baseline_policy.label,
        candidate_label=v3_policy.label,
    )

    review_summary = _load_review_summary(settings)
    v3_advice = await advisor.advise(
        review_summary=review_summary,
        backtest_report=v3_report,
        lessons=lesson_store.read_all(),
    )
    (output_dir / "v3.0_advisor.md").write_text(v3_advice.raw_response, encoding="utf-8")

    refiner = HMMStrategyRefiner()
    refinement = refiner.refine(
        base_policy=v3_policy,
        report=v3_report,
        advisor_markdown=v3_advice.raw_response,
        next_version="v3.1",
    )
    v3_1_policy = refinement.policy
    v3_1_result = backtester.simulate(
        bars=bars,
        policy=build_analyst_agent(v3_1_policy),
        evaluation_start_index=evaluation_start_index,
    )
    v3_1_report = _build_report(
        bars=bars,
        evaluation_start_index=evaluation_start_index,
        baseline_policy=v3_policy,
        candidate_policy=v3_1_policy,
        baseline_result=v3_result,
        candidate_result=v3_1_result,
        settings=settings,
    )
    _write_report_bundle(
        output_dir=output_dir,
        stem="v3.1",
        report=v3_1_report,
        baseline_label=v3_policy.label,
        candidate_label=v3_1_policy.label,
    )
    (output_dir / "v3.1_comparison.md").write_text(
        render_comparison_markdown(
            baseline_report=v3_report,
            candidate_report=v3_1_report,
            baseline_label=v3_policy.label,
            candidate_label=v3_1_policy.label,
        ),
        encoding="utf-8",
    )

    print(
        "\n".join(
            [
                f"bars={len(bars)}",
                f"evaluation_start_index={evaluation_start_index}",
                f"v3.0_score={v3_report.candidate.score:.2f}",
                f"v3.0_trades={v3_report.candidate.closed_trades}",
                f"v3.1_score={v3_1_report.candidate.score:.2f}",
                f"v3.1_trades={v3_1_report.candidate.closed_trades}",
                f"output_dir={output_dir}",
            ]
        )
    )


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _evaluation_start_index(bars, report_start: datetime) -> int:
    for index, bar in enumerate(bars):
        if bar.timestamp >= report_start:
            return index
    return max(0, len(bars) - 1)


def _report_end_index(bars, report_end: datetime) -> int:
    last_index = 0
    for index, bar in enumerate(bars):
        if bar.timestamp.date() <= report_end.date():
            last_index = index
    return last_index


def _v2_baseline_policy() -> PolicyVersionRecord:
    definition = next(definition for definition in DEFAULT_POLICY_DEFINITIONS if definition["policy_name"] == "baseline")
    return PolicyVersionRecord(
        id="baseline-v2.2",
        policy_name=str(definition["policy_name"]),
        version=str(definition["version"]),
        status=str(definition["status"]),
        thresholds=dict(definition["thresholds"]),
        risk_params={},
        strategy_config=dict(definition["strategy_config"]),
        notes=str(definition["notes"]),
    )


def _build_risk_policy(settings) -> RiskPolicy:
    return RiskPolicy(
        min_confidence=settings.min_decision_confidence,
        max_risk_fraction=Decimal(str(settings.max_risk_per_trade_pct)),
        max_position_notional_usd=settings.max_position_notional_usd,
        max_spread_bps=Decimal(str(settings.max_spread_bps)),
        max_trades_per_hour=min(settings.max_trades_per_hour, 4),
        cooldown_seconds=max(settings.cooldown_seconds_after_trade, 600),
    )


def _build_report(
    *,
    bars,
    evaluation_start_index: int,
    baseline_policy: PolicyVersionRecord,
    candidate_policy: PolicyVersionRecord,
    baseline_result: SimulationResult,
    candidate_result: SimulationResult,
    settings,
) -> BacktestReport:
    decision = Challenger(
        min_closed_trades=settings.evaluation_min_closed_trades,
        min_score_improvement=settings.evaluation_min_score_improvement,
        max_additional_drawdown_bps=settings.evaluation_max_additional_drawdown_bps,
    ).compare(baseline_result.metrics, candidate_result.metrics)
    return BacktestReport(
        symbol=settings.trading_symbol,
        timeframe=settings.backtest_timeframe,
        location=settings.backtest_location,
        start_at=bars[evaluation_start_index].timestamp,
        end_at=bars[-1].timestamp,
        total_bars=max(0, len(bars) - evaluation_start_index),
        bars_inserted=0,
        baseline=baseline_result.metrics,
        candidate=candidate_result.metrics,
        decision=decision,
        windows=[],
        trade_summary=candidate_result.trade_summary,
        regime_summary=candidate_result.regime_summary,
    )


def _write_report_bundle(
    *,
    output_dir: Path,
    stem: str,
    report: BacktestReport,
    baseline_label: str,
    candidate_label: str,
) -> None:
    (output_dir / f"{stem}_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / f"{stem}_report.md").write_text(
        render_backtest_report_markdown(
            report,
            baseline_label=baseline_label,
            candidate_label=candidate_label,
        ),
        encoding="utf-8",
    )


def _load_review_summary(settings) -> ReviewSummary:
    review_path = Path(settings.review_summary_path)
    if not review_path.exists():
        return ReviewSummary()
    return ReviewSummary.model_validate_json(review_path.read_text(encoding="utf-8"))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
