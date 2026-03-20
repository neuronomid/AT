from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

from agents.mt5_v60_entry_analyst import MT5V60EntryAnalystAgent
from agents.mt5_v60_position_manager import MT5V60PositionManagerAgent
from app.v6_0_mt5 import (
    _latest_entry_bar_end,
    _process_acks,
    _record_closed_tickets,
    _run_deterministic_management_cycle,
    _run_entry_cycle,
    _run_entry_protection_cycle,
    _run_fast_entry_cycle,
    _run_manager_cycle,
    _should_trigger_stop_loss_reversal,
    _sync_screenshot_state,
)
from app.v6_1_config import V61Settings, get_v61_settings
from brokers.mt5_v60 import MT5V60BridgeState, create_mt5_v60_bridge_app
from data.mt5_v60_schemas import MT5V60BridgeSnapshot, MT5V60ScreenshotState
from data.schemas import LessonRecord, TradeReflection
from execution.mt5_v60_entry_planner import MT5V60EntryPlanner
from execution.mt5_v60_immediate_entry import MT5V60ImmediateEntryBuilder
from execution.mt5_v60_ticket_registry import MT5V60TicketRegistry
from infra.logging import configure_logging, get_logger
from memory.journal import Journal
from memory.supabase_mt5_v60 import SupabaseMT5V60Store
from risk.mt5_v60_policy import MT5V60RiskArbiter, MT5V60RiskPostureEngine
from runtime.mt5_v60_context_packet import MT5V60ContextBuilder
from runtime.mt5_v60_symbols import normalize_mt5_v60_symbol


@dataclass
class V61SymbolRuntimeState:
    symbol: str
    risk_arbiter: MT5V60RiskArbiter
    reflections: list[TradeReflection]
    lessons: list[LessonRecord]
    context_builder: MT5V60ContextBuilder = field(default_factory=MT5V60ContextBuilder)
    screenshot_state: MT5V60ScreenshotState = field(default_factory=MT5V60ScreenshotState)
    last_entry_bar_end: datetime | None = None
    last_manager_run_at: datetime | None = None
    last_fast_entry_key: str | None = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the V6.1 MT5 multi-symbol demo-paper orchestrator.")
    parser.add_argument("--agent-name", default=None)
    parser.add_argument("--duration-minutes", type=int, default=0)
    parser.add_argument("--session-tag", default=None)
    parser.add_argument("--enable-trade-commands", action="store_true")
    parser.add_argument("--shadow-mode", action="store_true")
    parser.add_argument("--bridge-host", default=None)
    parser.add_argument("--bridge-port", type=int, default=None)
    return parser.parse_args()


async def _start_bridge_server(*, app, host: str, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.05)
    return server, task


def _build_symbol_state(
    *,
    symbol: str,
    settings: V61Settings,
    store: SupabaseMT5V60Store | None,
    seeded_lessons: list[LessonRecord],
) -> V61SymbolRuntimeState:
    recent_entry_times = (
        store.list_recent_approved_entry_times(
            symbol=symbol,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        if store is not None
        else []
    )
    reflections = store.list_recent_trade_reflections(symbol=symbol, limit=10) if store is not None else []
    risk_arbiter = MT5V60RiskArbiter(
        symbol=symbol,
        account_mode=settings.v61_mt5_account_mode,
        min_confidence=settings.v61_min_decision_confidence,
        max_spread_bps=settings.v61_max_spread_bps,
        stale_after_seconds=settings.v61_stale_after_seconds,
        min_risk_fraction=settings.v61_min_risk_fraction,
        max_risk_fraction=settings.v61_max_risk_fraction,
        daily_loss_pct=settings.v61_max_daily_loss_pct,
        max_trades_per_hour=settings.v61_max_trades_per_hour,
        seeded_entry_times=recent_entry_times,
    )
    return V61SymbolRuntimeState(
        symbol=symbol,
        risk_arbiter=risk_arbiter,
        reflections=reflections,
        lessons=list(seeded_lessons),
    )


async def _shutdown_flatten_open_tickets(
    *,
    settings: V61Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V60Store | None,
    registry: MT5V60TicketRegistry,
    bridge_state: MT5V60BridgeState,
    shadow_mode: bool,
    logger,
) -> None:
    if shadow_mode:
        return
    snapshots_by_symbol = await bridge_state.latest_snapshots()
    if not snapshots_by_symbol:
        return
    planner = MT5V60EntryPlanner()
    for snapshot in snapshots_by_symbol.values():
        for ticket in registry.all(snapshot.symbol):
            command = planner.build_close_command(
                ticket=ticket,
                volume_lots=ticket.current_volume_lots,
                reason="Timed V6.1 MT5 demo session shutdown flatten.",
                created_at=snapshot.server_time,
                expires_at=snapshot.server_time + timedelta(seconds=30),
                metadata={"action": "shutdown_flatten"},
            )
            if command is None:
                continue
            await bridge_state.queue_command(command)
            event_journal.record(
                {
                    "record_type": "mt5_v60_bridge_command_enqueued",
                    "agent_name": agent_name,
                    "command_source": "shutdown_flatten",
                    "command": command.model_dump(mode="json"),
                }
            )
            if store is not None:
                try:
                    store.insert_mt5_v60_bridge_command(
                        agent_name=agent_name,
                        command=command,
                        bridge_id=settings.v61_bridge_id,
                    )
                except Exception as exc:
                    logger.error("v6_1_shutdown_flatten_store_error symbol=%s error=%s", snapshot.symbol, exc)


async def run() -> None:
    args = _parse_args()
    settings = get_v61_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.openai_api_key is None:
        raise RuntimeError("V61_OPENAI_API_KEY is required for the V6.1 MT5 runtime.")

    session_tag = args.session_tag or datetime.now(timezone.utc).strftime("v6-1-mt5-%Y%m%d-%H%M%S")
    artifact_dir = Path("var/v6_1") / session_tag
    artifact_dir.mkdir(parents=True, exist_ok=True)
    event_journal = Journal(str(artifact_dir / "events.jsonl"))
    reflection_journal = Journal(str(artifact_dir / "trade_reflections.jsonl"))
    store = SupabaseMT5V60Store(settings.supabase_db_dsn) if settings.supabase_db_dsn is not None else None
    agent_name = args.agent_name or settings.v61_agent_name
    host = args.bridge_host or settings.v61_bridge_host
    port = args.bridge_port or settings.v61_bridge_port

    bridge_state = MT5V60BridgeState(settings.v61_bridge_id)
    bridge_app = create_mt5_v60_bridge_app(
        bridge_state,
        journal=event_journal,
        store=store,
        agent_name=agent_name,
    )
    bridge_server, bridge_task = await _start_bridge_server(app=bridge_app, host=host, port=port)

    entry_agent = MT5V60EntryAnalystAgent(
        api_key=settings.openai_api_key,
        model=settings.v61_openai_model,
        base_url=settings.v61_openai_base_url,
        reasoning_effort=settings.v61_entry_reasoning_effort,
    )
    manager_agent = MT5V60PositionManagerAgent(
        api_key=settings.openai_api_key,
        model=settings.v61_openai_model,
        base_url=settings.v61_openai_base_url,
        reasoning_effort=settings.manager_reasoning_effort,
    )
    planner = MT5V60EntryPlanner()
    entry_builder = MT5V60ImmediateEntryBuilder()
    posture_engine = MT5V60RiskPostureEngine()
    registry = MT5V60TicketRegistry(store=store)
    if store is not None:
        registry.seed(store.list_open_ticket_states())
    seeded_lessons = store.list_recent_lessons(limit=20) if store is not None else []
    symbol_states: dict[str, V61SymbolRuntimeState] = {}

    commands_enabled = args.enable_trade_commands or settings.v61_mt5_enable_trade_commands
    shadow_mode = settings.v61_mt5_shadow_mode or not commands_enabled
    if args.enable_trade_commands:
        shadow_mode = False
    if args.shadow_mode:
        shadow_mode = True
    end_at = datetime.now(timezone.utc) + timedelta(minutes=args.duration_minutes) if args.duration_minutes > 0 else None

    logger.info(
        "v6_1_mt5_start session_tag=%s bridge=%s:%s shadow_mode=%s symbols=dynamic",
        session_tag,
        host,
        port,
        shadow_mode,
    )

    try:
        while end_at is None or datetime.now(timezone.utc) < end_at:
            snapshot_event: MT5V60BridgeSnapshot | None = None
            updated_symbols: set[str] = set()
            try:
                snapshot_event = await bridge_state.wait_for_snapshot(timeout=1.0)
                updated_symbol = normalize_mt5_v60_symbol(snapshot_event.symbol)
                if updated_symbol:
                    updated_symbols.add(updated_symbol)
            except TimeoutError:
                pass

            await _process_acks(bridge_state=bridge_state, registry=registry)
            latest_by_symbol = await bridge_state.latest_snapshots()
            if snapshot_event is not None:
                latest_by_symbol[normalize_mt5_v60_symbol(snapshot_event.symbol)] = snapshot_event
            if not latest_by_symbol:
                continue

            ordered_symbols: list[str] = []
            if snapshot_event is not None:
                prioritized = normalize_mt5_v60_symbol(snapshot_event.symbol)
                if prioritized:
                    ordered_symbols.append(prioritized)
            ordered_symbols.extend(sorted(symbol for symbol in latest_by_symbol if symbol and symbol not in ordered_symbols))

            for normalized_symbol in ordered_symbols:
                snapshot = latest_by_symbol[normalized_symbol]
                state = symbol_states.get(normalized_symbol)
                if state is None:
                    state = _build_symbol_state(
                        symbol=snapshot.symbol,
                        settings=settings,
                        store=store,
                        seeded_lessons=seeded_lessons,
                    )
                    symbol_states[normalized_symbol] = state

                state.context_builder.observe_snapshot(snapshot)
                state.screenshot_state = _sync_screenshot_state(
                    snapshot=snapshot,
                    settings=settings,
                    current=state.screenshot_state,
                )
                sync_result = registry.sync(snapshot, scope_symbol=snapshot.symbol)
                _record_closed_tickets(
                    closed_tickets=sync_result.closed,
                    agent_name=agent_name,
                    reflection_journal=reflection_journal,
                    store=store,
                    reflections=state.reflections,
                    lessons=state.lessons,
                    logger=logger,
                )

                reversal_executed = False
                if sync_result.closed and not registry.has_open_position(snapshot.symbol) and not await bridge_state.has_pending_symbol(snapshot.symbol):
                    for closed_ticket in sync_result.closed:
                        if not _should_trigger_stop_loss_reversal(closed_ticket):
                            continue
                        reversal_executed = await _run_entry_cycle(
                            snapshot=snapshot,
                            settings=settings,
                            agent_name=agent_name,
                            event_journal=event_journal,
                            store=store,
                            registry=registry,
                            entry_builder=entry_builder,
                            risk_arbiter=state.risk_arbiter,
                            context_builder=state.context_builder,
                            posture_engine=posture_engine,
                            bridge_state=bridge_state,
                            reflections=state.reflections,
                            lessons=state.lessons,
                            screenshot_state=state.screenshot_state,
                            entry_agent=entry_agent,
                            shadow_mode=shadow_mode,
                            logger=logger,
                            analysis_mode="stop_loss_reversal",
                            reversal_context={
                                "trigger": "stop_loss_reversal",
                                "stopped_ticket_id": closed_ticket.ticket_id,
                                "prior_side": closed_ticket.side,
                                "required_opposite_side": ("short" if closed_ticket.side == "long" else "long"),
                                "prior_entry_price": float(closed_ticket.open_price),
                                "prior_stop_loss": float(closed_ticket.initial_stop_loss),
                                "prior_take_profit": float(closed_ticket.hard_take_profit),
                                "realized_pnl_usd": float(closed_ticket.unrealized_pnl_usd),
                                "realized_r": closed_ticket.unrealized_r,
                                "exit_reason": closed_ticket.last_close_reason,
                            },
                        )
                        if reversal_executed:
                            break
                if reversal_executed:
                    continue

                has_open_position = registry.has_open_position(snapshot.symbol)
                if has_open_position:
                    protection_queued = await _run_entry_protection_cycle(
                        snapshot=snapshot,
                        settings=settings,
                        agent_name=agent_name,
                        event_journal=event_journal,
                        store=store,
                        registry=registry,
                        planner=planner,
                        bridge_state=bridge_state,
                        shadow_mode=shadow_mode,
                        logger=logger,
                    )
                    if protection_queued:
                        continue
                    deterministic_managed = await _run_deterministic_management_cycle(
                        snapshot=snapshot,
                        settings=settings,
                        agent_name=agent_name,
                        event_journal=event_journal,
                        store=store,
                        registry=registry,
                        planner=planner,
                        context_builder=state.context_builder,
                        posture_engine=posture_engine,
                        bridge_state=bridge_state,
                        reflections=state.reflections,
                        lessons=state.lessons,
                        screenshot_state=state.screenshot_state,
                        shadow_mode=shadow_mode,
                        logger=logger,
                    )
                    if deterministic_managed:
                        continue
                    tickets = registry.all(snapshot.symbol)
                    manager_due = (
                        state.last_manager_run_at is None
                        or snapshot.server_time >= state.last_manager_run_at + timedelta(seconds=settings.v61_mt5_manager_sweep_seconds)
                        or (
                            normalized_symbol in updated_symbols
                            and any(ticket.first_protection_review_pending for ticket in tickets)
                        )
                    )
                    if manager_due:
                        state.screenshot_state = await _run_manager_cycle(
                            snapshot=snapshot,
                            settings=settings,
                            agent_name=agent_name,
                            event_journal=event_journal,
                            store=store,
                            registry=registry,
                            planner=planner,
                            context_builder=state.context_builder,
                            posture_engine=posture_engine,
                            bridge_state=bridge_state,
                            reflections=state.reflections,
                            lessons=state.lessons,
                            screenshot_state=state.screenshot_state,
                            manager_agent=manager_agent,
                            shadow_mode=shadow_mode,
                            logger=logger,
                        )
                        state.last_manager_run_at = snapshot.server_time
                    continue

                if await bridge_state.has_pending_symbol(snapshot.symbol):
                    continue

                if normalized_symbol in updated_symbols:
                    fast_entry_executed, state.last_fast_entry_key = await _run_fast_entry_cycle(
                        snapshot=snapshot,
                        settings=settings,
                        agent_name=agent_name,
                        event_journal=event_journal,
                        store=store,
                        registry=registry,
                        entry_builder=entry_builder,
                        risk_arbiter=state.risk_arbiter,
                        context_builder=state.context_builder,
                        posture_engine=posture_engine,
                        bridge_state=bridge_state,
                        reflections=state.reflections,
                        lessons=state.lessons,
                        screenshot_state=state.screenshot_state,
                        shadow_mode=shadow_mode,
                        logger=logger,
                        last_signal_key=state.last_fast_entry_key,
                    )
                    if fast_entry_executed:
                        continue

                current_bar_end = _latest_entry_bar_end(snapshot)
                if normalized_symbol in updated_symbols and current_bar_end is not None and current_bar_end != state.last_entry_bar_end:
                    await _run_entry_cycle(
                        snapshot=snapshot,
                        settings=settings,
                        agent_name=agent_name,
                        event_journal=event_journal,
                        store=store,
                        registry=registry,
                        entry_builder=entry_builder,
                        risk_arbiter=state.risk_arbiter,
                        context_builder=state.context_builder,
                        posture_engine=posture_engine,
                        bridge_state=bridge_state,
                        reflections=state.reflections,
                        lessons=state.lessons,
                        screenshot_state=state.screenshot_state,
                        entry_agent=entry_agent,
                        shadow_mode=shadow_mode,
                        logger=logger,
                        analysis_mode="standard_entry",
                    )
                    state.last_entry_bar_end = current_bar_end
    finally:
        await _shutdown_flatten_open_tickets(
            settings=settings,
            agent_name=agent_name,
            event_journal=event_journal,
            store=store,
            registry=registry,
            bridge_state=bridge_state,
            shadow_mode=shadow_mode,
            logger=logger,
        )
        bridge_server.should_exit = True
        await bridge_task


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
