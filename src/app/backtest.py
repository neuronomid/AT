import argparse
import asyncio
from pathlib import Path

from app.config import get_settings
from control_plane.models import BacktestJobRequest
from control_plane.policies import ensure_default_policies
from data.schemas import BacktestReport
from evaluation.backtest_runner import run_backtest_job
from infra.logging import configure_logging, get_logger
from memory.supabase import SupabaseStore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a historical strategy backtest.")
    parser.add_argument("--run-name", help="Name for the stored backtest run.")
    parser.add_argument("--symbol", help="Symbol to test, for example ETH/USD.")
    parser.add_argument("--timeframe", help="Bar timeframe, for example 1Min.")
    parser.add_argument("--location", help="Data location, for example us.")
    parser.add_argument("--lookback-days", type=int, help="Historical lookback window in days.")
    parser.add_argument("--train-window-days", type=int, help="Walk-forward training window length.")
    parser.add_argument("--test-window-days", type=int, help="Walk-forward test window length.")
    parser.add_argument("--step-days", type=int, help="Walk-forward step length.")
    parser.add_argument("--warmup-bars", type=int, help="Warm-up bars before evaluation.")
    parser.add_argument("--starting-cash", type=float, help="Starting cash balance in USD.")
    parser.add_argument("--baseline-policy-id", help="Policy version id for the baseline strategy.")
    parser.add_argument(
        "--candidate-policy-id",
        action="append",
        dest="candidate_policy_ids",
        help="Policy version id for a candidate strategy. Repeat the flag for multiple strategies.",
    )
    return parser.parse_args()


async def run() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.supabase_db_dsn is None:
        raise RuntimeError("SUPABASE_DB_URL is required for DB-backed backtests.")

    store = SupabaseStore(settings.supabase_db_dsn)
    default_policy_ids = ensure_default_policies(store, settings)
    baseline_policy_id = args.baseline_policy_id or default_policy_ids["baseline"]
    candidate_policy_ids = args.candidate_policy_ids or [
        policy_id
        for name, policy_id in default_policy_ids.items()
        if name != "baseline"
    ]

    request = BacktestJobRequest(
        run_name=args.run_name or f"{settings.trading_symbol}-{settings.backtest_timeframe}-manual",
        symbol=args.symbol or settings.trading_symbol,
        timeframe=args.timeframe or settings.backtest_timeframe,
        location=args.location or settings.backtest_location,
        lookback_days=args.lookback_days or settings.backtest_lookback_days,
        train_window_days=args.train_window_days or settings.backtest_train_window_days,
        test_window_days=args.test_window_days or settings.backtest_test_window_days,
        step_days=args.step_days or settings.backtest_step_days,
        warmup_bars=args.warmup_bars or settings.backtest_warmup_bars,
        starting_cash_usd=args.starting_cash or settings.backtest_starting_cash_usd,
        baseline_policy_version_id=baseline_policy_id,
        candidate_policy_version_ids=candidate_policy_ids,
    )

    _, run_id, report = await run_backtest_job(settings=settings, store=store, request=request, requested_by="cli")
    output_path = Path(settings.backtest_report_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "backtest_report_written path=%s run_id=%s total_bars=%s windows=%s decision=%s",
        output_path,
        run_id,
        report.total_bars,
        len(report.windows),
        report.decision.status,
    )
    print(report.model_dump_json(indent=2))


def main() -> None:
    asyncio.run(run())
