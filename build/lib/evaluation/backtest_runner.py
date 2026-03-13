from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from brokers.alpaca.historical import AlpacaHistoricalCryptoService
from control_plane.models import BacktestJobRequest
from control_plane.policies import build_analyst_agent, ensure_default_policies
from data.schemas import BacktestReport
from evaluation.backtest import HistoricalBacktester
from evaluation.challenger import Challenger
from infra.logging import get_logger
from memory.supabase import SupabaseStore
from risk.policy import RiskPolicy


def _synthetic_selector_version() -> str:
    return datetime.now(timezone.utc).strftime("run%Y%m%d%H%M%S")


async def run_backtest_job(
    *,
    settings: Settings,
    store: SupabaseStore,
    request: BacktestJobRequest,
    requested_by: str = "dashboard",
    job_id: str | None = None,
) -> tuple[str, str, BacktestReport]:
    logger = get_logger(__name__)
    if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
        raise RuntimeError("Alpaca credentials are required for historical backtests.")

    ensure_default_policies(store, settings)
    job_id = job_id or store.create_backtest_job(request, requested_by=requested_by)
    store.update_backtest_job(job_id=job_id, status="running")

    history_service = AlpacaHistoricalCryptoService(
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    try:
        candidate_ids = [policy_id for policy_id in request.candidate_policy_version_ids if policy_id]
        if not candidate_ids:
            raise RuntimeError("Select at least one candidate strategy before running a backtest.")

        baseline_policy = store.get_policy_version(request.baseline_policy_version_id)
        if baseline_policy is None:
            raise RuntimeError("The selected baseline strategy does not exist.")

        candidate_policies = {
            policy.id: policy
            for policy in store.get_policy_versions(candidate_ids)
            if policy.id != request.baseline_policy_version_id
        }
        if not candidate_policies:
            raise RuntimeError("The selected candidates did not resolve to any stored policy versions.")

        end_at = datetime.now(timezone.utc)
        start_at = end_at - timedelta(days=request.lookback_days)
        logger.info(
            "backtest_job_fetching_bars symbol=%s timeframe=%s lookback_days=%s",
            request.symbol,
            request.timeframe,
            request.lookback_days,
        )
        bars = await history_service.fetch_bars(
            symbol=request.symbol,
            timeframe=request.timeframe,
            location=request.location,
            start=start_at,
            end=end_at,
        )
        logger.info("backtest_job_fetched_bars count=%s", len(bars))
        inserted = store.upsert_market_bars(bars)
        logger.info("backtest_job_upserted_bars inserted=%s", inserted)
        persisted_bars = store.load_market_bars(
            symbol=request.symbol,
            timeframe=request.timeframe,
            location=request.location,
            start=start_at,
            end=end_at,
            include_raw_bar=False,
        )
        logger.info("backtest_job_loaded_bars count=%s", len(persisted_bars))

        agent_config = (
            store.get_agent_config_by_id(request.agent_config_id)
            if request.agent_config_id is not None
            else store.get_agent_config(settings.agent_name)
        )
        if agent_config is None:
            raise RuntimeError("The target agent config was not found.")

        risk_policy = RiskPolicy(
            min_confidence=agent_config.min_decision_confidence,
            max_risk_fraction=Decimal(str(agent_config.max_risk_per_trade_pct)),
            max_position_notional_usd=agent_config.max_position_notional_usd,
            max_spread_bps=Decimal(str(agent_config.max_spread_bps)),
            max_trades_per_hour=agent_config.max_trades_per_hour,
            cooldown_seconds=agent_config.cooldown_seconds_after_trade,
        )
        backtester = HistoricalBacktester(
            symbol=request.symbol,
            starting_cash_usd=request.starting_cash_usd,
            risk_policy=risk_policy,
        )

        baseline_agent = build_analyst_agent(baseline_policy)
        candidate_agents = {
            policy.label: build_analyst_agent(policy)
            for policy in candidate_policies.values()
        }
        baseline_metrics, candidate_metrics, windows, candidate_trades, trade_window_indexes = backtester.walk_forward(
            bars=persisted_bars,
            candidate_policies=candidate_agents,
            baseline_policy=baseline_agent,
            train_window_days=request.train_window_days,
            test_window_days=request.test_window_days,
            step_days=request.step_days,
            warmup_bars=request.warmup_bars,
        )
        logger.info(
            "backtest_job_walk_forward_complete windows=%s baseline_score=%.2f candidate_score=%.2f candidate_trades=%s",
            len(windows),
            baseline_metrics.score,
            candidate_metrics.score,
            len(candidate_trades),
        )

        decision = Challenger(
            min_closed_trades=settings.evaluation_min_closed_trades,
            min_score_improvement=settings.evaluation_min_score_improvement,
            max_additional_drawdown_bps=settings.evaluation_max_additional_drawdown_bps,
        ).compare(baseline_metrics, candidate_metrics)

        selector_version = _synthetic_selector_version()
        selector_policy_id = store.upsert_policy_version(
            policy_name="walk_forward_best",
            version=selector_version,
            status="shadow",
            thresholds={},
            risk_params={"selector": "train_score_max"},
            strategy_config={
                "candidates": [policy.label for policy in candidate_policies.values()],
                "baseline": baseline_policy.label,
            },
            notes="Synthetic selector policy representing the best candidate in each walk-forward window.",
        )
        policy_version_ids = {
            "baseline": baseline_policy.id,
            "walk_forward_best": selector_policy_id,
            baseline_policy.label: baseline_policy.id,
        }
        policy_version_ids.update({policy.label: policy.id for policy in candidate_policies.values()})

        run_id = store.create_backtest_run(
            run_name=request.run_name,
            symbol=request.symbol,
            timeframe=request.timeframe,
            location=request.location,
            start_at=start_at,
            end_at=end_at,
            train_window_days=request.train_window_days,
            test_window_days=request.test_window_days,
            step_days=request.step_days,
            warmup_bars=request.warmup_bars,
            starting_cash_usd=float(request.starting_cash_usd),
            bars_inserted=inserted,
            total_bars=len(persisted_bars),
            baseline_policy_version_id=baseline_policy.id,
            candidate_policy_version_id=selector_policy_id,
            agent_config_id=agent_config.id,
            agent_name=agent_config.agent_name,
            backtest_job_id=job_id,
        )
        logger.info("backtest_job_created_run run_id=%s", run_id)
        window_lookup = store.insert_backtest_window_results(
            run_id=run_id,
            window_summaries=windows,
            policy_version_ids=policy_version_ids,
        )
        logger.info("backtest_job_inserted_window_results count=%s", len(window_lookup))
        store.insert_backtest_trades(
            run_id=run_id,
            trades=candidate_trades,
            policy_version_ids=policy_version_ids,
            window_lookup=window_lookup,
            window_index_by_trade=trade_window_indexes,
        )
        logger.info("backtest_job_inserted_trades count=%s", len(candidate_trades))
        store.finalize_backtest_run(
            run_id=run_id,
            status="completed",
            baseline=baseline_metrics,
            candidate=candidate_metrics,
            decision=decision,
            notes="Historical bar-based walk-forward replay stored via Supabase.",
        )
        logger.info("backtest_job_finalized_run run_id=%s decision=%s", run_id, decision.status)
        store.update_backtest_job(job_id=job_id, status="completed", run_id=run_id)

        report = BacktestReport(
            symbol=request.symbol,
            timeframe=request.timeframe,
            location=request.location,
            start_at=start_at,
            end_at=end_at,
            total_bars=len(persisted_bars),
            bars_inserted=inserted,
            baseline=baseline_metrics,
            candidate=candidate_metrics,
            decision=decision,
            windows=windows,
        )
        return job_id, run_id, report
    except Exception as exc:
        store.update_backtest_job(job_id=job_id, status="failed", error_message=str(exc))
        raise
    finally:
        await history_service.aclose()
