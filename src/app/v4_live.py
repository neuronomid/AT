from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, deque
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from agents.llm_live_analyst import LLMAnalysisResult
from agents.reviewer import ReviewerAgent
from app.config import Settings, get_settings
from brokers.alpaca.account import AlpacaAccountService
from brokers.alpaca.client import AlpacaClient
from brokers.alpaca.historical import AlpacaHistoricalCryptoService
from brokers.alpaca.market_data import AlpacaMarketDataService
from brokers.alpaca.trading import AlpacaTradingService
from brokers.alpaca.trading_stream import AlpacaTradingStreamService
from control_plane.models import AgentConfigRecord
from control_plane.policies import (
    build_v4_live_policy,
    build_v4_runtime_components,
    ensure_v4_live_policy,
)
from data.schemas import HistoricalBar, LessonRecord, LLMRuntimeDecision, LiveCandle, OrderRequest, TradeDecision, TradeReflection
from execution.executor import ExecutionExecutor
from execution.order_manager import OrderManager
from execution.position_tracker import PositionTracker
from feedback.reflection import build_trade_reflection, derive_lessons
from infra.logging import configure_logging, get_logger
from memory.journal import Journal
from memory.supabase import SupabaseStore
from runtime.candle_builder import CandleBuilder
from runtime.context_packet import ContextPacketBuilder


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the v4 live LLM paper-trading session.")
    parser.add_argument("--agent-name", default="primary")
    parser.add_argument("--duration-minutes", type=int, default=60)
    parser.add_argument("--session-tag", default=None)
    return parser.parse_args()


async def run() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.alpaca_api_key is None or settings.alpaca_api_secret is None:
        raise RuntimeError("Alpaca credentials are required for v4 live paper trading.")
    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for v4 live runtime decisions.")

    session_tag = args.session_tag or datetime.now(timezone.utc).strftime("v4-paper-%Y%m%d-%H%M%S")
    artifact_dir = Path("var/v4") / session_tag
    artifact_dir.mkdir(parents=True, exist_ok=True)
    decision_journal = Journal(str(artifact_dir / "decision_journal.jsonl"))
    reflection_journal = Journal(str(artifact_dir / "trade_reflections.jsonl"))

    store = SupabaseStore(settings.supabase_db_dsn) if settings.supabase_db_dsn is not None else None
    policy = build_v4_live_policy()
    if store is not None:
        policy_id = ensure_v4_live_policy(store)
        loaded_policy = store.get_policy_version(policy_id)
        if loaded_policy is not None:
            policy = loaded_policy

    agent_config = _load_or_create_v4_agent_config(
        store=store,
        settings=settings,
        agent_name=args.agent_name,
        policy_id=(policy.id if store is None else policy.id if getattr(policy, "id", None) else None),
    )
    analyst, risk_policy = build_v4_runtime_components(policy=policy, settings=settings)

    client = AlpacaClient(settings)
    account_service = AlpacaAccountService(client)
    trading_service = AlpacaTradingService(client)
    trading_stream_service = AlpacaTradingStreamService(
        websocket_url=settings.alpaca_trading_stream_ws_url,
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    market_data_service = AlpacaMarketDataService(
        websocket_url=settings.alpaca_crypto_data_ws_url,
        symbol=settings.trading_symbol,
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    history_service = AlpacaHistoricalCryptoService(
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
    )
    await trading_stream_service.handshake()

    reviewer = ReviewerAgent()
    order_manager = OrderManager()
    executor = ExecutionExecutor(trading_service, order_manager)
    position_tracker = PositionTracker()
    context_builder = ContextPacketBuilder(candle_lookback=int(policy.strategy_config.get("candle_lookback", 20)))
    candle_builder = CandleBuilder(
        settings.trading_symbol,
        max_candles=480,
        stale_after_seconds=int(policy.strategy_config.get("stale_after_seconds", 90)),
    )
    closed_candles: deque[LiveCandle] = deque(maxlen=480)
    for candle in await _fetch_recent_candles(history_service, settings.trading_symbol, minutes=45):
        closed_candles.append(candle)
    last_processed_candle_at = closed_candles[-1].end_at if closed_candles else None

    session_stats = {
        "session_tag": session_tag,
        "policy_label": policy.label,
        "total_decisions": 0,
        "executable_decisions": 0,
        "entries": 0,
        "partial_exits": 0,
        "full_exits": 0,
        "fills": 0,
        "rejections": 0,
        "llm_decisions": 0,
        "deterministic_decisions": 0,
        "position_reconciliations": 0,
        "market_stream_reconnects": 0,
        "risk_rejection_reasons": Counter(),
    }
    reflections: list[TradeReflection] = []
    lessons: list[LessonRecord] = []
    recent_context_signatures: deque[str] = deque(maxlen=3)
    entry_times: deque[datetime] = deque()
    end_at = datetime.now(timezone.utc) + timedelta(minutes=max(1, args.duration_minutes))
    starting_account = await account_service.fetch_account_snapshot(settings.trading_symbol)
    session_start_equity = starting_account.equity

    logger.info("v4_live_start session_tag=%s end_at=%s", session_tag, end_at.isoformat())
    try:
        async for closed_candle in _stream_closed_candles(
            market_data_service=market_data_service,
            history_service=history_service,
            candle_builder=candle_builder,
            symbol=settings.trading_symbol,
            end_at=end_at,
            last_processed_candle_at=last_processed_candle_at,
            logger=logger,
            session_stats=session_stats,
        ):
            closed_candles.append(closed_candle)
            last_processed_candle_at = closed_candle.end_at

            now = datetime.now(timezone.utc)
            while entry_times and entry_times[0] < now - timedelta(hours=1):
                entry_times.popleft()

            account_snapshot = await account_service.fetch_account_snapshot(settings.trading_symbol)
            if _reconcile_position_tracker(
                position_tracker=position_tracker,
                account_snapshot=account_snapshot,
                logger=logger,
            ):
                session_stats["position_reconciliations"] += 1

            if (
                not position_tracker.has_position()
                and account_snapshot.open_position_qty > 0
                and account_snapshot.avg_entry_price > 0
                and len(closed_candles) >= 5
            ):
                _bootstrap_existing_position(
                    position_tracker=position_tracker,
                    risk_policy=risk_policy,
                    candles=list(closed_candles)[-int(policy.strategy_config.get("candle_lookback", 20)) :],
                    account_snapshot=account_snapshot,
                    opened_at=closed_candle.end_at,
                )
                logger.info(
                    "v4_bootstrapped_open_position qty=%s avg_entry_price=%s",
                    account_snapshot.open_position_qty,
                    account_snapshot.avg_entry_price,
                )

            if position_tracker.has_position():
                position_tracker.record_candle(closed_candle.close_price)

            stale_age_seconds = _market_stale_age_seconds(
                candle_builder=candle_builder,
                last_processed_candle_at=last_processed_candle_at,
                now=now,
            )
            latest_reflection = reflections[-1] if reflections else None
            context_packet = context_builder.build(
                candles=list(closed_candles)[-int(policy.strategy_config.get("candle_lookback", 20)) :],
                account_snapshot=account_snapshot,
                open_trade=position_tracker.open_trade,
                trades_this_hour=len(entry_times),
                stale_age_seconds=stale_age_seconds,
                latest_reflection=latest_reflection,
                lessons=lessons,
            )
            current_signature = str(context_packet["state"]["context_signature"])
            recent_context_signatures.append(current_signature)

            runtime_decision, llm_result, decision_source = await _build_runtime_decision(
                analyst=analyst,
                risk_policy=risk_policy,
                context_packet=context_packet,
                position_tracker=position_tracker,
                closed_candle=closed_candle,
                current_equity=account_snapshot.equity,
                session_start_equity=session_start_equity,
            )

            decision = (
                runtime_decision
                if isinstance(runtime_decision, TradeDecision)
                else risk_policy.normalize_decision(
                    runtime_decision=runtime_decision,
                    candles=list(closed_candles)[-int(policy.strategy_config.get("candle_lookback", 20)) :],
                    account_snapshot=account_snapshot,
                    context_signature=current_signature,
                )
            )

            last_losing_signature = None
            if reflections and reflections[-1].realized_pnl_usd < 0:
                last_losing_signature = reflections[-1].context_signature

            risk_decision = risk_policy.evaluate(
                decision=decision,
                account_snapshot=account_snapshot,
                order_manager=order_manager,
                position_tracker=position_tracker,
                trades_this_hour=len(entry_times),
                spread_bps=closed_candle.spread_bps,
                stale_age_seconds=stale_age_seconds,
                recent_context_signatures=list(recent_context_signatures),
                last_losing_signature=last_losing_signature,
            )
            decision_id = _record_decision(
                decision_journal=decision_journal,
                store=store,
                agent_config=agent_config,
                policy_id=(policy.id if store is not None else None),
                decision=decision,
                llm_result=llm_result,
                context_packet=context_packet,
                risk_decision=risk_decision,
                market_timestamp=closed_candle.end_at,
                account_snapshot=account_snapshot,
                closed_candle=closed_candle,
                session_tag=session_tag,
            )

            session_stats["total_decisions"] += 1
            if llm_result is not None:
                session_stats["llm_decisions"] += 1
            else:
                session_stats["deterministic_decisions"] += 1
            if decision.action in {"buy", "reduce", "exit"}:
                session_stats["executable_decisions"] += 1
            if not risk_decision.approved:
                session_stats["rejections"] += 1
                session_stats["risk_rejection_reasons"][risk_decision.reason] += 1
                continue

            outcome = await _execute_decision(
                decision=decision,
                decision_source=decision_source,
                decision_journal=decision_journal,
                reflection_journal=reflection_journal,
                reviewer=reviewer,
                executor=executor,
                trading_stream_service=trading_stream_service,
                account_service=account_service,
                store=store,
                agent_config=agent_config,
                policy_id=(policy.id if store is not None else None),
                decision_id=decision_id,
                account_snapshot=account_snapshot,
                closed_candle=closed_candle,
                risk_allowed_notional=risk_decision.allowed_notional_usd,
                position_tracker=position_tracker,
                session_stats=session_stats,
                entry_times=entry_times,
                context_packet=context_packet,
                reflections=reflections,
                lessons=lessons,
            )
            if outcome is not None:
                logger.info("v4_trade_event source=%s detail=%s", decision_source, outcome)

        if position_tracker.has_position():
            logger.info("v4_live_session_end_forcing_exit session_tag=%s", session_tag)
            account_snapshot = await account_service.fetch_account_snapshot(settings.trading_symbol)
            exit_decision = TradeDecision(
                action="exit",
                confidence=1.0,
                rationale="Session ended; flatten the remaining paper position.",
                context_signature=position_tracker.open_trade.context_signature if position_tracker.open_trade else None,
                thesis_tags=["session_end"],
            )
            await _execute_decision(
                decision=exit_decision,
                decision_source="session_end",
                decision_journal=decision_journal,
                reflection_journal=reflection_journal,
                reviewer=reviewer,
                executor=executor,
                trading_stream_service=trading_stream_service,
                account_service=account_service,
                store=store,
                agent_config=agent_config,
                policy_id=(policy.id if store is not None else None),
                decision_id=None,
                account_snapshot=account_snapshot,
                closed_candle=list(closed_candles)[-1],
                risk_allowed_notional=Decimal("0"),
                position_tracker=position_tracker,
                session_stats=session_stats,
                entry_times=entry_times,
                context_packet={"feedback": {"avoid": [], "reinforce": []}},
                reflections=reflections,
                lessons=lessons,
            )
    finally:
        summary = _build_summary(
            session_tag=session_tag,
            session_stats=session_stats,
            reflections=reflections,
            lessons=lessons,
        )
        _write_session_artifacts(
            artifact_dir=artifact_dir,
            summary=summary,
            reflections=reflections,
        )
        await history_service.aclose()
        await client.aclose()


async def _stream_closed_candles(
    *,
    market_data_service: AlpacaMarketDataService,
    history_service: AlpacaHistoricalCryptoService,
    candle_builder: CandleBuilder,
    symbol: str,
    end_at: datetime,
    last_processed_candle_at: datetime | None,
    logger,
    session_stats: dict[str, object],
) -> AsyncIterator[LiveCandle]:
    reconnect_delay_seconds = 2.0
    while datetime.now(timezone.utc) < end_at:
        snapshot_stream = market_data_service.stream_snapshots()
        try:
            while datetime.now(timezone.utc) < end_at:
                remaining_seconds = max(5.0, (end_at - datetime.now(timezone.utc)).total_seconds())
                snapshot = await asyncio.wait_for(anext(snapshot_stream), timeout=min(25.0, remaining_seconds))
                for flushed_candle in candle_builder.flush(snapshot.timestamp):
                    if last_processed_candle_at is not None and flushed_candle.end_at <= last_processed_candle_at:
                        continue
                    last_processed_candle_at = flushed_candle.end_at
                    yield flushed_candle
                closed_candle = candle_builder.update(snapshot)
                if closed_candle is None:
                    continue
                if last_processed_candle_at is not None and closed_candle.end_at <= last_processed_candle_at:
                    continue
                last_processed_candle_at = closed_candle.end_at
                yield closed_candle
        except TimeoutError:
            session_stats["market_stream_reconnects"] += 1
            logger.warning(
                "v4_market_stream_timeout reconnect_count=%s",
                session_stats["market_stream_reconnects"],
            )
            for flushed_candle in candle_builder.flush():
                if last_processed_candle_at is not None and flushed_candle.end_at <= last_processed_candle_at:
                    continue
                last_processed_candle_at = flushed_candle.end_at
                yield flushed_candle
            catchup_candles = await _fetch_recent_candles(history_service, symbol, minutes=5)
            for catchup_candle in catchup_candles:
                if last_processed_candle_at is not None and catchup_candle.end_at <= last_processed_candle_at:
                    continue
                last_processed_candle_at = catchup_candle.end_at
                yield catchup_candle
            await asyncio.sleep(reconnect_delay_seconds)
        except Exception as exc:  # pragma: no cover - exercised in live runtime
            session_stats["market_stream_reconnects"] += 1
            logger.warning(
                "v4_market_stream_error reconnect_count=%s error=%s",
                session_stats["market_stream_reconnects"],
                exc,
            )
            await asyncio.sleep(reconnect_delay_seconds)
        finally:
            await snapshot_stream.aclose()


def _reconcile_position_tracker(
    *,
    position_tracker: PositionTracker,
    account_snapshot,
    logger,
) -> bool:
    trade = position_tracker.open_trade
    if trade is None:
        return False

    if account_snapshot.open_position_qty <= 0:
        logger.warning(
            "v4_position_reconciled reason=account_flat tracker_qty=%s",
            trade.remaining_qty,
        )
        position_tracker.clear()
        return True

    changed = position_tracker.sync_with_account(
        qty=account_snapshot.open_position_qty,
        avg_entry_price=account_snapshot.avg_entry_price,
    )
    if changed:
        logger.info(
            "v4_position_reconciled reason=broker_truth tracker_qty=%s account_qty=%s",
            trade.remaining_qty,
            account_snapshot.open_position_qty,
        )
    return changed


def _market_stale_age_seconds(
    *,
    candle_builder: CandleBuilder,
    last_processed_candle_at: datetime | None,
    now: datetime,
) -> float | None:
    candidate_ages: list[float] = []
    latest_snapshot_age = candle_builder.latest_snapshot_age_seconds(now=now)
    candle_age = _stale_age_seconds(last_processed_candle_at, now=now)
    if latest_snapshot_age is not None:
        candidate_ages.append(latest_snapshot_age)
    if candle_age is not None:
        candidate_ages.append(candle_age)
    return min(candidate_ages) if candidate_ages else None


async def _build_runtime_decision(
    *,
    analyst,
    risk_policy,
    context_packet: dict[str, object],
    position_tracker: PositionTracker,
    closed_candle,
    current_equity: Decimal,
    session_start_equity: Decimal,
) -> tuple[TradeDecision | LLMRuntimeDecision, LLMAnalysisResult | None, str]:
    if position_tracker.has_position():
        if risk_policy.should_kill_for_daily_loss(
            session_start_equity=session_start_equity,
            current_equity=current_equity,
        ):
            return (
                TradeDecision(action="exit", confidence=1.0, rationale="Daily loss kill switch triggered."),
                None,
                "kill_switch",
            )
        if position_tracker.should_hard_stop(closed_candle.close_price):
            return (
                TradeDecision(action="exit", confidence=1.0, rationale="Hard stop hit at -1R."),
                None,
                "hard_stop",
            )
        if position_tracker.should_take_partial(closed_candle.close_price):
            return (
                TradeDecision(
                    action="reduce",
                    confidence=1.0,
                    rationale="Auto-partial at the configured target.",
                    reduce_fraction=position_tracker.suggested_reduce_fraction(),
                ),
                None,
                "auto_partial",
            )
        if position_tracker.should_trailing_stop(closed_candle.close_price):
            return (
                TradeDecision(action="exit", confidence=1.0, rationale="Trailing stop triggered."),
                None,
                "trailing_stop",
            )
        if position_tracker.should_time_exit(risk_policy.max_bars_in_trade):
            return (
                TradeDecision(action="exit", confidence=1.0, rationale="Time stop reached."),
                None,
                "time_stop",
            )

    llm_result = await analyst.analyze(context_packet)
    return llm_result.decision, llm_result, "llm"


def _load_or_create_v4_agent_config(
    *,
    store: SupabaseStore | None,
    settings: Settings,
    agent_name: str,
    policy_id: str | None,
) -> AgentConfigRecord:
    if store is None:
        return AgentConfigRecord(
            agent_name=agent_name,
            description="Environment-backed v4 live agent config.",
            status="active",
            broker="alpaca",
            mode="paper",
            symbols=[settings.trading_symbol],
            decision_interval_seconds=60,
            max_trades_per_hour=10,
            max_risk_per_trade_pct=0.015,
            max_daily_loss_pct=settings.max_daily_loss_pct,
            max_position_notional_usd=settings.max_position_notional_usd,
            max_spread_bps=settings.max_spread_bps,
            min_decision_confidence=0.60,
            cooldown_seconds_after_trade=0,
            enable_agent_orders=True,
            strategy_policy_version_id=policy_id,
        )

    existing = store.get_agent_config(agent_name)
    if existing is None:
        existing = AgentConfigRecord(
            agent_name=agent_name,
            description="Dedicated v4 live paper-trading agent.",
            status="active",
            broker="alpaca",
            mode="paper",
            symbols=[settings.trading_symbol],
            decision_interval_seconds=60,
            max_trades_per_hour=10,
            max_risk_per_trade_pct=0.015,
            max_daily_loss_pct=settings.max_daily_loss_pct,
            max_position_notional_usd=settings.max_position_notional_usd,
            max_spread_bps=settings.max_spread_bps,
            min_decision_confidence=0.60,
            cooldown_seconds_after_trade=0,
            enable_agent_orders=True,
            strategy_policy_version_id=policy_id,
        )
    else:
        existing.status = "active"
        existing.mode = "paper"
        existing.symbols = [settings.trading_symbol]
        existing.decision_interval_seconds = 60
        existing.max_trades_per_hour = 10
        existing.max_risk_per_trade_pct = 0.015
        existing.min_decision_confidence = 0.60
        existing.cooldown_seconds_after_trade = 0
        existing.enable_agent_orders = True
        existing.strategy_policy_version_id = policy_id
    existing.id = store.upsert_agent_config(existing)
    return existing


def _record_decision(
    *,
    decision_journal: Journal,
    store: SupabaseStore | None,
    agent_config: AgentConfigRecord,
    policy_id: str | None,
    decision: TradeDecision,
    llm_result: LLMAnalysisResult | None,
    context_packet: dict[str, object],
    risk_decision,
    market_timestamp: datetime,
    account_snapshot,
    closed_candle,
    session_tag: str,
) -> str | None:
    payload = {
        "record_type": "decision",
        "agent_name": agent_config.agent_name,
        "session_tag": session_tag,
        "market_timestamp": market_timestamp.isoformat(),
        "candle_close": float(closed_candle.close_price),
        "context_packet": context_packet,
        "decision": decision.model_dump(mode="json"),
        "risk_decision": risk_decision.model_dump(mode="json"),
        "raw_llm_response": (llm_result.raw_response if llm_result is not None else None),
        "decision_source": ("llm" if llm_result is not None else "deterministic"),
    }
    decision_journal.record(payload)
    if store is None:
        return None
    return store.insert_decision_record(
        agent_config_id=agent_config.id,
        agent_name=agent_config.agent_name,
        symbol=closed_candle.symbol,
        action=decision.action,
        decision_confidence=decision.confidence,
        rationale=decision.rationale,
        risk_approved=risk_decision.approved,
        risk_reason=risk_decision.reason,
        allowed_notional_usd=risk_decision.allowed_notional_usd,
        trades_this_hour=int(context_packet["portfolio"]["trades_this_hour"]),
        reference_price=closed_candle.close_price,
        spread_bps=closed_candle.spread_bps,
        market_timestamp=market_timestamp,
        policy_version_id=policy_id,
        market_snapshot={
            "start_at": closed_candle.start_at.isoformat(),
            "end_at": closed_candle.end_at.isoformat(),
            "close_price": float(closed_candle.close_price),
            "spread_bps": closed_candle.spread_bps,
        },
        account_snapshot=account_snapshot.model_dump(mode="json"),
        features={
            **dict(context_packet["indicator_snapshot"]),
            **{"spread_bps": closed_candle.spread_bps},
        },
        decision_payload={
            "normalized_decision": decision.model_dump(mode="json"),
            "raw_llm_response": (llm_result.raw_response if llm_result is not None else None),
            "llm_latency_ms": (llm_result.latency_ms if llm_result is not None else 0),
            "context_packet": context_packet,
            "session_tag": session_tag,
        },
        risk_payload=risk_decision.model_dump(mode="json"),
        analyst_model=("llm_live_runtime" if llm_result is not None else "deterministic"),
        analyst_prompt_version=("v4.0" if llm_result is not None else "deterministic"),
        record_source="v4_live",
        notes=session_tag,
    )


async def _execute_decision(
    *,
    decision: TradeDecision,
    decision_source: str,
    decision_journal: Journal,
    reflection_journal: Journal,
    reviewer: ReviewerAgent,
    executor: ExecutionExecutor,
    trading_stream_service: AlpacaTradingStreamService,
    account_service: AlpacaAccountService,
    store: SupabaseStore | None,
    agent_config: AgentConfigRecord,
    policy_id: str | None,
    decision_id: str | None,
    account_snapshot,
    closed_candle,
    risk_allowed_notional: Decimal,
    position_tracker: PositionTracker,
    session_stats: dict[str, object],
    entry_times: deque[datetime],
    context_packet: dict[str, object],
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
) -> str | None:
    request = _order_request_from_decision(
        decision=decision,
        symbol=closed_candle.symbol,
        account_snapshot=account_snapshot,
        risk_allowed_notional=risk_allowed_notional,
    )
    if request is None:
        return None

    order = await executor.place(request)
    if decision.action == "buy":
        entry_times.append(datetime.now(timezone.utc))
    stored_order_id = None
    if store is not None:
        stored_order_id = store.upsert_order_record(
            agent_config_id=agent_config.id,
            agent_name=agent_config.agent_name,
            decision_id=decision_id,
            order=order,
        )

    update = None
    try:
        update = await trading_stream_service.read_order_update(order.id, timeout_seconds=30)
    except TimeoutError:
        update = None
        latest_order = await executor._trading_service.fetch_order(order.id)
        if latest_order.filled_qty is not None and latest_order.filled_qty > 0:
            update = _synthetic_trade_update(latest_order)

    after_account_snapshot = await account_service.fetch_account_snapshot(closed_candle.symbol)
    if update is not None:
        executor._order_manager.apply_update(update)
        session_stats["fills"] += 1
        if store is not None:
            stored_order_id = store.upsert_order_record(
                agent_config_id=agent_config.id,
                agent_name=agent_config.agent_name,
                decision_id=decision_id,
                order=update.order,
            )

    review = reviewer.review_execution(
        decision=decision,
        market_snapshot=closed_candle,
        before_account=account_snapshot,
        after_account=after_account_snapshot,
        order=(update.order if update is not None else order),
        update=update,
        spread_bps=closed_candle.spread_bps,
    )
    decision_journal.record(
        {
            "record_type": "trade_review",
            "decision_source": decision_source,
            "order": order.model_dump(mode="json"),
            "trade_update": update.model_dump(mode="json") if update is not None else None,
            "review": review.model_dump(mode="json"),
        }
    )
    if store is not None:
        store.insert_trade_outcome(
            agent_config_id=agent_config.id,
            agent_name=agent_config.agent_name,
            decision_id=decision_id,
            order_id=stored_order_id,
            review=review,
        )

    filled_price = _filled_price(update, order, closed_candle.close_price)
    filled_qty = _filled_qty(update, order, request)
    if filled_qty is None or filled_qty <= 0 or filled_price is None:
        return review.outcome

    if decision.action == "buy":
        session_stats["entries"] += 1
        tracked_fill_price = (
            after_account_snapshot.avg_entry_price if after_account_snapshot.avg_entry_price > 0 else filled_price
        )
        tracked_fill_qty = (
            after_account_snapshot.open_position_qty if after_account_snapshot.open_position_qty > 0 else filled_qty
        )
        position_tracker.open_from_fill(
            opened_at=(update.timestamp if update is not None and update.timestamp is not None else closed_candle.end_at),
            symbol=closed_candle.symbol,
            fill_price=tracked_fill_price,
            filled_qty=tracked_fill_qty,
            decision=decision,
            risk_amount_usd=Decimal(str(decision.planned_risk_usd or 0)),
            stop_loss_price=Decimal(str(decision.execution_plan.stop_price if decision.execution_plan and decision.execution_plan.stop_price is not None else tracked_fill_price)),
            take_profit_price=Decimal(str(decision.execution_plan.take_profit_price if decision.execution_plan and decision.execution_plan.take_profit_price is not None else tracked_fill_price)),
            initial_r_distance=max(
                tracked_fill_price - Decimal(str(decision.execution_plan.stop_price)),
                Decimal("0.01"),
            )
            if decision.execution_plan and decision.execution_plan.stop_price is not None
            else Decimal("0.01"),
            entry_spread_bps=closed_candle.spread_bps,
            entry_packet_summary={
                "indicator_snapshot": context_packet.get("indicator_snapshot", {}),
                "timeframes": context_packet.get("timeframes", {}),
            },
            followed_lessons=list(context_packet.get("feedback", {}).get("avoid", []))
            + list(context_packet.get("feedback", {}).get("reinforce", [])),
        )
        return "entry_opened"

    if position_tracker.has_position():
        position_tracker.sync_with_account(
            qty=account_snapshot.open_position_qty,
            avg_entry_price=account_snapshot.avg_entry_price,
        )
    completed_trade = position_tracker.apply_sell_fill(
        fill_price=filled_price,
        filled_qty=filled_qty,
        decision=decision,
    )
    if position_tracker.has_position() and after_account_snapshot.open_position_qty > 0:
        position_tracker.sync_with_account(
            qty=after_account_snapshot.open_position_qty,
            avg_entry_price=after_account_snapshot.avg_entry_price,
        )
    if decision.action == "reduce":
        session_stats["partial_exits"] += 1
    else:
        session_stats["full_exits"] += 1

    if completed_trade is None:
        return review.outcome

    reflection = build_trade_reflection(
        completed_trade,
        closed_at=(update.timestamp if update is not None and update.timestamp is not None else closed_candle.end_at),
        exit_price=filled_price,
        exit_reason=decision_source if decision_source != "llm" else "llm_exit",
        spread_bps_exit=closed_candle.spread_bps,
    )
    reflections.append(reflection)
    reflection_journal.record(
        {
            "record_type": "trade_reflection",
            "reflection": reflection.model_dump(mode="json"),
        }
    )
    new_lessons = derive_lessons(reflection)
    lessons.extend(new_lessons)
    if store is not None:
        store.upsert_lessons(new_lessons, policy_version_id=policy_id)
    return review.outcome


def _order_request_from_decision(
    *,
    decision: TradeDecision,
    symbol: str,
    account_snapshot,
    risk_allowed_notional: Decimal,
) -> OrderRequest | None:
    if decision.action == "buy":
        if risk_allowed_notional <= 0:
            return None
        return OrderRequest(
            symbol=symbol,
            side="buy",
            type="market",
            time_in_force="gtc",
            notional=risk_allowed_notional,
        )

    if account_snapshot.open_position_qty <= 0:
        return None

    if decision.action == "reduce":
        reduce_fraction = Decimal(str(decision.reduce_fraction or 0))
        qty = _normalize_qty(account_snapshot.open_position_qty * reduce_fraction)
        if qty <= 0:
            return None
        return OrderRequest(
            symbol=symbol,
            side="sell",
            type="market",
            time_in_force="gtc",
            qty=qty,
        )

    if decision.action == "exit":
        qty = _normalize_qty(account_snapshot.open_position_qty)
        if qty <= 0:
            return None
        return OrderRequest(
            symbol=symbol,
            side="sell",
            type="market",
            time_in_force="gtc",
            qty=qty,
        )
    return None


def _normalize_qty(qty: Decimal) -> Decimal:
    return qty.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)


async def _fetch_recent_candles(
    history_service: AlpacaHistoricalCryptoService,
    symbol: str,
    *,
    minutes: int,
) -> list[LiveCandle]:
    now = datetime.now(timezone.utc)
    bars = await history_service.fetch_bars(
        symbol=symbol,
        timeframe="1Min",
        location="us",
        start=now - timedelta(minutes=max(5, minutes)),
        end=now,
        limit=max(10, minutes),
    )
    current_minute = now.replace(second=0, microsecond=0)
    return [_live_candle_from_bar(bar) for bar in bars if bar.timestamp < current_minute]


def _live_candle_from_bar(bar: HistoricalBar) -> LiveCandle:
    total_range = bar.high_price - bar.low_price
    body = abs(bar.close_price - bar.open_price)
    upper_wick = max(bar.high_price - max(bar.open_price, bar.close_price), Decimal("0"))
    lower_wick = max(min(bar.open_price, bar.close_price) - bar.low_price, Decimal("0"))
    close_range_position = 0.5
    if total_range > 0:
        close_range_position = float((bar.close_price - bar.low_price) / total_range)
    return LiveCandle(
        symbol=bar.symbol,
        start_at=bar.timestamp,
        end_at=bar.timestamp + timedelta(minutes=1),
        open_price=bar.open_price,
        high_price=bar.high_price,
        low_price=bar.low_price,
        close_price=bar.close_price,
        volume=bar.volume,
        trade_count=bar.trade_count,
        vwap=bar.vwap,
        spread_bps=None,
        body_pct=(float(body / total_range) if total_range > 0 else 0.0),
        upper_wick_pct=(float(upper_wick / total_range) if total_range > 0 else 0.0),
        lower_wick_pct=(float(lower_wick / total_range) if total_range > 0 else 0.0),
        close_range_position=close_range_position,
    )


def _stale_age_seconds(last_processed_candle_at: datetime | None, *, now: datetime) -> float | None:
    if last_processed_candle_at is None:
        return None
    return max(0.0, (now - last_processed_candle_at).total_seconds())


def _bootstrap_existing_position(
    *,
    position_tracker: PositionTracker,
    risk_policy,
    candles,
    account_snapshot,
    opened_at: datetime,
) -> None:
    entry_price = account_snapshot.avg_entry_price
    stop_distance = risk_policy._stop_distance(candles, entry_price)
    position_tracker.bootstrap_from_account(
        opened_at=opened_at,
        symbol="ETH/USD",
        entry_price=entry_price,
        qty=account_snapshot.open_position_qty,
        stop_loss_price=entry_price - stop_distance,
        take_profit_price=entry_price + stop_distance,
        initial_r_distance=stop_distance,
    )


def _filled_price(update, order, default_price: Decimal) -> Decimal | None:
    if update is not None and update.price is not None:
        return update.price
    if update is not None and update.order.filled_avg_price is not None:
        return update.order.filled_avg_price
    if order.filled_avg_price is not None:
        return order.filled_avg_price
    return default_price


def _filled_qty(update, order, request: OrderRequest) -> Decimal | None:
    if update is not None and update.qty is not None:
        return update.qty
    if update is not None and update.order.filled_qty is not None:
        return update.order.filled_qty
    if order.filled_qty is not None:
        return order.filled_qty
    return request.qty


def _synthetic_trade_update(order) -> object:
    from data.schemas import TradeUpdate

    event = "partial_fill"
    if order.status in {"filled", "partially_filled"}:
        event = "fill" if order.status == "filled" else "partial_fill"
    return TradeUpdate(
        event=event,
        order=order,
        timestamp=order.updated_at,
        price=order.filled_avg_price,
        qty=order.filled_qty,
    )


def _build_summary(
    *,
    session_tag: str,
    session_stats: dict[str, object],
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
) -> dict[str, object]:
    realized_pnl = sum((reflection.realized_pnl_usd for reflection in reflections), Decimal("0"))
    average_r = (
        sum(reflection.realized_r for reflection in reflections) / len(reflections)
        if reflections
        else 0.0
    )
    wins = sum(1 for reflection in reflections if reflection.realized_pnl_usd > 0)
    losses = sum(1 for reflection in reflections if reflection.realized_pnl_usd < 0)
    avoid = [lesson.message for lesson in lessons if lesson.metadata.get("polarity") == "avoid"][-3:]
    reinforce = [lesson.message for lesson in lessons if lesson.metadata.get("polarity") == "reinforce"][-3:]
    return {
        "session_tag": session_tag,
        "policy_label": session_stats["policy_label"],
        "total_decisions": session_stats["total_decisions"],
        "llm_decisions": session_stats["llm_decisions"],
        "deterministic_decisions": session_stats["deterministic_decisions"],
        "executable_decisions": session_stats["executable_decisions"],
        "entries": session_stats["entries"],
        "partial_exits": session_stats["partial_exits"],
        "full_exits": session_stats["full_exits"],
        "fills": session_stats["fills"],
        "rejections": session_stats["rejections"],
        "position_reconciliations": session_stats["position_reconciliations"],
        "market_stream_reconnects": session_stats["market_stream_reconnects"],
        "realized_pnl_usd": float(realized_pnl),
        "average_realized_r": average_r,
        "win_count": wins,
        "loss_count": losses,
        "risk_rejection_reasons": dict(session_stats["risk_rejection_reasons"]),
        "top_avoid_lessons": avoid,
        "top_reinforce_lessons": reinforce,
    }


def _write_session_artifacts(
    *,
    artifact_dir: Path,
    summary: dict[str, object],
    reflections: list[TradeReflection],
) -> None:
    (artifact_dir / "session_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = [
        f"Session Tag: {summary['session_tag']}",
        f"Policy: {summary['policy_label']}",
        f"Total decisions: {summary['total_decisions']}",
        f"LLM decisions: {summary['llm_decisions']}",
        f"Deterministic decisions: {summary['deterministic_decisions']}",
        f"Executable decisions: {summary['executable_decisions']}",
        f"Entries: {summary['entries']}",
        f"Partial exits: {summary['partial_exits']}",
        f"Full exits: {summary['full_exits']}",
        f"Fills: {summary['fills']}",
        f"Position reconciliations: {summary['position_reconciliations']}",
        f"Market stream reconnects: {summary['market_stream_reconnects']}",
        f"Realized PnL USD: {summary['realized_pnl_usd']}",
        f"Average realized R: {summary['average_realized_r']}",
        f"Wins/Losses: {summary['win_count']}/{summary['loss_count']}",
        f"Risk rejection reasons: {summary['risk_rejection_reasons']}",
        f"Top avoid lessons: {summary['top_avoid_lessons']}",
        f"Top reinforce lessons: {summary['top_reinforce_lessons']}",
        f"Closed reflections: {len(reflections)}",
    ]
    (artifact_dir / "session_report.md").write_text("\n".join(report), encoding="utf-8")


def main() -> None:
    asyncio.run(run())
