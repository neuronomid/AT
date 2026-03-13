import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count
from pathlib import Path
from uuid import uuid4

from agents.analyst import AnalystAgent
from agents.reviewer import ReviewerAgent
from app.config import Settings, get_settings
from brokers.alpaca.account import AlpacaAccountService
from brokers.alpaca.client import AlpacaClient
from brokers.alpaca.market_data import AlpacaMarketDataService
from brokers.alpaca.trading import AlpacaTradingService
from brokers.alpaca.trading_stream import AlpacaTradingStreamService
from control_plane.models import AgentConfigRecord, AgentHeartbeatRecord
from control_plane.policies import build_analyst_agent, build_risk_policy, ensure_default_policies
from data.feature_engine import FeatureEngine
from data.schemas import OrderRequest, ReviewSummary
from execution.executor import ExecutionExecutor
from execution.order_manager import OrderManager
from infra.logging import configure_logging, get_logger
from memory.journal import Journal
from memory.lessons import LessonStore
from memory.supabase import SupabaseStore
from risk.policy import RiskPolicy


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    logger = get_logger(__name__)
    logger.info("AT agent loop starting")
    logger.info("environment=%s agent=%s default_symbol=%s", settings.app_env, settings.agent_name, settings.trading_symbol)

    if not settings.has_alpaca_credentials:
        logger.warning(
            "Alpaca credentials are not configured yet. Copy .env.example to .env and fill in the API values."
        )
        return

    store = SupabaseStore(settings.supabase_db_dsn) if settings.supabase_db_dsn is not None else None
    default_policy_ids = ensure_default_policies(store, settings) if store is not None else {}
    agent_config = _load_agent_config(store=store, settings=settings, default_policy_ids=default_policy_ids)

    client = AlpacaClient(settings)
    account_service = AlpacaAccountService(client)
    trading_service = AlpacaTradingService(client)
    order_manager = OrderManager()
    trading_stream_service = AlpacaTradingStreamService(
        websocket_url=settings.alpaca_trading_stream_ws_url,
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    reviewer = ReviewerAgent()
    feature_engine = FeatureEngine()
    journal = Journal(settings.journal_path)
    lesson_store = LessonStore(settings.lessons_path)
    executor = ExecutionExecutor(trading_service, order_manager)
    runtime_id = str(uuid4())
    executed_trade_times: deque[datetime] = deque()

    try:
        await trading_stream_service.handshake()
        logger.info("trade_updates_stream_ok stream=trade_updates")

        for iteration in _iteration_sequence(settings.decision_loop_iterations):
            if iteration > 1:
                await asyncio.sleep(agent_config.decision_interval_seconds)

            agent_config = _load_agent_config(
                store=store,
                settings=settings,
                default_policy_ids=default_policy_ids,
                fallback=agent_config,
            )
            if agent_config.status == "stopped":
                _record_heartbeat(
                    store=store,
                    agent_config=agent_config,
                    runtime_id=runtime_id,
                    status="stopped",
                    details={"reason": "agent_config_status_stopped"},
                )
                logger.info("agent_stopping reason=agent_config_status_stopped")
                break

            if agent_config.status == "paused":
                _record_heartbeat(
                    store=store,
                    agent_config=agent_config,
                    runtime_id=runtime_id,
                    status="paused",
                    details={"reason": "agent_config_status_paused"},
                )
                logger.info("agent_paused agent=%s", agent_config.agent_name)
                continue

            symbol = agent_config.symbols[0] if agent_config.symbols else settings.trading_symbol
            analyst, policy_version_id = _build_runtime_policy(
                store=store,
                agent_config=agent_config,
                default_policy_ids=default_policy_ids,
            )
            risk_policy = build_risk_policy(agent_config)
            market_data_service = AlpacaMarketDataService(
                websocket_url=settings.alpaca_crypto_data_ws_url,
                symbol=symbol,
                api_key=settings.alpaca_api_key.get_secret_value(),
                api_secret=settings.alpaca_api_secret.get_secret_value(),
            )

            while executed_trade_times and executed_trade_times[0] < datetime.now(timezone.utc) - timedelta(hours=1):
                executed_trade_times.popleft()

            account_snapshot = await account_service.fetch_account_snapshot(symbol)
            market_snapshot = await _read_market_snapshot_with_retry(market_data_service, logger)
            features = feature_engine.build_features(market_snapshot)
            decision = analyst.analyze(market_snapshot, account_snapshot, features)
            risk_decision = risk_policy.evaluate(
                decision=decision,
                account_snapshot=account_snapshot,
                market_snapshot=market_snapshot,
                order_manager=order_manager,
                trades_this_hour=len(executed_trade_times),
            )

            logger.info(
                "decision_loop iteration=%s agent=%s symbol=%s action=%s confidence=%.2f approved=%s reason=%s",
                iteration,
                agent_config.agent_name,
                symbol,
                decision.action,
                decision.confidence,
                risk_decision.approved,
                risk_decision.reason,
            )
            logger.info("feature_snapshot iteration=%s %s", iteration, features)
            event_payload = {
                "record_type": "decision",
                "agent_name": agent_config.agent_name,
                "iteration": iteration,
                "market_snapshot": market_snapshot.model_dump(mode="json"),
                "account_snapshot": account_snapshot.model_dump(mode="json"),
                "features": features,
                "decision": decision.model_dump(mode="json"),
                "risk_decision": risk_decision.model_dump(mode="json"),
            }
            journal.record(event_payload)
            decision_id = (
                store.insert_decision_record(
                    agent_config_id=agent_config.id,
                    agent_name=agent_config.agent_name,
                    symbol=symbol,
                    action=decision.action,
                    decision_confidence=decision.confidence,
                    rationale=decision.rationale,
                    risk_approved=risk_decision.approved,
                    risk_reason=risk_decision.reason,
                    allowed_notional_usd=risk_decision.allowed_notional_usd,
                    trades_this_hour=len(executed_trade_times),
                    reference_price=_reference_price(market_snapshot),
                    spread_bps=features.get("spread_bps"),
                    market_timestamp=market_snapshot.timestamp,
                    policy_version_id=policy_version_id,
                    market_snapshot=market_snapshot.model_dump(mode="json"),
                    account_snapshot=account_snapshot.model_dump(mode="json"),
                    features=features,
                    decision_payload=decision.model_dump(mode="json"),
                    risk_payload=risk_decision.model_dump(mode="json"),
                )
                if store is not None
                else None
            )

            _record_heartbeat(
                store=store,
                agent_config=agent_config,
                runtime_id=runtime_id,
                status="healthy",
                current_symbol=symbol,
                latest_decision_action=decision.action,
                latest_decision_at=market_snapshot.timestamp,
                open_position_qty=account_snapshot.open_position_qty,
                cash=account_snapshot.cash,
                equity=account_snapshot.equity,
                details={
                    "iteration": iteration,
                    "risk_approved": risk_decision.approved,
                    "risk_reason": risk_decision.reason,
                    "policy_version_id": policy_version_id,
                },
            )

            should_execute_orders = (
                agent_config.status == "active"
                and agent_config.enable_agent_orders
                and settings.enable_paper_test_order
            )
            if not should_execute_orders:
                continue

            await _maybe_execute_decision(
                settings=settings,
                logger=logger,
                agent_config=agent_config,
                decision=decision,
                decision_id=decision_id,
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
                store=store,
                executed_trade_times=executed_trade_times,
            )
    finally:
        summary = reviewer.summarize_journal(journal.read_all())
        inserted = lesson_store.add_many(summary.lessons)
        if store is not None:
            store.upsert_lessons(summary.lessons)
        _write_review_summary(settings.review_summary_path, summary)
        logger.info(
            "review_summary_ready total_records=%s trade_reviews=%s lessons_added=%s",
            summary.total_records,
            summary.trade_reviews,
            inserted,
        )
        await client.aclose()


def _load_agent_config(
    *,
    store: SupabaseStore | None,
    settings: Settings,
    default_policy_ids: dict[str, str],
    fallback: AgentConfigRecord | None = None,
) -> AgentConfigRecord:
    if store is None:
        return _settings_to_agent_config(settings)

    agent_config = store.get_agent_config(settings.agent_name)
    if agent_config is None:
        agent_config = _settings_to_agent_config(settings)
        agent_config.strategy_policy_version_id = default_policy_ids.get("baseline")
        agent_config.id = store.upsert_agent_config(agent_config)
        return agent_config

    changed = False
    if not agent_config.symbols:
        agent_config.symbols = [settings.trading_symbol]
        changed = True
    if agent_config.strategy_policy_version_id is None and default_policy_ids.get("baseline") is not None:
        agent_config.strategy_policy_version_id = default_policy_ids["baseline"]
        changed = True
    elif agent_config.strategy_policy_version_id is not None:
        active_policy = store.get_policy_version(agent_config.strategy_policy_version_id)
        if (
            active_policy is not None
            and active_policy.version == "v1"
            and active_policy.policy_name in default_policy_ids
            and default_policy_ids[active_policy.policy_name] != active_policy.id
        ):
            agent_config.strategy_policy_version_id = default_policy_ids[active_policy.policy_name]
            changed = True
    if changed:
        agent_config.id = store.upsert_agent_config(agent_config)
    return agent_config


def _settings_to_agent_config(settings: Settings) -> AgentConfigRecord:
    return AgentConfigRecord(
        agent_name=settings.agent_name,
        description="Environment-backed fallback agent config.",
        status="active",
        broker="alpaca",
        mode="paper",
        symbols=[settings.trading_symbol],
        decision_interval_seconds=settings.decision_interval_seconds,
        max_trades_per_hour=settings.max_trades_per_hour,
        max_risk_per_trade_pct=settings.max_risk_per_trade_pct,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_position_notional_usd=settings.max_position_notional_usd,
        max_spread_bps=settings.max_spread_bps,
        min_decision_confidence=settings.min_decision_confidence,
        cooldown_seconds_after_trade=settings.cooldown_seconds_after_trade,
        enable_agent_orders=settings.enable_agent_orders,
    )


def _build_runtime_policy(
    *,
    store: SupabaseStore | None,
    agent_config: AgentConfigRecord,
    default_policy_ids: dict[str, str],
) -> tuple[AnalystAgent, str | None]:
    if store is None or agent_config.strategy_policy_version_id is None:
        return AnalystAgent(), default_policy_ids.get("baseline")

    policy = store.get_policy_version(agent_config.strategy_policy_version_id)
    if policy is None:
        return AnalystAgent(), default_policy_ids.get("baseline")

    return build_analyst_agent(policy), policy.id


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
    settings: Settings,
    logger,
    agent_config: AgentConfigRecord,
    decision,
    decision_id: str | None,
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
    store: SupabaseStore | None,
    executed_trade_times: deque[datetime],
) -> None:
    if not risk_approved:
        logger.info("agent_order_skipped reason=risk_rejected")
        return

    if decision.action == "buy":
        requested_notional = (
            Decimal(str(decision.execution_plan.requested_notional_usd))
            if decision.execution_plan is not None and decision.execution_plan.requested_notional_usd is not None
            else settings.paper_test_order_notional_usd
        )
        order_notional = min(risk_allowed_notional, settings.paper_test_order_notional_usd, requested_notional)
        if order_notional <= 0:
            logger.info("agent_order_skipped reason=zero_notional")
            return
        request = OrderRequest(
            symbol=market_snapshot.symbol,
            side="buy",
            type=(decision.execution_plan.order_type if decision.execution_plan is not None else "market"),
            time_in_force=(decision.execution_plan.time_in_force if decision.execution_plan is not None else "gtc"),
            notional=order_notional,
        )
    elif decision.action == "sell" and account_snapshot.open_position_qty <= 0:
        logger.info("agent_order_skipped reason=alpaca_crypto_spot_short_not_supported action=sell")
        return
    elif decision.action == "exit" and account_snapshot.open_position_qty > 0:
        request = OrderRequest(
            symbol=market_snapshot.symbol,
            side="sell",
            type=(decision.execution_plan.order_type if decision.execution_plan is not None else "market"),
            time_in_force=(decision.execution_plan.time_in_force if decision.execution_plan is not None else "gtc"),
            qty=account_snapshot.open_position_qty,
        )
    else:
        logger.info("agent_order_skipped reason=decision_not_executable action=%s", decision.action)
        return

    order = await executor.place(request)
    executed_trade_times.append(datetime.now(timezone.utc))
    logger.info(
        "agent_order_submitted order_id=%s side=%s notional=%s qty=%s status=%s",
        order.id,
        order.side,
        order.notional,
        order.qty,
        order.status,
    )
    stored_order_id = (
        store.upsert_order_record(
            agent_config_id=agent_config.id,
            agent_name=agent_config.agent_name,
            decision_id=decision_id,
            order=order,
        )
        if store is not None
        else None
    )
    try:
        update = await trading_stream_service.read_order_update(order.id, timeout_seconds=20)
    except TimeoutError:
        logger.warning("agent_order_update_timeout order_id=%s", order.id)
        update = None

    after_account_snapshot = await account_service.fetch_account_snapshot(market_snapshot.symbol)
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
            "agent_name": agent_config.agent_name,
            "order": order.model_dump(mode="json"),
            "trade_update": update.model_dump(mode="json") if update is not None else None,
            "review": review.model_dump(mode="json"),
        }
    )
    lessons = reviewer.lessons_from_review(review)
    lesson_store.add_many(lessons)
    if store is not None:
        store.upsert_lessons(lessons)
        if update is not None:
            stored_order_id = store.upsert_order_record(
                agent_config_id=agent_config.id,
                agent_name=agent_config.agent_name,
                decision_id=decision_id,
                order=update.order,
            )
        store.insert_trade_outcome(
            agent_config_id=agent_config.id,
            agent_name=agent_config.agent_name,
            decision_id=decision_id,
            order_id=stored_order_id,
            review=review,
        )
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


def _record_heartbeat(
    *,
    store: SupabaseStore | None,
    agent_config: AgentConfigRecord,
    runtime_id: str,
    status: str,
    current_symbol: str | None = None,
    latest_decision_action: str | None = None,
    latest_decision_at: datetime | None = None,
    latest_order_at: datetime | None = None,
    open_position_qty: Decimal | None = None,
    cash: Decimal | None = None,
    equity: Decimal | None = None,
    details: dict[str, object] | None = None,
) -> None:
    if store is None or agent_config.id is None:
        return

    store.record_agent_heartbeat(
        AgentHeartbeatRecord(
            agent_config_id=agent_config.id,
            runtime_id=runtime_id,
            status=status,
            current_symbol=current_symbol,
            latest_decision_action=latest_decision_action,
            latest_decision_at=latest_decision_at.isoformat() if latest_decision_at else None,
            latest_order_at=latest_order_at.isoformat() if latest_order_at else None,
            open_position_qty=open_position_qty,
            cash=cash,
            equity=equity,
            details=details or {},
        )
    )


def _reference_price(market_snapshot) -> Decimal | None:
    if market_snapshot.last_trade_price is not None:
        return market_snapshot.last_trade_price
    if market_snapshot.bid_price is not None and market_snapshot.ask_price is not None:
        return (market_snapshot.bid_price + market_snapshot.ask_price) / Decimal("2")
    return market_snapshot.bid_price or market_snapshot.ask_price


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
