from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import uvicorn

from agents.mt5_v51_entry_analyst import MT5V51EntryAnalysisResult, MT5V51EntryAnalystAgent
from agents.mt5_v51_position_manager import MT5V51PositionManagerAgent
from app.v5_1_config import V51Settings, get_v51_settings
from brokers.mt5_v51 import MT5V51BridgeState, create_mt5_v51_bridge_app
from data.mt5_v51_schemas import (
    MT5V51BridgeCommand,
    MT5V51BridgeSnapshot,
    MT5V51EntryDecision,
    MT5V51ManagementDecision,
    MT5V51RiskDecision,
    MT5V51TicketRecord,
)
from data.schemas import LessonRecord, TradeReflection
from execution.mt5_v51_entry_planner import MT5V51EntryPlanner
from execution.mt5_v51_ticket_registry import MT5V51TicketRegistry
from feedback.mt5_v51_reflection import build_mt5_v51_ticket_reflection, derive_mt5_v51_lessons
from infra.logging import configure_logging, get_logger
from memory.journal import Journal
from memory.supabase_mt5_v51 import SupabaseMT5V51Store
from risk.mt5_v51_policy import MT5V51RiskArbiter, MT5V51RiskPostureEngine
from runtime.mt5_v51_microbars import MT5V51Synthetic20sBuilder
from runtime.mt5_v51_context_packet import MT5V51ContextBuilder


def _safe_store_call(logger, operation: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        logger.error("v5_1_mt5_store_error operation=%s error=%s", operation, exc)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the V5.1 MT5 BTCUSD demo-paper orchestrator.")
    parser.add_argument("--agent-name", default=None)
    parser.add_argument("--duration-minutes", type=int, default=0)
    parser.add_argument("--session-tag", default=None)
    parser.add_argument("--enable-trade-commands", action="store_true")
    parser.add_argument("--bridge-host", default=None)
    parser.add_argument("--bridge-port", type=int, default=None)
    return parser.parse_args()


@dataclass
class MT5V51PendingEntrySignal:
    symbol: str
    source_bar_end: datetime
    source_server_time: datetime
    target_open_at: datetime
    analysis_packet: dict[str, object]
    source_risk_posture: str
    result: MT5V51EntryAnalysisResult


def _latest_entry_bar_end(snapshot: MT5V51BridgeSnapshot) -> datetime | None:
    return snapshot.bars_1m[-1].end_at if snapshot.bars_1m else None


def _entry_target_open_at(snapshot: MT5V51BridgeSnapshot, *, timeout_seconds: int) -> datetime:
    reference = _latest_entry_bar_end(snapshot) or snapshot.server_time
    return reference + timedelta(seconds=timeout_seconds)


def _entry_analysis_budget_seconds(snapshot: MT5V51BridgeSnapshot, *, timeout_seconds: int) -> float:
    target_open_at = _entry_target_open_at(snapshot, timeout_seconds=timeout_seconds)
    return max(0.1, (target_open_at - snapshot.server_time).total_seconds())


def _entry_signal_ready(signal: MT5V51PendingEntrySignal, snapshot: MT5V51BridgeSnapshot) -> bool:
    return (
        signal.symbol.strip().upper() == snapshot.symbol.strip().upper()
        and snapshot.server_time >= signal.target_open_at
    )


def _entry_command_expires_at(snapshot: MT5V51BridgeSnapshot, *, stale_after_seconds: int) -> datetime:
    return snapshot.server_time + timedelta(seconds=stale_after_seconds)


def _microbars_ready(snapshot: MT5V51BridgeSnapshot, *, minimum_bars: int) -> bool:
    return len([bar for bar in snapshot.bars_20s if bar.complete]) >= minimum_bars


def _preflight_scalp_veto_reason(
    *,
    snapshot: MT5V51BridgeSnapshot,
    decision: MT5V51EntryDecision,
    context_builder: MT5V51ContextBuilder,
    minimum_micro_bars: int,
) -> str | None:
    if not _microbars_ready(snapshot, minimum_bars=minimum_micro_bars):
        return "Synthetic 20s warm-up is incomplete."
    if context_builder.preflight_alignment_flipped(snapshot=snapshot, action=decision.action):
        return "Fresh 20s and 1m EMA alignment flipped against the entry during preflight."
    return None


async def run() -> None:
    args = _parse_args()
    settings = get_v51_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.openrouter_api_key is None:
        raise RuntimeError("V51_OPENROUTER_API_KEY is required for the V5.1 MT5 runtime.")

    session_tag = args.session_tag or datetime.now(timezone.utc).strftime("v5-1-mt5-%Y%m%d-%H%M%S")
    artifact_dir = Path("var/v5_1") / session_tag
    artifact_dir.mkdir(parents=True, exist_ok=True)
    event_journal = Journal(str(artifact_dir / "events.jsonl"))
    reflection_journal = Journal(str(artifact_dir / "trade_reflections.jsonl"))
    store = SupabaseMT5V51Store(settings.supabase_db_dsn) if settings.supabase_db_dsn is not None else None

    bridge_state = MT5V51BridgeState(settings.v51_bridge_id)
    bridge_app = create_mt5_v51_bridge_app(
        bridge_state,
        journal=event_journal,
        store=store,
        agent_name=args.agent_name or settings.v51_agent_name,
    )
    bridge_server, bridge_task = await _start_bridge_server(
        app=bridge_app,
        host=args.bridge_host or settings.v51_bridge_host,
        port=args.bridge_port or settings.v51_bridge_port,
    )

    entry_agent = MT5V51EntryAnalystAgent(
        api_key=settings.openrouter_api_key,
        model=settings.v51_openrouter_model,
        base_url=settings.v51_openrouter_base_url,
        reasoning_enabled=settings.v51_entry_reasoning_enabled,
    )
    manager_agent = MT5V51PositionManagerAgent(
        api_key=settings.openrouter_api_key,
        model=settings.v51_openrouter_model,
        base_url=settings.v51_openrouter_base_url,
        reasoning_enabled=settings.v51_manager_reasoning_enabled,
    )
    planner = MT5V51EntryPlanner(
        partial_target_r=Decimal(str(settings.v51_partial_target_r)),
        final_target_r=Decimal(str(settings.v51_final_target_r)),
    )
    context_builder = MT5V51ContextBuilder()
    micro_bar_builder = MT5V51Synthetic20sBuilder(
        settings.v51_mt5_symbol,
        max_bars=settings.v51_micro_lookback_bars,
        warmup_bars=settings.v51_micro_min_warmup_bars,
    )
    recent_entry_times = (
        store.list_recent_approved_entry_times(
            symbol=settings.v51_mt5_symbol,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        if store is not None
        else []
    )
    risk_arbiter = MT5V51RiskArbiter(
        symbol=settings.v51_mt5_symbol,
        account_mode=settings.v51_mt5_account_mode,
        min_confidence=settings.v51_min_decision_confidence,
        max_spread_bps=settings.v51_max_spread_bps,
        stale_after_seconds=settings.v51_stale_after_seconds,
        min_risk_fraction=settings.v51_min_risk_fraction,
        max_risk_fraction=settings.v51_max_risk_fraction,
        daily_loss_pct=settings.v51_max_daily_loss_pct,
        max_trades_per_hour=settings.v51_max_trades_per_hour,
        seeded_entry_times=recent_entry_times,
    )
    posture_engine = MT5V51RiskPostureEngine()
    registry = MT5V51TicketRegistry(
        store=store,
        partial_target_r=Decimal(str(settings.v51_partial_target_r)),
        final_target_r=Decimal(str(settings.v51_final_target_r)),
        post_partial_stop_lock_r=Decimal(str(settings.v51_post_partial_stop_lock_r)),
    )
    if store is not None:
        registry.seed(store.list_open_ticket_states(symbol=settings.v51_mt5_symbol))

    reflections: list[TradeReflection] = store.list_recent_trade_reflections(symbol=settings.v51_mt5_symbol, limit=10) if store is not None else []
    lessons: list[LessonRecord] = store.list_recent_lessons(limit=20) if store is not None else []
    entry_analysis_tasks: dict[datetime, asyncio.Task[MT5V51PendingEntrySignal]] = {}
    pending_entry_signals: dict[datetime, MT5V51PendingEntrySignal] = {}
    last_entry_bar_end: datetime | None = None
    last_manager_run_at: datetime | None = None
    last_manager_signature = ""
    last_quarter_r_buckets: dict[str, float] = {}
    commands_enabled = args.enable_trade_commands or settings.v51_mt5_enable_trade_commands
    shadow_mode = settings.v51_mt5_shadow_mode or not commands_enabled
    if args.enable_trade_commands:
        shadow_mode = False
    end_at = None
    if args.duration_minutes > 0:
        end_at = datetime.now(timezone.utc) + timedelta(minutes=args.duration_minutes)

    logger.info(
        "v5_1_mt5_start session_tag=%s symbol=%s bridge=%s:%s shadow_mode=%s",
        session_tag,
        settings.v51_mt5_symbol,
        args.bridge_host or settings.v51_bridge_host,
        args.bridge_port or settings.v51_bridge_port,
        shadow_mode,
    )

    try:
        while end_at is None or datetime.now(timezone.utc) < end_at:
            snapshot_updated = False
            try:
                await bridge_state.wait_for_snapshot(timeout=1.0)
                snapshot_updated = True
            except TimeoutError:
                pass

            snapshot = await bridge_state.latest_snapshot()
            if snapshot is None:
                continue
            snapshot = micro_bar_builder.enrich_snapshot(snapshot)

            await _process_acks(bridge_state=bridge_state, registry=registry)
            sync_result = registry.sync(snapshot)
            for closed_ticket in sync_result.closed:
                reflection = build_mt5_v51_ticket_reflection(closed_ticket, exit_reason="snapshot_flat")
                reflections.append(reflection)
                new_lessons = derive_mt5_v51_lessons(reflection)
                lessons.extend(new_lessons)
                reflection_journal.record(
                    {
                        "record_type": "mt5_v51_trade_reflection",
                        "agent_name": args.agent_name or settings.v51_agent_name,
                        "reflection": reflection.model_dump(mode="json"),
                        "lessons": [lesson.model_dump(mode="json") for lesson in new_lessons],
                    }
                )
                if store is not None:
                    _safe_store_call(
                        logger,
                        "insert_mt5_v51_trade_reflection",
                        store.insert_mt5_v51_trade_reflection,
                        agent_name=args.agent_name or settings.v51_agent_name,
                        reflection=reflection,
                        ticket_id=closed_ticket.ticket_id,
                        basket_id=closed_ticket.basket_id,
                    )
                    _safe_store_call(logger, "upsert_mt5_v51_lessons", store.upsert_lessons, new_lessons)

            _harvest_completed_entry_analyses(
                agent_name=args.agent_name or settings.v51_agent_name,
                event_journal=event_journal,
                store=store,
                entry_prompt_version=entry_agent.prompt_version,
                analysis_tasks=entry_analysis_tasks,
                pending_entry_signals=pending_entry_signals,
                logger=logger,
            )

            current_bar_end = _latest_entry_bar_end(snapshot)
            if snapshot_updated and current_bar_end is not None and current_bar_end != last_entry_bar_end:
                _launch_entry_analysis(
                    snapshot=snapshot,
                    settings=settings,
                    entry_agent=entry_agent,
                    registry=registry,
                    risk_arbiter=risk_arbiter,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    reflections=reflections,
                    lessons=lessons,
                    analysis_tasks=entry_analysis_tasks,
                    logger=logger,
                )
                last_entry_bar_end = current_bar_end

            await _preflight_pending_entries(
                snapshot=snapshot,
                settings=settings,
                agent_name=args.agent_name or settings.v51_agent_name,
                event_journal=event_journal,
                store=store,
                entry_prompt_version=entry_agent.prompt_version,
                registry=registry,
                planner=planner,
                risk_arbiter=risk_arbiter,
                context_builder=context_builder,
                posture_engine=posture_engine,
                bridge_state=bridge_state,
                reflections=reflections,
                lessons=lessons,
                pending_entry_signals=pending_entry_signals,
                shadow_mode=shadow_mode,
                logger=logger,
            )

            if snapshot_updated:
                protection_queued = await _run_entry_protection_cycle(
                    snapshot=snapshot,
                    agent_name=args.agent_name or settings.v51_agent_name,
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

                await _run_auto_scalp_cycle(
                    snapshot=snapshot,
                    agent_name=args.agent_name or settings.v51_agent_name,
                    event_journal=event_journal,
                    store=store,
                    registry=registry,
                    planner=planner,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    bridge_state=bridge_state,
                    reflections=reflections,
                    lessons=lessons,
                    shadow_mode=shadow_mode,
                    logger=logger,
                )

            should_run_manager = _should_run_manager(
                snapshot=snapshot,
                registry=registry,
                last_manager_run_at=last_manager_run_at,
                last_signature=last_manager_signature,
                last_quarter_r_buckets=last_quarter_r_buckets,
                manager_sweep_seconds=settings.v51_mt5_manager_sweep_seconds,
            )
            if should_run_manager:
                manager_signature, quarter_r_buckets = await _run_manager_cycle(
                    snapshot=snapshot,
                    agent_name=args.agent_name or settings.v51_agent_name,
                    event_journal=event_journal,
                    store=store,
                    manager_agent=manager_agent,
                    registry=registry,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    bridge_state=bridge_state,
                    reflections=reflections,
                    lessons=lessons,
                    shadow_mode=shadow_mode,
                    logger=logger,
                )
                last_manager_run_at = snapshot.server_time
                last_manager_signature = manager_signature
                last_quarter_r_buckets = quarter_r_buckets
    finally:
        for task in entry_analysis_tasks.values():
            task.cancel()
        if entry_analysis_tasks:
            await asyncio.gather(*entry_analysis_tasks.values(), return_exceptions=True)
        bridge_server.should_exit = True
        await bridge_task


async def _process_acks(*, bridge_state: MT5V51BridgeState, registry: MT5V51TicketRegistry) -> None:
    for ack in await bridge_state.drain_acks():
        registry.record_ack(ack)


def _launch_entry_analysis(
    *,
    snapshot: MT5V51BridgeSnapshot,
    settings: V51Settings,
    entry_agent: MT5V51EntryAnalystAgent,
    registry: MT5V51TicketRegistry,
    risk_arbiter: MT5V51RiskArbiter,
    context_builder: MT5V51ContextBuilder,
    posture_engine: MT5V51RiskPostureEngine,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    analysis_tasks: dict[datetime, asyncio.Task[MT5V51PendingEntrySignal]],
    logger,
) -> None:
    source_bar_end = _latest_entry_bar_end(snapshot)
    if source_bar_end is None or source_bar_end in analysis_tasks:
        return
    if risk_arbiter.snapshot_is_stale(snapshot):
        logger.info(
            "v5_1_entry_analysis_skipped reason=source_snapshot_stale symbol=%s bar_end=%s",
            snapshot.symbol,
            source_bar_end.isoformat(),
        )
        return
    if not _microbars_ready(snapshot, minimum_bars=settings.v51_micro_min_warmup_bars):
        logger.info(
            "v5_1_entry_analysis_skipped reason=microbar_warmup_incomplete symbol=%s bar_end=%s closed_20s_bars=%s",
            snapshot.symbol,
            source_bar_end.isoformat(),
            len(snapshot.bars_20s),
        )
        return

    risk_posture, _ = posture_engine.derive(reflections)
    packet = context_builder.build_entry_packet(
        snapshot=snapshot,
        registry=registry,
        risk_posture=risk_posture,
        reflections=list(reflections),
        lessons=list(lessons),
    )
    analysis_tasks[source_bar_end] = asyncio.create_task(
        _analyze_entry_signal(
            symbol=snapshot.symbol,
            source_bar_end=source_bar_end,
            source_server_time=snapshot.server_time,
            target_open_at=_entry_target_open_at(snapshot, timeout_seconds=settings.v51_mt5_entry_timeout_seconds),
            analysis_packet=packet,
            source_risk_posture=risk_posture,
            timeout_seconds=_entry_analysis_budget_seconds(
                snapshot,
                timeout_seconds=settings.v51_mt5_entry_timeout_seconds,
            ),
            entry_agent=entry_agent,
        )
    )


async def _analyze_entry_signal(
    *,
    symbol: str,
    source_bar_end: datetime,
    source_server_time: datetime,
    target_open_at: datetime,
    analysis_packet: dict[str, object],
    source_risk_posture: str,
    timeout_seconds: float,
    entry_agent: MT5V51EntryAnalystAgent,
) -> MT5V51PendingEntrySignal:
    try:
        result = await asyncio.wait_for(entry_agent.analyze(analysis_packet), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        result = MT5V51EntryAnalysisResult(
            decision=MT5V51EntryDecision(action="hold", confidence=0.0, rationale="Entry analyst timed out.", thesis_tags=[]),
            prompt="",
            raw_response="",
            latency_ms=int(timeout_seconds * 1000),
        )
    except Exception as exc:
        result = MT5V51EntryAnalysisResult(
            decision=entry_agent.fallback_decision("Entry analyst failed."),
            prompt="",
            raw_response=str(exc),
            latency_ms=0,
        )
    return MT5V51PendingEntrySignal(
        symbol=symbol,
        source_bar_end=source_bar_end,
        source_server_time=source_server_time,
        target_open_at=target_open_at,
        analysis_packet=analysis_packet,
        source_risk_posture=source_risk_posture,
        result=result,
    )


def _harvest_completed_entry_analyses(
    *,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V51Store | None,
    entry_prompt_version: str,
    analysis_tasks: dict[datetime, asyncio.Task[MT5V51PendingEntrySignal]],
    pending_entry_signals: dict[datetime, MT5V51PendingEntrySignal],
    logger,
) -> None:
    for source_bar_end, task in list(analysis_tasks.items()):
        if not task.done():
            continue
        analysis_tasks.pop(source_bar_end, None)
        if task.cancelled():
            continue
        try:
            signal = task.result()
        except Exception as exc:
            logger.error("v5_1_entry_analysis_error bar_end=%s error=%s", source_bar_end.isoformat(), exc)
            continue

        event_journal.record(
            {
                "record_type": "mt5_v51_entry_analysis",
                "agent_name": agent_name,
                "context": signal.analysis_packet,
                "decision": signal.result.decision.model_dump(mode="json"),
                "raw_llm_response": signal.result.raw_response,
                "latency_ms": signal.result.latency_ms,
                "source_bar_end": signal.source_bar_end.isoformat(),
                "target_open_at": signal.target_open_at.isoformat(),
                "source_risk_posture": signal.source_risk_posture,
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v51_runtime_decision_entry_signal",
                store.insert_mt5_v51_runtime_decision,
                agent_name=agent_name,
                decision_kind="entry",
                symbol=signal.symbol,
                action=signal.result.decision.action,
                confidence=signal.result.decision.confidence,
                rationale=signal.result.decision.rationale,
                risk_posture=signal.source_risk_posture,
                risk_approved=None,
                risk_reason=(
                    "Awaiting execution preflight on the following candle open."
                    if signal.result.decision.action != "hold"
                    else "Entry decision is hold."
                ),
                context_payload=signal.analysis_packet,
                decision_payload={
                    "stage": "signal",
                    "decision": signal.result.decision.model_dump(mode="json"),
                    "raw_response": signal.result.raw_response,
                    "prompt_version": entry_prompt_version,
                    "latency_ms": signal.result.latency_ms,
                    "source_bar_end": signal.source_bar_end.isoformat(),
                    "source_server_time": signal.source_server_time.isoformat(),
                    "target_open_at": signal.target_open_at.isoformat(),
                },
            )

        if signal.result.decision.action == "hold":
            continue
        pending_entry_signals[source_bar_end] = signal


async def _preflight_pending_entries(
    *,
    snapshot: MT5V51BridgeSnapshot,
    settings: V51Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V51Store | None,
    entry_prompt_version: str,
    registry: MT5V51TicketRegistry,
    planner: MT5V51EntryPlanner,
    risk_arbiter: MT5V51RiskArbiter,
    context_builder: MT5V51ContextBuilder,
    posture_engine: MT5V51RiskPostureEngine,
    bridge_state: MT5V51BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    pending_entry_signals: dict[datetime, MT5V51PendingEntrySignal],
    shadow_mode: bool,
    logger,
) -> None:
    ready_bar_ends = [
        source_bar_end
        for source_bar_end, signal in pending_entry_signals.items()
        if _entry_signal_ready(signal, snapshot)
    ]
    if not ready_bar_ends:
        return

    pending_symbol_command = await bridge_state.has_pending_symbol(snapshot.symbol)
    risk_posture, multiplier = posture_engine.derive(reflections)
    followed_lessons = [lesson.message for lesson in lessons[-3:]]

    for source_bar_end in sorted(ready_bar_ends, key=lambda item: pending_entry_signals[item].target_open_at):
        signal = pending_entry_signals.pop(source_bar_end)
        preflight_packet = context_builder.build_entry_packet(
            snapshot=snapshot,
            registry=registry,
            risk_posture=risk_posture,
            reflections=list(reflections),
            lessons=list(lessons),
        )
        risk_decision = risk_arbiter.evaluate_entry(
            decision=signal.result.decision,
            snapshot=snapshot,
            registry=registry,
            risk_posture=risk_posture,
            risk_multiplier=multiplier,
            pending_symbol_command=pending_symbol_command,
        )
        scalp_veto_reason = None
        if risk_decision.approved:
            scalp_veto_reason = _preflight_scalp_veto_reason(
                snapshot=snapshot,
                decision=signal.result.decision,
                context_builder=context_builder,
                minimum_micro_bars=settings.v51_micro_min_warmup_bars,
            )
        if scalp_veto_reason is not None:
            risk_decision = MT5V51RiskDecision(
                approved=False,
                reason=scalp_veto_reason,
                risk_fraction=None,
                risk_posture=risk_decision.risk_posture,
            )
        event_journal.record(
            {
                "record_type": "mt5_v51_entry_preflight",
                "agent_name": agent_name,
                "source_bar_end": signal.source_bar_end.isoformat(),
                "target_open_at": signal.target_open_at.isoformat(),
                "decision": signal.result.decision.model_dump(mode="json"),
                "risk_decision": risk_decision.model_dump(mode="json"),
                "preflight_context": preflight_packet,
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v51_runtime_decision_entry",
                store.insert_mt5_v51_runtime_decision,
                agent_name=agent_name,
                decision_kind="entry",
                symbol=snapshot.symbol,
                action=signal.result.decision.action,
                confidence=signal.result.decision.confidence,
                rationale=signal.result.decision.rationale,
                risk_posture=risk_posture,
                risk_approved=risk_decision.approved,
                risk_reason=risk_decision.reason,
                context_payload=preflight_packet,
                decision_payload={
                    "stage": "preflight",
                    "decision": signal.result.decision.model_dump(mode="json"),
                    "raw_response": signal.result.raw_response,
                    "prompt_version": entry_prompt_version,
                    "source_bar_end": signal.source_bar_end.isoformat(),
                    "source_server_time": signal.source_server_time.isoformat(),
                    "target_open_at": signal.target_open_at.isoformat(),
                    "analysis_risk_posture": signal.source_risk_posture,
                    "preflight_server_time": snapshot.server_time.isoformat(),
                },
            )

        if not risk_decision.approved:
            continue

        plan = planner.plan_entry(
            decision=signal.result.decision,
            snapshot=snapshot,
            risk_decision=risk_decision,
            ticket_sequence=1,
        )
        if plan is None:
            logger.info(
                "v5_1_entry_skipped reason=planner_returned_none symbol=%s bar_end=%s",
                snapshot.symbol,
                signal.source_bar_end.isoformat(),
            )
            continue

        command = planner.build_entry_command(
            plan=plan,
            reason=signal.result.decision.rationale,
            created_at=snapshot.server_time,
            expires_at=_entry_command_expires_at(
                snapshot,
                stale_after_seconds=settings.v51_stale_after_seconds,
            ),
            thesis_tags=signal.result.decision.thesis_tags,
            context_signature=signal.result.decision.context_signature or signal.analysis_packet.get("context_signature"),
            followed_lessons=followed_lessons,
        )
        command = command.model_copy(
            update={
                "metadata": {
                    **command.metadata,
                    "analysis_source_bar_end": signal.source_bar_end.isoformat(),
                    "analysis_source_server_time": signal.source_server_time.isoformat(),
                    "execution_target_open_at": signal.target_open_at.isoformat(),
                    "analysis_risk_posture": signal.source_risk_posture,
                    "preflight_risk_posture": risk_posture,
                    "preflight_context_signature": preflight_packet.get("context_signature"),
                }
            }
        )
        risk_arbiter.record_approved_entry(snapshot.server_time)
        pending_symbol_command = True

        if shadow_mode:
            event_journal.record(
                {
                    "record_type": "mt5_v51_shadow_command",
                    "agent_name": agent_name,
                    "command": command.model_dump(mode="json"),
                }
            )
            continue

        registry.register_pending_entry(
            command=command,
            plan_payload={
                **plan.model_dump(mode="json"),
                "hard_take_profit": plan.take_profit,
                "soft_take_profit_1": plan.soft_take_profit_1,
                "soft_take_profit_2": plan.soft_take_profit_2,
                "thesis_tags": signal.result.decision.thesis_tags,
                "context_signature": signal.result.decision.context_signature or signal.analysis_packet.get("context_signature"),
                "followed_lessons": followed_lessons,
                "risk_posture": risk_posture,
                "analysis_source_bar_end": signal.source_bar_end.isoformat(),
                "analysis_source_server_time": signal.source_server_time.isoformat(),
                "execution_target_open_at": signal.target_open_at.isoformat(),
                "preflight_context_signature": preflight_packet.get("context_signature"),
            },
        )

        await bridge_state.queue_command(command)
        event_journal.record(
            {
                "record_type": "mt5_v51_bridge_command_enqueued",
                "agent_name": agent_name,
                "command": command.model_dump(mode="json"),
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v51_bridge_command_entry",
                store.insert_mt5_v51_bridge_command,
                agent_name=agent_name,
                command=command,
                bridge_id=settings.v51_bridge_id,
            )


async def _run_manager_cycle(
    *,
    snapshot: MT5V51BridgeSnapshot,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V51Store | None,
    manager_agent: MT5V51PositionManagerAgent,
    registry: MT5V51TicketRegistry,
    context_builder: MT5V51ContextBuilder,
    posture_engine: MT5V51RiskPostureEngine,
    bridge_state: MT5V51BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
    logger,
) -> tuple[str, dict[str, float]]:
    risk_posture, _ = posture_engine.derive(reflections)
    allowed_actions = {
        ticket.ticket_id: registry.allowed_actions(ticket.ticket_id)
        for ticket in registry.all(snapshot.symbol)
    }
    packet = context_builder.build_manager_packet(
        snapshot=snapshot,
        registry=registry,
        allowed_actions=allowed_actions,
        risk_posture=risk_posture,
        reflections=reflections,
        lessons=lessons,
    )
    result = await manager_agent.analyze(packet)
    pending_symbol_command = await bridge_state.has_pending_symbol(snapshot.symbol)
    for decision in result.decision_batch.decisions:
        ticket = registry.by_ticket_id(decision.ticket_id)
        if ticket is None:
            continue
        allowed = allowed_actions.get(ticket.ticket_id, ["hold"])
        risk_approved = decision.action in allowed and not pending_symbol_command
        risk_reason = "Management action approved." if risk_approved else "Management action is not allowed in the current state."
        event_journal.record(
            {
                "record_type": "mt5_v51_management_decision",
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
                "insert_mt5_v51_runtime_decision_management",
                store.insert_mt5_v51_runtime_decision,
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
                decision_payload={
                    "decision": decision.model_dump(mode="json"),
                    "raw_response": result.raw_response,
                    "prompt_version": manager_agent.prompt_version,
                },
            )
        if not risk_approved or decision.action == "hold":
            continue

        commands = _management_commands_from_decision(
            decision=decision,
            ticket=ticket,
            snapshot=snapshot,
        )
        if shadow_mode:
            for command in commands:
                event_journal.record(
                    {
                        "record_type": "mt5_v51_shadow_management_command",
                        "agent_name": agent_name,
                        "command": command.model_dump(mode="json"),
                    }
                )
            continue
        for command in commands:
            await bridge_state.queue_command(command)
            event_journal.record(
                {
                    "record_type": "mt5_v51_bridge_command_enqueued",
                    "agent_name": agent_name,
                    "command_source": "management",
                    "command": command.model_dump(mode="json"),
                }
            )
            if store is not None:
                _safe_store_call(
                    logger,
                    "insert_mt5_v51_bridge_command_management",
                    store.insert_mt5_v51_bridge_command,
                    agent_name=agent_name,
                    command=command,
                    bridge_id=snapshot.bridge_id,
                )
    return registry.signature(snapshot.symbol), registry.quarter_r_buckets(snapshot.symbol)


async def _run_entry_protection_cycle(
    *,
    snapshot: MT5V51BridgeSnapshot,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V51Store | None,
    registry: MT5V51TicketRegistry,
    planner: MT5V51EntryPlanner,
    bridge_state: MT5V51BridgeState,
    shadow_mode: bool,
    logger,
) -> bool:
    tickets = [ticket for ticket in registry.all(snapshot.symbol) if ticket.stop_loss is None or ticket.take_profit is None]
    if not tickets:
        return False
    pending_symbol_command = await bridge_state.has_pending_symbol(snapshot.symbol)
    if pending_symbol_command:
        return False

    for ticket in tickets:
        command = planner.build_protection_command(
            ticket=ticket,
            snapshot=snapshot,
            reason="Attach broker-safe protection after entry fill.",
            created_at=snapshot.server_time,
            expires_at=snapshot.server_time + timedelta(seconds=60),
        )
        if command is None:
            continue
        if shadow_mode:
            event_journal.record(
                {
                    "record_type": "mt5_v51_shadow_management_command",
                    "agent_name": agent_name,
                    "command_source": "entry_protection",
                    "command": command.model_dump(mode="json"),
                }
            )
            return True
        await bridge_state.queue_command(command)
        event_journal.record(
            {
                "record_type": "mt5_v51_bridge_command_enqueued",
                "agent_name": agent_name,
                "command_source": "entry_protection",
                "command": command.model_dump(mode="json"),
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v51_bridge_command_entry_protection",
                store.insert_mt5_v51_bridge_command,
                agent_name=agent_name,
                command=command,
                bridge_id=snapshot.bridge_id,
            )
        return True
    return False


def _management_commands_from_decision(
    *,
    decision: MT5V51ManagementDecision,
    ticket: MT5V51TicketRecord,
    snapshot: MT5V51BridgeSnapshot,
) -> list[MT5V51BridgeCommand]:
    created_at = snapshot.server_time
    expires_at = created_at + timedelta(seconds=60)
    if decision.action == "close_ticket":
        return [
            MT5V51BridgeCommand(
                command_id=f"close-{ticket.ticket_id}-{int(created_at.timestamp())}",
                command_type="close_ticket",
                symbol=ticket.symbol,
                created_at=created_at,
                expires_at=expires_at,
                ticket_id=ticket.ticket_id,
                basket_id=ticket.basket_id,
                volume_lots=ticket.current_volume_lots,
                reason=decision.rationale,
                metadata={"action": decision.action},
            )
        ]
    return []


async def _run_auto_scalp_cycle(
    *,
    snapshot: MT5V51BridgeSnapshot,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V51Store | None,
    registry: MT5V51TicketRegistry,
    planner: MT5V51EntryPlanner,
    context_builder: MT5V51ContextBuilder,
    posture_engine: MT5V51RiskPostureEngine,
    bridge_state: MT5V51BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
    logger,
) -> None:
    tickets = registry.all(snapshot.symbol)
    if not tickets:
        return
    pending_symbol_command = await bridge_state.has_pending_symbol(snapshot.symbol)
    if pending_symbol_command:
        return
    risk_posture, _ = posture_engine.derive(reflections)
    allowed_actions = {ticket.ticket_id: ["close_ticket"] for ticket in tickets}
    context_packet = context_builder.build_manager_packet(
        snapshot=snapshot,
        registry=registry,
        allowed_actions=allowed_actions,
        risk_posture=risk_posture,
        reflections=reflections,
        lessons=lessons,
    )
    for ticket in tickets:
        trigger = None
        rationale = None
        commands: list[MT5V51BridgeCommand] = []
        if registry.scalp_final_ready(ticket):
            trigger = "tp1.0_final"
            rationale = "Automatic scalp exit at 1.0R."
            commands = [
                MT5V51BridgeCommand(
                    command_id=f"close-{ticket.ticket_id}-{int(snapshot.server_time.timestamp())}",
                    command_type="close_ticket",
                    symbol=ticket.symbol,
                    created_at=snapshot.server_time,
                    expires_at=snapshot.server_time + timedelta(seconds=60),
                    ticket_id=ticket.ticket_id,
                    basket_id=ticket.basket_id,
                    volume_lots=ticket.current_volume_lots,
                    reason=rationale,
                    metadata={"action": "auto_scalp_final"},
                )
            ]
        elif registry.scalp_partial_ready(ticket):
            trigger = "tp0.5_partial"
            rationale = "Automatic scalp harvest at 0.5R with stop moved to breakeven."
            fraction = registry.partial_close_fraction(ticket)
            partial_volume = planner.partial_close_volume(
                original_volume_lots=ticket.original_volume_lots,
                close_fraction=fraction,
                snapshot=snapshot,
            )
            remainder = ticket.current_volume_lots - partial_volume
            if partial_volume > 0 and (remainder == 0 or remainder >= snapshot.symbol_spec.volume_min):
                commands.append(
                    MT5V51BridgeCommand(
                        command_id=f"partial-{ticket.ticket_id}-{int(snapshot.server_time.timestamp())}",
                        command_type="close_ticket",
                        symbol=ticket.symbol,
                        created_at=snapshot.server_time,
                        expires_at=snapshot.server_time + timedelta(seconds=60),
                        ticket_id=ticket.ticket_id,
                        basket_id=ticket.basket_id,
                        volume_lots=min(partial_volume, ticket.current_volume_lots),
                        reason=rationale,
                        metadata={"action": "auto_scalp_partial", "fraction": float(fraction)},
                    )
                )
            stop_target = registry.stop_target_for_action(ticket=ticket, snapshot=snapshot)
            safer_stop = _coerce_safer_stop(ticket=ticket, stop_target=stop_target)
            if safer_stop is not None:
                commands.append(
                    MT5V51BridgeCommand(
                        command_id=f"modify-{ticket.ticket_id}-{int(snapshot.server_time.timestamp())}",
                        command_type="modify_ticket",
                        symbol=ticket.symbol,
                        created_at=snapshot.server_time,
                        expires_at=snapshot.server_time + timedelta(seconds=60),
                        ticket_id=ticket.ticket_id,
                        basket_id=ticket.basket_id,
                        stop_loss=safer_stop,
                        take_profit=ticket.take_profit or ticket.hard_take_profit,
                        reason=rationale,
                        metadata={"action": "auto_scalp_breakeven"},
                    )
                )
        if not commands or rationale is None or trigger is None:
            continue

        decision = {
            "ticket_id": ticket.ticket_id,
            "action": "close_ticket",
            "confidence": 1.0,
            "rationale": rationale,
        }
        event_journal.record(
            {
                "record_type": "mt5_v51_management_decision",
                "agent_name": agent_name,
                "decision": decision,
                "allowed_actions": ["close_ticket"],
                "risk_approved": True,
                "risk_reason": "Deterministic scalp management trigger fired.",
                "decision_stage": "auto_scalp",
                "decision_trigger": trigger,
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v51_runtime_decision_management_auto_scalp",
                store.insert_mt5_v51_runtime_decision,
                agent_name=agent_name,
                decision_kind="management",
                symbol=snapshot.symbol,
                action="close_ticket",
                confidence=1.0,
                rationale=rationale,
                risk_posture=risk_posture,
                risk_approved=True,
                risk_reason="Deterministic scalp management trigger fired.",
                context_payload=context_packet,
                decision_payload={
                    "stage": "auto_scalp",
                    "trigger": trigger,
                    "decision": decision,
                    "commands": [command.model_dump(mode="json") for command in commands],
                },
            )
        if shadow_mode:
            for command in commands:
                event_journal.record(
                    {
                        "record_type": "mt5_v51_shadow_management_command",
                        "agent_name": agent_name,
                        "command": command.model_dump(mode="json"),
                    }
                )
            return
        for command in commands:
            await bridge_state.queue_command(command)
            event_journal.record(
                {
                    "record_type": "mt5_v51_bridge_command_enqueued",
                    "agent_name": agent_name,
                    "command_source": "auto_scalp",
                    "command": command.model_dump(mode="json"),
                }
            )
            if store is not None:
                _safe_store_call(
                    logger,
                    "insert_mt5_v51_bridge_command_auto_scalp",
                    store.insert_mt5_v51_bridge_command,
                    agent_name=agent_name,
                    command=command,
                    bridge_id=snapshot.bridge_id,
                )
        return


def _coerce_safer_stop(*, ticket: MT5V51TicketRecord, stop_target: Decimal | None) -> Decimal | None:
    if stop_target is None:
        return None
    if ticket.stop_loss is None:
        return stop_target
    if ticket.side == "long" and stop_target > ticket.stop_loss:
        return stop_target
    if ticket.side == "short" and stop_target < ticket.stop_loss:
        return stop_target
    return None


def _should_run_manager(
    *,
    snapshot: MT5V51BridgeSnapshot,
    registry: MT5V51TicketRegistry,
    last_manager_run_at: datetime | None,
    last_signature: str,
    last_quarter_r_buckets: dict[str, float],
    manager_sweep_seconds: int,
) -> bool:
    if not registry.all(snapshot.symbol):
        return False
    if registry.signature(snapshot.symbol) != last_signature:
        return True
    if registry.quarter_r_buckets(snapshot.symbol) != last_quarter_r_buckets:
        return True
    if last_manager_run_at is None:
        return True
    return snapshot.server_time >= last_manager_run_at + timedelta(seconds=manager_sweep_seconds)


async def _start_bridge_server(*, app, host: str, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.05)
    return server, task


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
