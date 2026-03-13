from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agents.research_reviewer import ResearchReviewAdvisor
from app.config import get_settings
from brokers.alpaca.historical import AlpacaHistoricalCryptoService
from control_plane.models import PolicyVersionRecord
from control_plane.policies import DEFAULT_POLICY_DEFINITIONS, build_analyst_agent
from data.schemas import BacktestReport, HistoricalBar
from evaluation.backtest import HistoricalBacktester, SimulationResult
from evaluation.challenger import Challenger
from evaluation.reporting import render_backtest_report_markdown
from infra.logging import configure_logging, get_logger
from memory.supabase import SupabaseStore
from research import (
    DiscoveryResearcher,
    render_discovered_strategy_markdown,
    render_discovery_report_markdown,
    render_inverse_appendix_markdown,
)
from risk.policy import RiskPolicy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the reverse discovery-first ETH/USD strategy cycle.")
    parser.add_argument("--research-start", default="2025-12-11", help="Research window start date in YYYY-MM-DD.")
    parser.add_argument("--research-end", default="2026-03-11", help="Research window end date in YYYY-MM-DD.")
    parser.add_argument("--validation-start", default="2025-09-11", help="Validation start date in YYYY-MM-DD.")
    parser.add_argument(
        "--output-dir",
        default="var/research/discovery",
        help="Root directory for discovery research artifacts.",
    )
    parser.add_argument(
        "--min-closed-trades",
        type=int,
        default=18,
        help="Minimum closed trades required for the six-month validation gate.",
    )
    parser.add_argument(
        "--disable-inverse-appendix",
        action="store_true",
        help="Skip the inverse research appendix.",
    )
    return parser.parse_args()


async def run() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
        raise RuntimeError("Alpaca credentials are required for the discovery cycle.")
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for the discovery review.")

    research_start = _parse_date(args.research_start)
    research_end = _parse_date(args.research_end)
    validation_start = _parse_date(args.validation_start)
    window_label = f"{research_start:%Y%m%d}-{research_end:%Y%m%d}"
    artifact_dir = Path(args.output_dir) / window_label
    artifact_dir.mkdir(parents=True, exist_ok=True)

    researcher = DiscoveryResearcher(
        symbol=settings.trading_symbol,
        timeframe=settings.backtest_timeframe,
        estimated_fee_bps=1.25,
        base_slippage_bps=0.9,
    )
    fetch_start = researcher.warmup_start(validation_start)
    fetch_end = research_end + timedelta(days=1)

    store = SupabaseStore(settings.supabase_db_dsn) if settings.supabase_db_dsn is not None else None
    history_service = AlpacaHistoricalCryptoService(
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    try:
        logger.info(
            "discovery_cycle_fetching_bars symbol=%s start=%s end=%s",
            settings.trading_symbol,
            fetch_start.isoformat(),
            fetch_end.isoformat(),
        )
        bars, bars_inserted = await _load_or_fetch_bars(
            history_service=history_service,
            store=store,
            symbol=settings.trading_symbol,
            timeframe=settings.backtest_timeframe,
            location=settings.backtest_location,
            start=fetch_start,
            end=fetch_end,
        )
    finally:
        await history_service.aclose()

    if not bars:
        raise RuntimeError("No historical bars were returned for the discovery cycle.")

    research_frame, discovery_report_dataset = researcher.build_research_frame(
        bars=bars,
        start_at=research_start,
        end_at=research_end,
    )
    discovery_report = researcher.discover(
        frame=research_frame,
        dataset=discovery_report_dataset,
        version=_discovery_version(research_start, research_end),
        include_inverse=not args.disable_inverse_appendix,
    )
    if discovery_report.candidate_strategy is None or discovery_report.selected_pattern is None:
        raise RuntimeError("Discovery did not produce a primary candidate strategy.")

    candidate_policy = _policy_from_strategy(discovery_report.candidate_strategy)
    baseline_policy = _v2_baseline_policy()
    backtester = HistoricalBacktester(
        symbol=settings.trading_symbol,
        starting_cash_usd=settings.backtest_starting_cash_usd,
        risk_policy=_build_risk_policy(settings),
    )

    research_bars = _bars_for_window(bars=bars, start=researcher.warmup_start(research_start), end=research_end)
    backtest_3m = _run_window_backtest(
        bars=research_bars,
        evaluation_start=research_start,
        evaluation_end=research_end,
        baseline_policy=baseline_policy,
        candidate_policy=candidate_policy,
        backtester=backtester,
        settings=settings,
        bars_inserted=bars_inserted,
    )

    gate_passed, gate_status = passes_three_month_gate(
        report=backtest_3m,
        min_closed_trades=args.min_closed_trades,
    )

    backtest_6m: BacktestReport | None = None
    if gate_passed:
        validation_bars = _bars_for_window(bars=bars, start=researcher.warmup_start(validation_start), end=research_end)
        backtest_6m = _run_window_backtest(
            bars=validation_bars,
            evaluation_start=validation_start,
            evaluation_end=research_end,
            baseline_policy=baseline_policy,
            candidate_policy=candidate_policy,
            backtester=backtester,
            settings=settings,
            bars_inserted=bars_inserted,
        )

    advisor = ResearchReviewAdvisor(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    review = await advisor.advise(
        discovery_report=discovery_report,
        backtest_3m=backtest_3m,
        backtest_6m=backtest_6m,
        inverse_appendix=discovery_report.inverse_appendix,
    )

    _write_json(artifact_dir / "research_report.json", discovery_report.model_dump(mode="json"))
    _write_text(artifact_dir / "research_report.md", render_discovery_report_markdown(discovery_report))
    _write_json(artifact_dir / "candidate_strategy.json", discovery_report.candidate_strategy.model_dump(mode="json"))
    _write_text(
        artifact_dir / "candidate_strategy.md",
        render_discovered_strategy_markdown(discovery_report.candidate_strategy),
    )
    _write_json(artifact_dir / "backtest_3m.json", backtest_3m.model_dump(mode="json"))
    _write_text(
        artifact_dir / "backtest_3m.md",
        render_backtest_report_markdown(
            backtest_3m,
            baseline_label=baseline_policy.label,
            candidate_label=candidate_policy.label,
        ),
    )
    if discovery_report.inverse_appendix is not None:
        _write_json(artifact_dir / "inverse_appendix.json", discovery_report.inverse_appendix.model_dump(mode="json"))
        _write_text(
            artifact_dir / "inverse_appendix.md",
            render_inverse_appendix_markdown(discovery_report.inverse_appendix),
        )
    if backtest_6m is not None:
        _write_json(artifact_dir / "backtest_6m.json", backtest_6m.model_dump(mode="json"))
        _write_text(
            artifact_dir / "backtest_6m.md",
            render_backtest_report_markdown(
                backtest_6m,
                baseline_label=baseline_policy.label,
                candidate_label=candidate_policy.label,
            ),
        )
    _write_json(artifact_dir / "llm_review.json", review.model_dump(mode="json"))
    _write_text(artifact_dir / "llm_review.md", review.raw_response)

    print(
        "\n".join(
            [
                f"artifact_dir={artifact_dir}",
                f"three_month_score={backtest_3m.candidate.score:.2f}",
                f"three_month_realized_pnl_bps={backtest_3m.candidate.realized_pnl_bps:.2f}",
                f"three_month_closed_trades={backtest_3m.candidate.closed_trades}",
                f"gate_status={gate_status}",
                (
                    f"six_month_score={backtest_6m.candidate.score:.2f}"
                    if backtest_6m is not None
                    else "six_month_score=not_run"
                ),
            ]
        )
    )


def passes_three_month_gate(report: BacktestReport, *, min_closed_trades: int) -> tuple[bool, str]:
    candidate = report.candidate
    if candidate.realized_pnl_bps <= 0:
        return False, "three_month_failed_gate"
    if candidate.average_trade_bps <= 0:
        return False, "three_month_failed_gate"
    if candidate.closed_trades < min_closed_trades:
        return False, "three_month_failed_gate"
    return True, "six_month_validation_ready"


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


async def _load_or_fetch_bars(
    *,
    history_service: AlpacaHistoricalCryptoService,
    store: SupabaseStore | None,
    symbol: str,
    timeframe: str,
    location: str,
    start: datetime,
    end: datetime,
) -> tuple[list[HistoricalBar], int]:
    if store is not None:
        cached = store.load_market_bars(
            symbol=symbol,
            timeframe=timeframe,
            location=location,
            start=start,
            end=end,
            include_raw_bar=False,
        )
        if _bars_cover_window(cached, start=start, end=end):
            return cached, 0

    fetched = await history_service.fetch_bars(
        symbol=symbol,
        timeframe=timeframe,
        location=location,
        start=start,
        end=end,
    )
    inserted = 0
    if store is not None:
        inserted = store.upsert_market_bars(fetched)
        loaded = store.load_market_bars(
            symbol=symbol,
            timeframe=timeframe,
            location=location,
            start=start,
            end=end,
            include_raw_bar=False,
        )
        if loaded:
            return loaded, inserted
    return fetched, inserted


def _bars_cover_window(bars: list[HistoricalBar], *, start: datetime, end: datetime) -> bool:
    if not bars:
        return False
    return bars[0].timestamp <= start and bars[-1].timestamp >= end - timedelta(minutes=1)


def _bars_for_window(*, bars: list[HistoricalBar], start: datetime, end: datetime) -> list[HistoricalBar]:
    return [
        bar
        for bar in bars
        if bar.timestamp >= start and bar.timestamp.date() <= end.date()
    ]


def _run_window_backtest(
    *,
    bars: list[HistoricalBar],
    evaluation_start: datetime,
    evaluation_end: datetime,
    baseline_policy: PolicyVersionRecord,
    candidate_policy: PolicyVersionRecord,
    backtester: HistoricalBacktester,
    settings,
    bars_inserted: int,
) -> BacktestReport:
    baseline_result = backtester.simulate(
        bars=bars,
        policy=build_analyst_agent(baseline_policy),
        evaluation_start_index=_evaluation_start_index(bars, evaluation_start),
    )
    candidate_result = backtester.simulate(
        bars=bars,
        policy=build_analyst_agent(candidate_policy),
        evaluation_start_index=_evaluation_start_index(bars, evaluation_start),
    )
    return _build_backtest_report(
        bars=bars,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        baseline_policy=baseline_policy,
        candidate_policy=candidate_policy,
        baseline_result=baseline_result,
        candidate_result=candidate_result,
        settings=settings,
        bars_inserted=bars_inserted,
    )


def _build_backtest_report(
    *,
    bars: list[HistoricalBar],
    evaluation_start: datetime,
    evaluation_end: datetime,
    baseline_policy: PolicyVersionRecord,
    candidate_policy: PolicyVersionRecord,
    baseline_result: SimulationResult,
    candidate_result: SimulationResult,
    settings,
    bars_inserted: int,
) -> BacktestReport:
    start_index = _evaluation_start_index(bars, evaluation_start)
    end_index = _report_end_index(bars, evaluation_end)
    decision = Challenger(
        min_closed_trades=settings.evaluation_min_closed_trades,
        min_score_improvement=settings.evaluation_min_score_improvement,
        max_additional_drawdown_bps=settings.evaluation_max_additional_drawdown_bps,
    ).compare(baseline_result.metrics, candidate_result.metrics)
    return BacktestReport(
        symbol=settings.trading_symbol,
        timeframe=settings.backtest_timeframe,
        location=settings.backtest_location,
        start_at=bars[start_index].timestamp,
        end_at=bars[end_index].timestamp,
        total_bars=max(0, end_index - start_index + 1),
        bars_inserted=bars_inserted,
        baseline=baseline_result.metrics,
        candidate=candidate_result.metrics,
        decision=decision,
        windows=[],
        trade_summary=candidate_result.trade_summary,
        regime_summary=candidate_result.regime_summary,
    )


def _evaluation_start_index(bars: list[HistoricalBar], report_start: datetime) -> int:
    for index, bar in enumerate(bars):
        if bar.timestamp >= report_start:
            return index
    return max(0, len(bars) - 1)


def _report_end_index(bars: list[HistoricalBar], report_end: datetime) -> int:
    last_index = 0
    for index, bar in enumerate(bars):
        if bar.timestamp.date() <= report_end.date():
            last_index = index
    return last_index


def _policy_from_strategy(strategy) -> PolicyVersionRecord:
    return PolicyVersionRecord(
        id=f"{strategy.policy_name}-{strategy.version}",
        policy_name=strategy.policy_name,
        version=strategy.version,
        status="candidate",
        thresholds=dict(strategy.thresholds),
        risk_params={},
        strategy_config=dict(strategy.strategy_config),
        notes=strategy.notes,
    )


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


def _discovery_version(start_at: datetime, end_at: datetime) -> str:
    return f"discovery-{start_at:%Y%m%d}-{end_at:%Y%m%d}"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        __import__("json").dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
