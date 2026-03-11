import asyncio
from decimal import Decimal
from itertools import count
from pathlib import Path

from agents.analyst import AnalystAgent
from agents.reviewer import ReviewerAgent
from app.config import get_settings
from brokers.alpaca.account import AlpacaAccountService
from brokers.alpaca.client import AlpacaClient
from brokers.alpaca.market_data import AlpacaMarketDataService
from brokers.alpaca.trading import AlpacaTradingService
from brokers.alpaca.trading_stream import AlpacaTradingStreamService
from data.feature_engine import FeatureEngine
from data.schemas import OrderRequest, ReviewSummary
from execution.executor import ExecutionExecutor
from execution.order_manager import OrderManager
from infra.logging import configure_logging, get_logger
from memory.journal import Journal
from memory.lessons import LessonStore
from risk.policy import RiskPolicy


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    logger = get_logger(__name__)
    logger.info("AT agent phase 4 loop starting")
    logger.info("environment=%s symbol=%s", settings.app_env, settings.trading_symbol)

    if not settings.has_alpaca_credentials:
        logger.warning(
            "Alpaca credentials are not configured yet. Copy .env.example to .env and fill in the API values."
        )
        return

    client = AlpacaClient(settings)
    account_service = AlpacaAccountService(client)
    trading_service = AlpacaTradingService(client)
    order_manager = OrderManager()
    market_data_service = AlpacaMarketDataService(
        websocket_url=settings.alpaca_crypto_data_ws_url,
        symbol=settings.trading_symbol,
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    trading_stream_service = AlpacaTradingStreamService(
        websocket_url=settings.alpaca_trading_stream_ws_url,
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    analyst = AnalystAgent()
    reviewer = ReviewerAgent()
    feature_engine = FeatureEngine()
    journal = Journal(settings.journal_path)
    lesson_store = LessonStore(settings.lessons_path)
    risk_policy = RiskPolicy(
        min_confidence=settings.min_decision_confidence,
        max_risk_fraction=Decimal(str(settings.max_risk_per_trade_pct)),
        max_position_notional_usd=settings.max_position_notional_usd,
        max_spread_bps=Decimal(str(settings.max_spread_bps)),
        max_trades_per_hour=settings.max_trades_per_hour,
        cooldown_seconds=settings.cooldown_seconds_after_trade,
    )
    executor = ExecutionExecutor(trading_service, order_manager)

    try:
        await trading_stream_service.handshake()
        logger.info("trade_updates_stream_ok stream=trade_updates")
        if settings.enable_agent_orders and not settings.enable_paper_test_order:
            logger.info(
                "execution_gate_active set ENABLE_PAPER_TEST_ORDER=true alongside ENABLE_AGENT_ORDERS=true to allow paper orders."
            )

        for iteration in _iteration_sequence(settings.decision_loop_iterations):
            if iteration > 1:
                await asyncio.sleep(settings.decision_interval_seconds)

            account_snapshot = await account_service.fetch_account_snapshot(settings.trading_symbol)
            market_snapshot = await _read_market_snapshot_with_retry(market_data_service, logger)
            features = feature_engine.build_features(market_snapshot)
            decision = analyst.analyze(market_snapshot, account_snapshot, features)
            risk_decision = risk_policy.evaluate(
                decision=decision,
                account_snapshot=account_snapshot,
                market_snapshot=market_snapshot,
                order_manager=order_manager,
                trades_this_hour=0,
            )

            logger.info(
                "decision_loop iteration=%s action=%s confidence=%.2f approved=%s reason=%s",
                iteration,
                decision.action,
                decision.confidence,
                risk_decision.approved,
                risk_decision.reason,
            )
            logger.info("feature_snapshot iteration=%s %s", iteration, features)
            journal.record(
                {
                    "record_type": "decision",
                    "iteration": iteration,
                    "market_snapshot": market_snapshot.model_dump(mode="json"),
                    "account_snapshot": account_snapshot.model_dump(mode="json"),
                    "features": features,
                    "decision": decision.model_dump(mode="json"),
                    "risk_decision": risk_decision.model_dump(mode="json"),
                }
            )

            if not (settings.enable_agent_orders and settings.enable_paper_test_order):
                continue

            await _maybe_execute_decision(
                settings=settings,
                logger=logger,
                decision=decision,
                account_snapshot=account_snapshot,
                market_snapshot=market_snapshot,
                features=features,
                risk_allowed_notional=risk_decision.allowed_notional_usd,
                risk_approved=risk_decision.approved,
                executor=executor,
                trading_stream_service=trading_stream_service,
                order_manager=order_manager,
                account_service=account_service,
                reviewer=reviewer,
                journal=journal,
                lesson_store=lesson_store,
            )
    finally:
        summary = reviewer.summarize_journal(journal.read_all())
        inserted = lesson_store.add_many(summary.lessons)
        _write_review_summary(settings.review_summary_path, summary)
        logger.info(
            "review_summary_ready total_records=%s trade_reviews=%s lessons_added=%s",
            summary.total_records,
            summary.trade_reviews,
            inserted,
        )
        await client.aclose()


async def _read_market_snapshot_with_retry(
    market_data_service: AlpacaMarketDataService,
    logger,
):
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return await market_data_service.read_one(timeout_seconds=20.0)
        except TimeoutError as exc:
            last_error = exc
            logger.warning("market_stream_timeout attempt=%s/3", attempt)
    raise last_error or TimeoutError("Unable to read a market snapshot from Alpaca.")


async def _maybe_execute_decision(
    *,
    settings,
    logger,
    decision,
    account_snapshot,
    market_snapshot,
    features,
    risk_allowed_notional,
    risk_approved,
    executor,
    trading_stream_service,
    order_manager,
    account_service,
    reviewer,
    journal,
    lesson_store,
) -> None:
    if not risk_approved:
        logger.info("agent_order_skipped reason=risk_rejected")
        return

    if decision.action == "buy":
        order_notional = min(risk_allowed_notional, settings.paper_test_order_notional_usd)
        if order_notional <= 0:
            logger.info("agent_order_skipped reason=zero_notional")
            return
        request = OrderRequest(
            symbol=settings.trading_symbol,
            side="buy",
            type="market",
            time_in_force="gtc",
            notional=order_notional,
        )
    elif decision.action == "exit" and account_snapshot.open_position_qty > 0:
        request = OrderRequest(
            symbol=settings.trading_symbol,
            side="sell",
            type="market",
            time_in_force="gtc",
            qty=account_snapshot.open_position_qty,
        )
    else:
        logger.info("agent_order_skipped reason=decision_not_executable action=%s", decision.action)
        return

    order = await executor.place(request)
    logger.info(
        "agent_order_submitted order_id=%s side=%s notional=%s qty=%s status=%s",
        order.id,
        order.side,
        order.notional,
        order.qty,
        order.status,
    )
    try:
        update = await trading_stream_service.read_order_update(order.id, timeout_seconds=20)
    except TimeoutError:
        logger.warning("agent_order_update_timeout order_id=%s", order.id)
        update = None

    after_account_snapshot = await account_service.fetch_account_snapshot(settings.trading_symbol)
    review = reviewer.review_execution(
        decision=decision,
        market_snapshot=market_snapshot,
        before_account=account_snapshot,
        after_account=after_account_snapshot,
        order=order,
        update=update,
        spread_bps=features.get("spread_bps"),
    )
    journal.record(
        {
            "record_type": "trade_review",
            "order": order.model_dump(mode="json"),
            "trade_update": update.model_dump(mode="json") if update is not None else None,
            "review": review.model_dump(mode="json"),
        }
    )
    lesson_store.add_many(reviewer.lessons_from_review(review))
    logger.info("trade_review outcome=%s summary=%s", review.outcome, review.summary)

    if update is not None:
        order_manager.apply_update(update)
        logger.info(
            "agent_order_update order_id=%s event=%s status=%s filled_qty=%s filled_avg_price=%s",
            update.order.id,
            update.event,
            update.order.status,
            update.order.filled_qty,
            update.order.filled_avg_price,
        )


def _iteration_sequence(decision_loop_iterations: int):
    if decision_loop_iterations == 0:
        return count(1)
    return range(1, decision_loop_iterations + 1)


def _write_review_summary(path: str, summary: ReviewSummary) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
