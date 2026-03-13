import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agents.strategy_advisor import StrategyAdvisor
from app.config import get_settings
from brokers.alpaca.historical import AlpacaHistoricalCryptoService
from control_plane.policies import build_analyst_agent, ensure_default_policies
from data.schemas import BacktestReport, ReviewSummary
from evaluation.backtest import HistoricalBacktester
from evaluation.challenger import Challenger
from evaluation.refinement import PolicyRefiner
from infra.logging import configure_logging, get_logger
from memory.lessons import LessonStore
from memory.supabase import SupabaseStore
from risk.policy import RiskPolicy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run iterative strategy refinement for ETH/USD.")
    parser.add_argument("--base-version", default="v2.2", help="Base strategy version family to refine.")
    parser.add_argument("--max-iterations", type=int, default=4, help="Maximum 90-day refinement iterations.")
    parser.add_argument("--three-month-days", type=int, default=90, help="Primary lookback in days.")
    parser.add_argument("--six-month-days", type=int, default=180, help="Validation lookback in days.")
    parser.add_argument("--min-closed-trades", type=int, default=18, help="Minimum acceptable closed trades over 90 days.")
    parser.add_argument("--three-month-train-days", type=int, default=45, help="Walk-forward train window for 90-day runs.")
    parser.add_argument("--three-month-test-days", type=int, default=15, help="Walk-forward test window for 90-day runs.")
    parser.add_argument("--three-month-step-days", type=int, default=15, help="Walk-forward step size for 90-day runs.")
    parser.add_argument("--six-month-train-days", type=int, default=90, help="Walk-forward train window for 180-day runs.")
    parser.add_argument("--six-month-test-days", type=int, default=30, help="Walk-forward test window for 180-day runs.")
    parser.add_argument("--six-month-step-days", type=int, default=30, help="Walk-forward step size for 180-day runs.")
    parser.add_argument("--warmup-bars", type=int, default=300, help="Warm-up bars for walk-forward tests.")
    return parser.parse_args()


async def run() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.supabase_db_dsn is None:
        raise RuntimeError("SUPABASE_DB_URL is required for DB-backed refinement runs.")
    if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
        raise RuntimeError("Alpaca credentials are required for historical backtests.")
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for strategy analysis.")

    artifact_root = Path("var/strategy_cycles") / args.base_version
    artifact_root.mkdir(parents=True, exist_ok=True)

    store = SupabaseStore(settings.supabase_db_dsn)
    default_policy_ids = ensure_default_policies(store, settings)
    current_policy_ids = {
        "baseline": default_policy_ids["baseline"],
        "conservative": default_policy_ids["conservative"],
        "aggressive": default_policy_ids["aggressive"],
    }

    advisor = StrategyAdvisor(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    lesson_store = LessonStore(settings.lessons_path)
    refiner = PolicyRefiner(min_closed_trades_90d=args.min_closed_trades)
    history_service = AlpacaHistoricalCryptoService(
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    bar_cache: dict[int, list] = {}

    final_summary: dict[str, object] = {}
    try:
        for iteration in range(1, args.max_iterations + 1):
            iter_dir = artifact_root / f"iter{iteration:02d}"
            iter_dir.mkdir(parents=True, exist_ok=True)

            report, run_id = await _run_backtest(
                settings=settings,
                store=store,
                history_service=history_service,
                bar_cache=bar_cache,
                policy_ids=current_policy_ids,
                run_name=f"{args.base_version}-iter{iteration:02d}-90d",
                lookback_days=args.three_month_days,
                train_window_days=args.three_month_train_days,
                test_window_days=args.three_month_test_days,
                step_days=args.three_month_step_days,
                warmup_bars=args.warmup_bars,
            )
            _write_report(iter_dir / "backtest_90d.json", report)

            advice = await advisor.advise(
                review_summary=_load_review_summary(settings),
                backtest_report=report,
                lessons=lesson_store.read_all(),
            )
            (iter_dir / "advisor_90d.md").write_text(advice.raw_response, encoding="utf-8")

            accepted, acceptance_reason = _meets_acceptance(report, min_closed_trades=args.min_closed_trades)
            final_summary = {
                "iteration": iteration,
                "three_month_run_id": run_id,
                "three_month_candidate_score": report.candidate.score,
                "three_month_realized_pnl_bps": report.candidate.realized_pnl_bps,
                "three_month_average_trade_bps": report.candidate.average_trade_bps,
                "three_month_closed_trades": report.candidate.closed_trades,
                "accepted": accepted,
                "acceptance_reason": acceptance_reason,
            }
            (iter_dir / "iteration_summary.txt").write_text(
                "\n".join(f"{key}={value}" for key, value in final_summary.items()),
                encoding="utf-8",
            )

            logger.info(
                "strategy_cycle iteration=%s candidate_score=%.2f realized_pnl_bps=%.2f closed_trades=%s accepted=%s",
                iteration,
                report.candidate.score,
                report.candidate.realized_pnl_bps,
                report.candidate.closed_trades,
                accepted,
            )

            if accepted:
                validation_report, validation_run_id = await _run_backtest(
                    settings=settings,
                    store=store,
                    history_service=history_service,
                    bar_cache=bar_cache,
                    policy_ids=current_policy_ids,
                    run_name=f"{args.base_version}-iter{iteration:02d}-180d",
                    lookback_days=args.six_month_days,
                    train_window_days=args.six_month_train_days,
                    test_window_days=args.six_month_test_days,
                    step_days=args.six_month_step_days,
                    warmup_bars=args.warmup_bars,
                )
                _write_report(iter_dir / "backtest_180d.json", validation_report)
                validation_advice = await advisor.advise(
                    review_summary=_load_review_summary(settings),
                    backtest_report=validation_report,
                    lessons=lesson_store.read_all(),
                )
                (iter_dir / "advisor_180d.md").write_text(validation_advice.raw_response, encoding="utf-8")
                final_summary.update(
                    {
                        "six_month_run_id": validation_run_id,
                        "six_month_candidate_score": validation_report.candidate.score,
                        "six_month_realized_pnl_bps": validation_report.candidate.realized_pnl_bps,
                        "six_month_average_trade_bps": validation_report.candidate.average_trade_bps,
                        "six_month_closed_trades": validation_report.candidate.closed_trades,
                    }
                )
                break

            if iteration == args.max_iterations:
                break

            next_version = f"{args.base_version}-r{iteration}"
            current_policies = {
                name: store.get_policy_version(policy_id)
                for name, policy_id in current_policy_ids.items()
            }
            if any(policy is None for policy in current_policies.values()):
                raise RuntimeError("One or more strategy policies could not be loaded for refinement.")

            refined = refiner.refine(
                policies={name: policy for name, policy in current_policies.items() if policy is not None},
                report=report,
                advisor_markdown=advice.raw_response,
                next_version=next_version,
            )
            refined_dir = iter_dir / "refinement"
            refined_dir.mkdir(parents=True, exist_ok=True)
            current_policy_ids = {}
            for result in refined:
                policy_id = store.upsert_policy_version(
                    policy_name=result.policy_name,
                    version=result.version,
                    status=result.status,
                    thresholds=result.thresholds,
                    risk_params=result.risk_params,
                    strategy_config=result.strategy_config,
                    notes=result.notes,
                )
                current_policy_ids[result.policy_name] = policy_id
                (refined_dir / f"{result.policy_name}.txt").write_text(
                    "\n".join(
                        [
                            f"policy_name={result.policy_name}",
                            f"version={result.version}",
                            f"status={result.status}",
                            f"thresholds={result.thresholds}",
                            f"strategy_config={result.strategy_config}",
                            f"notes={result.notes}",
                        ]
                    ),
                    encoding="utf-8",
                )
    finally:
        await history_service.aclose()

    summary_path = artifact_root / "final_summary.txt"
    summary_path.write_text(
        "\n".join(f"{key}={value}" for key, value in final_summary.items()),
        encoding="utf-8",
    )
    print(summary_path.read_text(encoding="utf-8"))


async def _run_backtest(
    *,
    settings,
    store: SupabaseStore,
    history_service: AlpacaHistoricalCryptoService,
    bar_cache: dict[int, list],
    policy_ids: dict[str, str],
    run_name: str,
    lookback_days: int,
    train_window_days: int,
    test_window_days: int,
    step_days: int,
    warmup_bars: int,
) -> tuple[BacktestReport, str]:
    logger = get_logger(__name__)
    baseline_policy = store.get_policy_version(policy_ids["baseline"])
    candidate_policies = {
        name: store.get_policy_version(policy_id)
        for name, policy_id in policy_ids.items()
        if name != "baseline"
    }
    if baseline_policy is None or any(policy is None for policy in candidate_policies.values()):
        raise RuntimeError("One or more policy versions could not be loaded for in-memory backtesting.")

    if lookback_days not in bar_cache:
        end_at = datetime.now(timezone.utc)
        start_at = end_at - timedelta(days=lookback_days)
        logger.info("strategy_cycle_fetching_bars lookback_days=%s", lookback_days)
        bar_cache[lookback_days] = await history_service.fetch_bars(
            symbol=settings.trading_symbol,
            timeframe=settings.backtest_timeframe,
            location=settings.backtest_location,
            start=start_at,
            end=end_at,
        )
        logger.info("strategy_cycle_fetched_bars lookback_days=%s count=%s", lookback_days, len(bar_cache[lookback_days]))

    bars = bar_cache[lookback_days]
    end_at = bars[-1].timestamp
    start_at = bars[0].timestamp
    risk_policy = RiskPolicy(
        min_confidence=settings.min_decision_confidence,
        max_risk_fraction=Decimal(str(settings.max_risk_per_trade_pct)),
        max_position_notional_usd=settings.max_position_notional_usd,
        max_spread_bps=Decimal(str(settings.max_spread_bps)),
        max_trades_per_hour=min(settings.max_trades_per_hour, 4),
        cooldown_seconds=max(settings.cooldown_seconds_after_trade, 600),
    )
    backtester = HistoricalBacktester(
        symbol=settings.trading_symbol,
        starting_cash_usd=settings.backtest_starting_cash_usd,
        risk_policy=risk_policy,
    )
    baseline_agent = build_analyst_agent(baseline_policy)
    candidate_agents = {
        policy.label: build_analyst_agent(policy)
        for policy in candidate_policies.values()
        if policy is not None
    }
    baseline_result = backtester.simulate(
        bars=bars,
        policy=baseline_agent,
        evaluation_start_index=min(warmup_bars, max(0, len(bars) - 1)),
    )
    candidate_results = [
        backtester.simulate(
            bars=bars,
            policy=agent,
            evaluation_start_index=min(warmup_bars, max(0, len(bars) - 1)),
        )
        for agent in candidate_agents.values()
    ]
    if not candidate_results:
        raise RuntimeError("No candidate policies were available for backtesting.")
    selected_candidate = max(candidate_results, key=lambda result: result.metrics.score)
    baseline_metrics = baseline_result.metrics
    candidate_metrics = selected_candidate.metrics
    decision = Challenger(
        min_closed_trades=settings.evaluation_min_closed_trades,
        min_score_improvement=settings.evaluation_min_score_improvement,
        max_additional_drawdown_bps=settings.evaluation_max_additional_drawdown_bps,
    ).compare(baseline_metrics, candidate_metrics)
    report = BacktestReport(
        symbol=settings.trading_symbol,
        timeframe=settings.backtest_timeframe,
        location=settings.backtest_location,
        start_at=start_at,
        end_at=end_at,
        total_bars=len(bars),
        bars_inserted=0,
        baseline=baseline_metrics,
        candidate=candidate_metrics,
        decision=decision,
        windows=[],
    )
    run_id = f"{run_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    logger.info(
        "strategy_cycle_backtest_complete run_name=%s candidate_score=%.2f candidate_trades=%s",
        run_name,
        report.candidate.score,
        report.candidate.closed_trades,
    )
    return report, run_id


def _load_review_summary(settings) -> ReviewSummary:
    review_path = Path(settings.review_summary_path)
    if not review_path.exists():
        return ReviewSummary()
    return ReviewSummary.model_validate_json(review_path.read_text(encoding="utf-8"))


def _write_report(path: Path, report: BacktestReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def _meets_acceptance(report: BacktestReport, *, min_closed_trades: int) -> tuple[bool, str]:
    candidate = report.candidate
    if candidate.closed_trades < min_closed_trades:
        return False, "candidate_trade_count_below_floor"
    if candidate.realized_pnl_bps <= 0:
        return False, "candidate_realized_pnl_not_positive"
    if candidate.average_trade_bps <= 0:
        return False, "candidate_average_trade_not_positive"
    if candidate.score <= report.baseline.score:
        return False, "candidate_score_not_above_baseline"
    return True, "candidate_positive_and_active"


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
