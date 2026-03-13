from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import uvicorn

from agents.mt5_entry_analyst import EntryAnalysisResult, MT5EntryAnalystAgent
from agents.mt5_position_manager import MT5PositionManagerAgent, PositionManagementResult
from app.config import Settings, get_settings
from brokers.mt5 import MT5BridgeState, create_mt5_bridge_app
from data.schemas import BridgeCommand, BridgeSnapshot, EntryDecision, LessonRecord, ManagementDecision, TicketState, TradeReflection
from execution.mt5_entry_planner import MT5EntryPlanner
from execution.mt5_ticket_book import MT5TicketBook
from feedback.reflection import build_ticket_reflection, derive_lessons
from infra.logging import configure_logging, get_logger
from memory.journal import Journal
from memory.supabase import SupabaseStore
from risk.mt5_v5_policy import MT5RiskPostureEngine, MT5V5RiskArbiter
from runtime.mt5_context_packet import MT5ContextBuilder


def _safe_store_call(logger, operation: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        logger.error("v5_mt5_store_error operation=%s error=%s", operation, exc)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the V5 MT5 demo-paper orchestrator.")
    parser.add_argument("--agent-name", default="mt5_primary")
    parser.add_argument("--duration-minutes", type=int, default=0)
    parser.add_argument("--session-tag", default=None)
    parser.add_argument("--enable-trade-commands", action="store_true")
    parser.add_argument("--bridge-host", default=None)
    parser.add_argument("--bridge-port", type=int, default=None)
    return parser.parse_args()


async def run() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for the V5 MT5 runtime.")

    session_tag = args.session_tag or datetime.now(timezone.utc).strftime("v5-mt5-%Y%m%d-%H%M%S")
    artifact_dir = Path("var/v5") / session_tag
    artifact_dir.mkdir(parents=True, exist_ok=True)
    event_journal = Journal(str(artifact_dir / "events.jsonl"))
    reflection_journal = Journal(str(artifact_dir / "trade_reflections.jsonl"))
    store = SupabaseStore(settings.supabase_db_dsn) if settings.supabase_db_dsn is not None else None

    bridge_state = MT5BridgeState(settings.mt5_bridge_id)
    bridge_app = create_mt5_bridge_app(bridge_state, journal=event_journal, store=store, agent_name=args.agent_name)
    bridge_server, bridge_task = await _start_bridge_server(
        app=bridge_app,
        host=args.bridge_host or settings.mt5_bridge_host,
        port=args.bridge_port or settings.mt5_bridge_port,
    )

    entry_agent = MT5EntryAnalystAgent(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    manager_agent = MT5PositionManagerAgent(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        base_url=settings.openai_base_url,
    )
    ticket_book = MT5TicketBook()
    planner = MT5EntryPlanner()
    posture_engine = MT5RiskPostureEngine()
    risk_arbiter = MT5V5RiskArbiter(
        symbol=settings.mt5_symbol,
        account_mode=settings.mt5_account_mode,
        decision_window_seconds=settings.mt5_entry_timeout_seconds,
        daily_loss_pct=settings.max_daily_loss_pct,
    )
    context_builder = MT5ContextBuilder()
    commands_enabled = args.enable_trade_commands or settings.mt5_enable_trade_commands
    shadow_mode = settings.mt5_shadow_mode or not commands_enabled
    if args.enable_trade_commands:
        shadow_mode = False

    reflections: list[TradeReflection] = []
    lessons: list[LessonRecord] = []
    last_entry_bar_end: datetime | None = None
    last_manager_run_at: datetime | None = None
    last_manager_signature = ""
    last_half_r_buckets: dict[str, float] = {}
    new_entries_this_bar = 0
    end_at = None
    if args.duration_minutes > 0:
        end_at = datetime.now(timezone.utc) + timedelta(minutes=args.duration_minutes)

    logger.info(
        "v5_mt5_start session_tag=%s symbol=%s bridge=%s:%s shadow_mode=%s",
        session_tag,
        settings.mt5_symbol,
        args.bridge_host or settings.mt5_bridge_host,
        args.bridge_port or settings.mt5_bridge_port,
        shadow_mode,
    )

    try:
        while end_at is None or datetime.now(timezone.utc) < end_at:
            try:
                await bridge_state.wait_for_snapshot(timeout=1.0)
            except TimeoutError:
                continue

            snapshot = await bridge_state.latest_snapshot()
            if snapshot is None:
                continue

            sync_result = ticket_book.sync(snapshot.open_tickets)
            for closed_ticket in sync_result.closed:
                reflection = build_ticket_reflection(
                    closed_ticket,
                    closed_at=snapshot.server_time,
                    exit_price=closed_ticket.current_price or snapshot.midpoint,
                    exit_reason="snapshot_flat",
                    spread_bps_exit=snapshot.spread_bps,
                )
                reflections.append(reflection)
                new_lessons = derive_lessons(reflection)
                lessons.extend(new_lessons)
                reflection_journal.record(
                    {
                        "record_type": "mt5_trade_reflection",
                        "agent_name": args.agent_name,
                        "reflection": reflection.model_dump(mode="json"),
                        "lessons": [lesson.model_dump(mode="json") for lesson in new_lessons],
                    }
                )
                if store is not None:
                    _safe_store_call(
                        logger,
                        "insert_mt5_trade_reflection",
                        store.insert_mt5_trade_reflection,
                        agent_name=args.agent_name,
                        reflection=reflection,
                        ticket_id=closed_ticket.ticket_id,
                        basket_id=closed_ticket.basket_id,
                        risk_posture=(closed_ticket.metadata.get("risk_posture") if closed_ticket.metadata else None),
                    )
                    _safe_store_call(logger, "upsert_lessons", store.upsert_lessons, new_lessons)

            current_bar_end = snapshot.bars_5m[-1].end_at if snapshot.bars_5m else None
            if current_bar_end != last_entry_bar_end:
                new_entries_this_bar = 0

            if current_bar_end is not None and current_bar_end != last_entry_bar_end:
                new_entries_this_bar = await _run_entry_cycle(
                    snapshot=snapshot,
                    agent_name=args.agent_name,
                    event_journal=event_journal,
                    store=store,
                    entry_agent=entry_agent,
                    ticket_book=ticket_book,
                    planner=planner,
                    risk_arbiter=risk_arbiter,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    bridge_state=bridge_state,
                    reflections=reflections,
                    lessons=lessons,
                    shadow_mode=shadow_mode,
                    logger=logger,
                    entries_this_bar=new_entries_this_bar,
                    timeout_seconds=settings.mt5_entry_timeout_seconds,
                )
                last_entry_bar_end = current_bar_end

            should_run_manager = _should_run_manager(
                snapshot=snapshot,
                ticket_book=ticket_book,
                last_manager_run_at=last_manager_run_at,
                last_signature=last_manager_signature,
                last_half_r_buckets=last_half_r_buckets,
                manager_sweep_seconds=settings.mt5_manager_sweep_seconds,
            )
            if should_run_manager:
                manager_signature, half_r_buckets = await _run_manager_cycle(
                    snapshot=snapshot,
                    agent_name=args.agent_name,
                    event_journal=event_journal,
                    store=store,
                    manager_agent=manager_agent,
                    ticket_book=ticket_book,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    bridge_state=bridge_state,
                    planner=planner,
                    reflections=reflections,
                    lessons=lessons,
                    shadow_mode=shadow_mode,
                    logger=logger,
                )
                last_manager_run_at = snapshot.server_time
                last_manager_signature = manager_signature
                last_half_r_buckets = half_r_buckets
    finally:
        bridge_server.should_exit = True
        await bridge_task


async def _run_entry_cycle(
    *,
    snapshot: BridgeSnapshot,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseStore | None,
    entry_agent: MT5EntryAnalystAgent,
    ticket_book: MT5TicketBook,
    planner: MT5EntryPlanner,
    risk_arbiter: MT5V5RiskArbiter,
    context_builder: MT5ContextBuilder,
    posture_engine: MT5RiskPostureEngine,
    bridge_state: MT5BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
    logger,
    entries_this_bar: int,
    timeout_seconds: int,
) -> int:
    risk_posture, multiplier = posture_engine.derive(reflections)
    packet = context_builder.build_entry_packet(
        snapshot=snapshot,
        ticket_book=ticket_book,
        risk_posture=risk_posture,
        reflections=reflections,
        lessons=lessons,
    )
    if snapshot.bars_5m:
        packet["context_signature"] = packet.get("context_signature")
    deadline = snapshot.bars_5m[-1].end_at + timedelta(seconds=timeout_seconds)
    remaining_seconds = max(0.1, (deadline - snapshot.server_time).total_seconds())
    try:
        result = await asyncio.wait_for(entry_agent.analyze(packet), timeout=remaining_seconds)
    except asyncio.TimeoutError:
        result = EntryAnalysisResult(
            decision=EntryDecision(action="hold", confidence=0.0, rationale="Entry analyst timed out.", thesis_tags=[]),
            prompt="",
            raw_response="",
            latency_ms=int(remaining_seconds * 1000),
        )

    risk_decision = risk_arbiter.evaluate_entry(
        decision=result.decision,
        snapshot=snapshot,
        ticket_book=ticket_book,
        risk_posture=risk_posture,
        risk_multiplier=multiplier,
        pending_symbol_command=await bridge_state.has_pending_symbol(snapshot.symbol),
        new_entries_this_bar=entries_this_bar,
    )
    event_journal.record(
        {
            "record_type": "mt5_entry_decision",
            "agent_name": agent_name,
            "context": packet,
            "decision": result.decision.model_dump(mode="json"),
            "risk_decision": risk_decision.model_dump(mode="json"),
            "raw_llm_response": result.raw_response,
        }
    )
    if store is not None:
        _safe_store_call(
            logger,
            "insert_mt5_runtime_decision_entry",
            store.insert_mt5_runtime_decision,
            agent_name=agent_name,
            decision_kind="entry",
            symbol=snapshot.symbol,
            action=result.decision.action,
            confidence=result.decision.confidence,
            rationale=result.decision.rationale,
            risk_posture=risk_posture,
            risk_approved=risk_decision.approved,
            risk_reason=risk_decision.reason,
            context_payload=packet,
            decision_payload={
                "decision": result.decision.model_dump(mode="json"),
                "raw_response": result.raw_response,
                "prompt_version": entry_agent.prompt_version,
            },
        )

    if not risk_decision.approved or result.decision.action == "hold":
        return entries_this_bar

    side = "long" if result.decision.action == "enter_long" else "short"
    ticket_sequence = ticket_book.ticket_count(snapshot.symbol, side) + 1
    plan = planner.plan_entry(
        decision=result.decision,
        snapshot=snapshot,
        risk_decision=risk_decision,
        existing_basket_id=ticket_book.same_direction_basket_id(snapshot.symbol, side),
        ticket_sequence=ticket_sequence,
    )
    if plan is None:
        logger.info("v5_entry_skipped reason=planner_returned_none symbol=%s", snapshot.symbol)
        return entries_this_bar

    command = planner.build_entry_command(
        plan=plan,
        reason=result.decision.rationale,
        created_at=snapshot.server_time,
        expires_at=deadline,
        thesis_tags=result.decision.thesis_tags,
    )
    if shadow_mode:
        event_journal.record(
            {
                "record_type": "mt5_shadow_command",
                "agent_name": agent_name,
                "command": command.model_dump(mode="json"),
            }
        )
        return entries_this_bar

    await bridge_state.queue_command(command)
    event_journal.record(
        {
            "record_type": "mt5_bridge_command_enqueued",
            "agent_name": agent_name,
            "command": command.model_dump(mode="json"),
        }
    )
    if store is not None:
        _safe_store_call(
            logger,
            "insert_mt5_bridge_command_entry",
            store.insert_mt5_bridge_command,
            agent_name=agent_name,
            command=command,
        )
    return entries_this_bar + 1


async def _run_manager_cycle(
    *,
    snapshot: BridgeSnapshot,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseStore | None,
    manager_agent: MT5PositionManagerAgent,
    ticket_book: MT5TicketBook,
    context_builder: MT5ContextBuilder,
    posture_engine: MT5RiskPostureEngine,
    bridge_state: MT5BridgeState,
    planner: MT5EntryPlanner,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
    logger,
) -> tuple[str, dict[str, float]]:
    del planner
    risk_posture, _ = posture_engine.derive(reflections)
    atr_pips = context_builder._timeframe_summary(snapshot.bars_5m, label="5m").get("atr_14_pips", 0.0)
    allowed_actions = {
        ticket.ticket_id: ticket_book.allowed_actions(ticket.ticket_id, atr_pips=float(atr_pips))
        for ticket in ticket_book.all(snapshot.symbol)
    }
    packet = context_builder.build_manager_packet(
        snapshot=snapshot,
        ticket_book=ticket_book,
        allowed_actions=allowed_actions,
        risk_posture=risk_posture,
        reflections=reflections,
        lessons=lessons,
    )
    result = await manager_agent.analyze(packet)
    pending_symbol_command = await bridge_state.has_pending_symbol(snapshot.symbol)
    for decision in result.decision_batch.decisions:
        ticket = ticket_book.by_ticket_id(decision.ticket_id)
        if ticket is None:
            continue
        allowed = allowed_actions.get(ticket.ticket_id, ["hold"])
        risk_approved = decision.action in allowed and not pending_symbol_command
        risk_reason = "Management action approved." if risk_approved else "Management action is not allowed in the current state."
        event_journal.record(
            {
                "record_type": "mt5_management_decision",
                "agent_name": agent_name,
                "decision": decision.model_dump(mode="json"),
                "allowed_actions": allowed,
                "risk_approved": risk_approved,
                "risk_reason": risk_reason,
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_runtime_decision_management",
                store.insert_mt5_runtime_decision,
                agent_name=agent_name,
                decision_kind="management",
                symbol=snapshot.symbol,
                action=decision.action,
                confidence=decision.confidence,
                rationale=decision.rationale,
                risk_posture=risk_posture,
                risk_approved=risk_approved,
                risk_reason=risk_reason,
                context_payload=packet,
                decision_payload=decision.model_dump(mode="json"),
            )
        if not risk_approved or decision.action == "hold":
            continue

        command = _management_command_from_decision(decision=decision, ticket=ticket, snapshot=snapshot)
        if command is None:
            continue
        if shadow_mode:
            event_journal.record(
                {
                    "record_type": "mt5_shadow_management_command",
                    "agent_name": agent_name,
                    "command": command.model_dump(mode="json"),
                }
            )
            continue
        await bridge_state.queue_command(command)
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_bridge_command_management",
                store.insert_mt5_bridge_command,
                agent_name=agent_name,
                command=command,
            )
    return ticket_book.signature(), ticket_book.half_r_buckets()


def _management_command_from_decision(
    *,
    decision: ManagementDecision,
    ticket: TicketState,
    snapshot: BridgeSnapshot,
) -> BridgeCommand | None:
    created_at = snapshot.server_time
    expires_at = created_at + timedelta(seconds=60)
    if decision.action == "close_ticket":
        return BridgeCommand(
            command_id=f"close-{ticket.ticket_id}-{int(created_at.timestamp())}",
            command_type="close_ticket",
            symbol=ticket.symbol,
            created_at=created_at,
            expires_at=expires_at,
            ticket_id=ticket.ticket_id,
            basket_id=ticket.basket_id,
            volume_lots=ticket.volume_lots,
            reason=decision.rationale,
            metadata={"action": decision.action},
        )
    if decision.action == "take_partial_50":
        half_volume = _round_lots(ticket.volume_lots / Decimal("2"))
        if half_volume <= 0:
            return None
        return BridgeCommand(
            command_id=f"partial-{ticket.ticket_id}-{int(created_at.timestamp())}",
            command_type="close_ticket",
            symbol=ticket.symbol,
            created_at=created_at,
            expires_at=expires_at,
            ticket_id=ticket.ticket_id,
            basket_id=ticket.basket_id,
            volume_lots=half_volume,
            reason=decision.rationale,
            metadata={"action": decision.action},
        )
    if decision.action == "move_stop_to_breakeven":
        stop_loss = ticket.open_price
    elif decision.action == "trail_stop_to_rule":
        atr_distance = Decimal("0.0001") * Decimal("0.75") * Decimal(str(max(1.0, _atr_14_pips(snapshot))))
        current_price = ticket.current_price or (snapshot.bid if ticket.side == "long" else snapshot.ask)
        if ticket.side == "long":
            stop_loss = max(ticket.open_price, current_price - atr_distance)
        else:
            stop_loss = min(ticket.open_price, current_price + atr_distance)
    else:
        return None

    if ticket.side == "long" and ticket.stop_loss is not None and stop_loss < ticket.stop_loss:
        return None
    if ticket.side == "short" and ticket.stop_loss is not None and stop_loss > ticket.stop_loss:
        return None

    return BridgeCommand(
        command_id=f"modify-{ticket.ticket_id}-{int(created_at.timestamp())}",
        command_type="modify_ticket",
        symbol=ticket.symbol,
        created_at=created_at,
        expires_at=expires_at,
        ticket_id=ticket.ticket_id,
        basket_id=ticket.basket_id,
        stop_loss=stop_loss,
        take_profit=ticket.take_profit,
        reason=decision.rationale,
        metadata={"action": decision.action},
    )


def _should_run_manager(
    *,
    snapshot: BridgeSnapshot,
    ticket_book: MT5TicketBook,
    last_manager_run_at: datetime | None,
    last_signature: str,
    last_half_r_buckets: dict[str, float],
    manager_sweep_seconds: int,
) -> bool:
    if not ticket_book.all(snapshot.symbol):
        return False
    if ticket_book.signature() != last_signature:
        return True
    if ticket_book.half_r_buckets() != last_half_r_buckets:
        return True
    if last_manager_run_at is None:
        return True
    return snapshot.server_time >= last_manager_run_at + timedelta(seconds=manager_sweep_seconds)


def _atr_14_pips(snapshot: BridgeSnapshot) -> float:
    bars = snapshot.bars_5m[-14:]
    if len(bars) < 2:
        return 6.0
    true_ranges: list[float] = []
    previous_close = float(bars[0].close_price)
    for bar in bars[1:]:
        high = float(bar.high_price)
        low = float(bar.low_price)
        true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = float(bar.close_price)
    atr_price = sum(true_ranges) / len(true_ranges) if true_ranges else 0.0006
    return max(atr_price * 10000.0, 6.0)


def _round_lots(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


async def _start_bridge_server(*, app, host: str, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.05)
    return server, task


def main() -> None:
    asyncio.run(run())
