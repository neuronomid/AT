from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.config import get_settings
from brokers.alpaca.historical import AlpacaHistoricalCryptoService
from control_plane.models import PolicyVersionRecord
from control_plane.policies import (
    build_analyst_agent,
    build_hmm_v3_policy,
    build_inverse_hmm_v3_policy,
)
from data.schemas import BacktestReport
from evaluation.backtest import HistoricalBacktester
from evaluation.challenger import Challenger
from evaluation.hmm_refinement import HMMStrategyRefiner
from evaluation.reporting import render_backtest_report_markdown, render_comparison_markdown
from infra.logging import configure_logging, get_logger
from risk.policy import RiskPolicy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ETH/USD V3.2 inverse HMM research cycle.")
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
        raise RuntimeError("Alpaca credentials are required for the V3.2 historical replay.")

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
    try:
        logger.info(
            "v3_2_research_fetching_bars symbol=%s start=%s end=%s",
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
            raise RuntimeError("No historical bars were returned for the V3.2 replay window.")
    finally:
        await history_service.aclose()

    evaluation_start_index = _evaluation_start_index(bars, report_start)
    report_end_index = _report_end_index(bars, report_end)
    bars = bars[: report_end_index + 1]

    backtester = HistoricalBacktester(
        symbol=settings.trading_symbol,
        starting_cash_usd=settings.backtest_starting_cash_usd,
        risk_policy=_build_risk_policy(settings),
    )

    v3_1_policy = _v3_1_policy_from_artifacts(output_dir)
    v3_2_policy = _inverse_policy_from_v3_1(v3_1_policy)

    v3_1_result = backtester.simulate(
        bars=bars,
        policy=build_analyst_agent(v3_1_policy),
        evaluation_start_index=evaluation_start_index,
    )
    v3_2_result = backtester.simulate(
        bars=bars,
        policy=build_analyst_agent(v3_2_policy),
        evaluation_start_index=evaluation_start_index,
    )

    decision = Challenger(
        min_closed_trades=settings.evaluation_min_closed_trades,
        min_score_improvement=settings.evaluation_min_score_improvement,
        max_additional_drawdown_bps=settings.evaluation_max_additional_drawdown_bps,
    ).compare(v3_1_result.metrics, v3_2_result.metrics)
    report = BacktestReport(
        symbol=settings.trading_symbol,
        timeframe=settings.backtest_timeframe,
        location=settings.backtest_location,
        start_at=bars[evaluation_start_index].timestamp,
        end_at=bars[-1].timestamp,
        total_bars=max(0, len(bars) - evaluation_start_index),
        bars_inserted=0,
        baseline=v3_1_result.metrics,
        candidate=v3_2_result.metrics,
        decision=decision,
        windows=[],
        trade_summary=v3_2_result.trade_summary,
        regime_summary=v3_2_result.regime_summary,
    )

    (output_dir / "v3.2_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "v3.2_report.md").write_text(
        render_backtest_report_markdown(
            report,
            baseline_label=v3_1_policy.label,
            candidate_label=v3_2_policy.label,
        ),
        encoding="utf-8",
    )
    (output_dir / "v3.2_comparison.md").write_text(
        render_comparison_markdown(
            baseline_report=_report_from_result(
                bars=bars,
                evaluation_start_index=evaluation_start_index,
                baseline_result=v3_1_result,
                candidate_result=v3_1_result,
                settings=settings,
                baseline_policy=v3_1_policy,
                candidate_policy=v3_1_policy,
            ),
            candidate_report=report,
            baseline_label=v3_1_policy.label,
            candidate_label=v3_2_policy.label,
        ),
        encoding="utf-8",
    )

    print(
        "\n".join(
            [
                f"bars={len(bars)}",
                f"evaluation_start_index={evaluation_start_index}",
                f"v3.1_score={v3_1_result.metrics.score:.2f}",
                f"v3.1_trades={v3_1_result.metrics.closed_trades}",
                f"v3.2_score={v3_2_result.metrics.score:.2f}",
                f"v3.2_trades={v3_2_result.metrics.closed_trades}",
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


def _build_risk_policy(settings) -> RiskPolicy:
    return RiskPolicy(
        min_confidence=settings.min_decision_confidence,
        max_risk_fraction=Decimal(str(settings.max_risk_per_trade_pct)),
        max_position_notional_usd=settings.max_position_notional_usd,
        max_spread_bps=Decimal(str(settings.max_spread_bps)),
        max_trades_per_hour=min(settings.max_trades_per_hour, 4),
        cooldown_seconds=max(settings.cooldown_seconds_after_trade, 600),
    )


def _v3_1_policy_from_artifacts(output_dir: Path) -> PolicyVersionRecord:
    v3_0_report_path = output_dir / "v3.0_report.json"
    v3_0_advice_path = output_dir / "v3.0_advisor.md"
    if not v3_0_report_path.exists() or not v3_0_advice_path.exists():
        raise RuntimeError("V3.0 artifacts are required before running V3.2. Run the V3 research cycle first.")
    base_policy = build_hmm_v3_policy(
        version="v3.0",
        notes="Initial V3 HMM regime strategy for the ETH/USD 90-day replay.",
    )
    refinement = HMMStrategyRefiner().refine(
        base_policy=base_policy,
        report=BacktestReport.model_validate_json(v3_0_report_path.read_text(encoding="utf-8")),
        advisor_markdown=v3_0_advice_path.read_text(encoding="utf-8"),
        next_version="v3.1",
    )
    return refinement.policy


def _inverse_policy_from_v3_1(v3_1_policy: PolicyVersionRecord) -> PolicyVersionRecord:
    strategy_config = dict(v3_1_policy.strategy_config)
    strategy_config["strategy_family"] = "inverse_hmm_regime_v3"
    strategy_config["hmm_bear_entry_probability"] = float(
        v3_1_policy.strategy_config.get("hmm_bull_entry_probability", 0.62)
    )
    strategy_config["hmm_bear_continuation_probability"] = float(
        v3_1_policy.strategy_config.get("hmm_bull_continuation_probability", 0.58)
    )
    strategy_config["hmm_bull_exit_probability"] = float(
        v3_1_policy.strategy_config.get("hmm_bear_exit_probability", 0.52)
    )
    return build_inverse_hmm_v3_policy(
        version="v3.2",
        notes="Research-only inverse mirror of V3.1. Uses short entries only in backtests to test whether reversing the HMM decisions produces positive outcomes.",
        thresholds_overrides=dict(v3_1_policy.thresholds),
        strategy_overrides=strategy_config,
    )


def _report_from_result(
    *,
    bars,
    evaluation_start_index: int,
    baseline_result,
    candidate_result,
    settings,
    baseline_policy: PolicyVersionRecord,
    candidate_policy: PolicyVersionRecord,
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
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
