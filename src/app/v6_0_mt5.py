from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import uvicorn

from agents.mt5_v60_entry_analyst import MT5V60EntryAnalystAgent
from agents.mt5_v60_position_manager import MT5V60PositionManagerAgent
from app.v6_0_config import V60Settings, get_v60_settings
from brokers.mt5_v60 import MT5V60BridgeState, create_mt5_v60_bridge_app
from data.mt5_v60_schemas import (
    MT5V60BridgeCommand,
    MT5V60BridgeSnapshot,
    MT5V60EntryDecision,
    MT5V60ManagementDecisionBatch,
    MT5V60ScreenshotState,
    MT5V60TicketRecord,
)
from data.schemas import LessonRecord, TradeReflection
from execution.mt5_v60_entry_planner import MT5V60EntryPlanner
from execution.mt5_v60_immediate_entry import MT5V60ImmediateEntryBuilder
from execution.mt5_v60_ticket_registry import MT5V60TicketRegistry
from feedback.mt5_v60_reflection import build_mt5_v60_ticket_reflection, derive_mt5_v60_lessons
from infra.logging import configure_logging, get_logger
from memory.journal import Journal
from memory.supabase_mt5_v60 import SupabaseMT5V60Store
from risk.mt5_v60_policy import MT5V60RiskArbiter, MT5V60RiskPostureEngine
from runtime.mt5_v60_context_packet import MT5V60ContextBuilder
from runtime.mt5_v60_symbols import mt5_v60_symbols_match


def _safe_store_call(logger, operation: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        logger.error("v6_0_mt5_store_error operation=%s error=%s", operation, exc)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the V6.0 MT5 demo-paper orchestrator.")
    parser.add_argument("--agent-name", default=None)
    parser.add_argument("--duration-minutes", type=int, default=0)
    parser.add_argument("--session-tag", default=None)
    parser.add_argument("--enable-trade-commands", action="store_true")
    parser.add_argument("--shadow-mode", action="store_true")
    parser.add_argument("--bridge-host", default=None)
    parser.add_argument("--bridge-port", type=int, default=None)
    return parser.parse_args()


def _latest_entry_bar_end(snapshot: MT5V60BridgeSnapshot) -> datetime | None:
    return snapshot.bars_3m[-1].end_at if snapshot.bars_3m else None


def _entry_command_expires_at(snapshot: MT5V60BridgeSnapshot, *, stale_after_seconds: int) -> datetime:
    del snapshot
    # Expire relative to queue time so slow LLM responses do not create already-expired commands.
    return datetime.now(timezone.utc) + timedelta(seconds=stale_after_seconds)


def _execution_snapshot(source_snapshot: MT5V60BridgeSnapshot, latest_snapshot: MT5V60BridgeSnapshot | None) -> MT5V60BridgeSnapshot:
    if latest_snapshot is None:
        return source_snapshot
    if not mt5_v60_symbols_match(source_snapshot.symbol, latest_snapshot.symbol):
        return source_snapshot
    source_received_at = source_snapshot.received_at or source_snapshot.server_time
    latest_received_at = latest_snapshot.received_at or latest_snapshot.server_time
    if latest_received_at >= source_received_at:
        return latest_snapshot
    if latest_snapshot.server_time >= source_snapshot.server_time:
        return latest_snapshot
    return source_snapshot


def _recent_lessons_for_latest_reflections(
    *,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
) -> list[str]:
    recent_sources = {reflection.reflection_id for reflection in reflections[-4:]}
    if not recent_sources:
        return []
    selected: list[str] = []
    for lesson in reversed(lessons):
        if lesson.source not in recent_sources:
            continue
        if lesson.message in selected:
            continue
        selected.append(lesson.message)
        if len(selected) >= 4:
            break
    selected.reverse()
    return selected


def _resolve_screenshot_path(settings: V60Settings, snapshot: MT5V60BridgeSnapshot) -> str:
    relative = snapshot.chart_screenshot.relative_path or settings.v60_screenshot_relative_path
    return str(Path(settings.v60_mt5_files_root).expanduser() / relative)


def _sync_screenshot_state(
    *,
    snapshot: MT5V60BridgeSnapshot,
    settings: V60Settings,
    current: MT5V60ScreenshotState,
) -> MT5V60ScreenshotState:
    screenshot = snapshot.chart_screenshot
    if not screenshot.capture_ok or not screenshot.fingerprint or screenshot.captured_at is None:
        return current
    return current.model_copy(
        update={
            "absolute_path": _resolve_screenshot_path(settings, snapshot),
            "latest_screenshot_capture_ts": screenshot.captured_at,
            "latest_screenshot_fingerprint": screenshot.fingerprint,
        }
    )


def _manager_should_attach_raw_image(*, screenshot_state: MT5V60ScreenshotState) -> bool:
    if screenshot_state.absolute_path is None:
        return False
    if screenshot_state.latest_screenshot_fingerprint is None:
        return False
    if screenshot_state.latest_screenshot_fingerprint == screenshot_state.last_manager_image_sent_fingerprint:
        return False
    return Path(screenshot_state.absolute_path).exists()


def _extract_visual_context_update(batch: MT5V60ManagementDecisionBatch) -> dict[str, object] | None:
    for decision in batch.decisions:
        if isinstance(decision.visual_context_update, dict) and decision.visual_context_update:
            return decision.visual_context_update
    return None


def _advance_manager_screenshot_state(
    *,
    screenshot_state: MT5V60ScreenshotState,
    delivery_succeeded: bool,
    visual_context_update: dict[str, object] | None,
) -> MT5V60ScreenshotState:
    if not delivery_succeeded or screenshot_state.latest_screenshot_fingerprint is None:
        return screenshot_state
    return screenshot_state.model_copy(
        update={
            "last_manager_image_sent_fingerprint": screenshot_state.latest_screenshot_fingerprint,
            "cached_visual_context": (visual_context_update or screenshot_state.cached_visual_context),
            "cached_visual_context_capture_ts": screenshot_state.latest_screenshot_capture_ts,
        }
    )


def _manager_command_changes_protection(
    *,
    command_spec,
    ticket: MT5V60TicketRecord,
) -> bool:
    return (
        (command_spec.stop_loss_price is not None and command_spec.stop_loss_price != ticket.stop_loss)
        or (command_spec.take_profit_price is not None and command_spec.take_profit_price != ticket.take_profit)
    )


def _effective_management_action(
    *,
    command_spec,
    ticket: MT5V60TicketRecord,
) -> str:
    if command_spec.action == "hold" and _manager_command_changes_protection(command_spec=command_spec, ticket=ticket):
        return "modify_ticket"
    return command_spec.action


def _should_trigger_stop_loss_reversal(ticket: MT5V60TicketRecord) -> bool:
    return ticket.last_close_reason == "stop_loss" and ticket.analysis_mode != "stop_loss_reversal"


def _reversal_context(ticket: MT5V60TicketRecord) -> dict[str, object]:
    return {
        "trigger": "stop_loss_reversal",
        "stopped_ticket_id": ticket.ticket_id,
        "prior_side": ticket.side,
        "required_opposite_side": ("short" if ticket.side == "long" else "long"),
        "prior_entry_price": float(ticket.open_price),
        "prior_stop_loss": float(ticket.initial_stop_loss),
        "prior_take_profit": float(ticket.hard_take_profit),
        "realized_pnl_usd": float(ticket.unrealized_pnl_usd),
        "realized_r": ticket.unrealized_r,
        "exit_reason": ticket.last_close_reason,
    }


def _coerce_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _coerce_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _coerce_bool(value: object) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _price_delta_bps(*, current: float, reference: float) -> float:
    if reference == 0:
        return 0.0
    return ((current - reference) / reference) * 10000.0


def _recent_closed_bars(bars, *, limit: int):
    return [bar for bar in bars if bar.complete][-limit:]


def _minimum_protection_buffer(snapshot: MT5V60BridgeSnapshot) -> Decimal:
    spread = snapshot.ask - snapshot.bid
    return max(
        spread,
        snapshot.symbol_spec.min_stop_distance_price,
        snapshot.symbol_spec.tick_size * Decimal("4"),
    )


def _more_protective_stop(ticket: MT5V60TicketRecord, left: Decimal, right: Decimal) -> Decimal:
    return max(left, right) if ticket.side == "long" else min(left, right)


def _locked_stop_at_r(ticket: MT5V60TicketRecord, locked_r: Decimal) -> Decimal:
    move = ticket.r_distance_price * locked_r
    if ticket.side == "long":
        return ticket.open_price + move
    return ticket.open_price - move


def _structure_trailing_stop(snapshot: MT5V60BridgeSnapshot, ticket: MT5V60TicketRecord) -> Decimal:
    if ticket.side == "long":
        lows = [bar.low_price for bar in _recent_closed_bars(snapshot.bars_1m, limit=3)]
        lows.extend(bar.low_price for bar in _recent_closed_bars(snapshot.bars_2m, limit=2))
        lows.extend(bar.low_price for bar in _recent_closed_bars(snapshot.bars_3m, limit=2))
        structure = min(lows) if lows else ticket.stop_loss or ticket.initial_stop_loss
        ratchet = ticket.highest_favorable_close - (ticket.r_distance_price * Decimal("0.40"))
        floor = _locked_stop_at_r(ticket, Decimal("0.05")) if ticket.partial_stage >= 1 else _locked_stop_at_r(ticket, Decimal("-0.25"))
        return max(structure, ratchet, floor)

    highs = [bar.high_price for bar in _recent_closed_bars(snapshot.bars_1m, limit=3)]
    highs.extend(bar.high_price for bar in _recent_closed_bars(snapshot.bars_2m, limit=2))
    highs.extend(bar.high_price for bar in _recent_closed_bars(snapshot.bars_3m, limit=2))
    structure = max(highs) if highs else ticket.stop_loss or ticket.initial_stop_loss
    ratchet = ticket.lowest_favorable_close + (ticket.r_distance_price * Decimal("0.40"))
    ceiling = _locked_stop_at_r(ticket, Decimal("0.05")) if ticket.partial_stage >= 1 else _locked_stop_at_r(ticket, Decimal("-0.25"))
    return min(structure, ratchet, ceiling)


def _timeframe_pressure_score(summary: dict[str, object], *, direction: str) -> int:
    prefix = "long" if direction == "bull" else "short"
    score = 0
    if _coerce_bool(summary.get(f"{prefix}_breakout_ready")):
        score += 3
    if _coerce_bool(summary.get(f"{prefix}_continuation_ready")):
        score += 2
    if str(summary.get("direction", "flat")) == direction:
        score += 1
    if direction == "bull":
        if _coerce_float(summary.get("return_1_bps")) > 0:
            score += 1
        if _coerce_float(summary.get("return_3_bps")) > 0:
            score += 1
        if _coerce_float(summary.get("ema_gap_bps")) > 0:
            score += 1
        if _coerce_float(summary.get("close_range_position"), 0.5) >= 0.55:
            score += 1
    else:
        if _coerce_float(summary.get("return_1_bps")) < 0:
            score += 1
        if _coerce_float(summary.get("return_3_bps")) < 0:
            score += 1
        if _coerce_float(summary.get("ema_gap_bps")) < 0:
            score += 1
        if _coerce_float(summary.get("close_range_position"), 0.5) <= 0.45:
            score += 1
    return score


def _aggregate_pressure_scores(packet: dict[str, object], *, ticket_side: str) -> tuple[int, int]:
    timeframes = _coerce_dict(packet.get("timeframes"))
    trade_direction = "bull" if ticket_side == "long" else "bear"
    opposite_direction = "bear" if ticket_side == "long" else "bull"
    same_pressure = 0
    opposite_pressure = 0
    for label in ("1m", "2m", "3m"):
        summary = _coerce_dict(timeframes.get(label))
        same_pressure += _timeframe_pressure_score(summary, direction=trade_direction)
        opposite_pressure += _timeframe_pressure_score(summary, direction=opposite_direction)
    return same_pressure, opposite_pressure


def _build_fast_entry_anchors(
    *,
    snapshot: MT5V60BridgeSnapshot,
    side: str,
) -> tuple[Decimal, Decimal]:
    buffer = _minimum_protection_buffer(snapshot)
    entry_price = snapshot.ask if side == "long" else snapshot.bid
    if side == "long":
        structure_lows = [bar.low_price for bar in _recent_closed_bars(snapshot.bars_1m, limit=4)]
        structure_lows.extend(bar.low_price for bar in _recent_closed_bars(snapshot.bars_2m, limit=3))
        structure_lows.extend(bar.low_price for bar in _recent_closed_bars(snapshot.bars_3m, limit=2))
        stop_loss = (min(structure_lows) - buffer) if structure_lows else (entry_price - (buffer * Decimal("2")))
        max_valid_stop = snapshot.bid - buffer
        stop_loss = min(stop_loss, max_valid_stop)
        if stop_loss <= 0 or stop_loss >= entry_price:
            stop_loss = entry_price - (buffer * Decimal("2"))
        take_profit = entry_price + (entry_price - stop_loss)
        return stop_loss, take_profit

    structure_highs = [bar.high_price for bar in _recent_closed_bars(snapshot.bars_1m, limit=4)]
    structure_highs.extend(bar.high_price for bar in _recent_closed_bars(snapshot.bars_2m, limit=3))
    structure_highs.extend(bar.high_price for bar in _recent_closed_bars(snapshot.bars_3m, limit=2))
    stop_loss = (max(structure_highs) + buffer) if structure_highs else (entry_price + (buffer * Decimal("2")))
    min_valid_stop = snapshot.ask + buffer
    stop_loss = max(stop_loss, min_valid_stop)
    take_profit = entry_price - (stop_loss - entry_price)
    return stop_loss, take_profit


def _fast_entry_signal_key(*, snapshot: MT5V60BridgeSnapshot, decision: MT5V60EntryDecision) -> str:
    one_minute_end = snapshot.bars_1m[-1].end_at.isoformat() if snapshot.bars_1m else snapshot.server_time.isoformat()
    return f"{snapshot.symbol}:{one_minute_end}:{decision.action}"


def _fast_breakout_entry_decision(
    *,
    snapshot: MT5V60BridgeSnapshot,
    packet: dict[str, object],
) -> MT5V60EntryDecision | None:
    if str(packet.get("position_state", "flat")) != "flat":
        return None
    freshness = _coerce_dict(packet.get("freshness"))
    if str(freshness.get("source_snapshot_age_bucket", "")).strip().lower() not in {"fresh", "aging"}:
        return None

    quote = _coerce_dict(packet.get("quote"))
    spread_bps = _coerce_float(quote.get("spread_bps"), default=999.0)
    if spread_bps > 10.0:
        return None
    microstructure = _coerce_dict(packet.get("microstructure"))
    spread_to_atr = microstructure.get("spread_to_3m_atr_ratio")
    if spread_to_atr is not None and _coerce_float(spread_to_atr, default=0.0) > 0.65:
        return None

    signals = _coerce_dict(packet.get("entry_signals"))
    recent_bars = _coerce_dict(packet.get("recent_bars"))
    recent_1m = _coerce_list(recent_bars.get("1m"))
    recent_3m = _coerce_list(recent_bars.get("3m"))
    if not recent_1m or not recent_3m:
        return None

    bid = _coerce_float(quote.get("bid"))
    ask = _coerce_float(quote.get("ask"))
    midpoint = (bid + ask) / 2.0
    last_1m_close = _coerce_float(_coerce_dict(recent_1m[-1]).get("close"))
    last_3m_close = _coerce_float(_coerce_dict(recent_3m[-1]).get("close"))
    long_live_confirmation = midpoint >= min(last_1m_close, last_3m_close)
    short_live_confirmation = midpoint <= max(last_1m_close, last_3m_close)

    long_ready = _coerce_bool(signals.get("long_fast_entry_ready"))
    short_ready = _coerce_bool(signals.get("short_fast_entry_ready"))
    long_score = int(_coerce_float(signals.get("long_alignment_score")))
    short_score = int(_coerce_float(signals.get("short_alignment_score")))
    risk_tiers = _coerce_dict(signals.get("risk_tier"))
    backdrops = _coerce_dict(signals.get("five_minute_backdrop"))

    if long_ready and long_live_confirmation and long_score >= short_score + 2:
        tier = str(risk_tiers.get("long", "none"))
        if tier == "none":
            return None
        stop_loss, take_profit = _build_fast_entry_anchors(snapshot=snapshot, side="long")
        confidence = {"full": 0.78, "reduced": 0.68, "probe": 0.58}.get(tier, 0.58)
        risk_fraction = {"full": 0.0045, "reduced": 0.0025, "probe": 0.0015}.get(tier, 0.0015)
        return MT5V60EntryDecision(
            action="enter_long",
            confidence=confidence,
            rationale=(
                "Deterministic V6 fast-breakout entry: 3m execution structure is aligned with 2m and 1m bullish "
                f"breakout/continuation pressure, while 5m is treated as {backdrops.get('long', 'neutral')} backdrop only."
            ),
            thesis_tags=["fast_breakout", "lower_tf_aligned", "trend_continuation"],
            requested_risk_fraction=risk_fraction,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            context_signature=str(packet.get("context_signature") or "") or None,
        )

    if short_ready and short_live_confirmation and short_score >= long_score + 2:
        tier = str(risk_tiers.get("short", "none"))
        if tier == "none":
            return None
        stop_loss, take_profit = _build_fast_entry_anchors(snapshot=snapshot, side="short")
        confidence = {"full": 0.78, "reduced": 0.68, "probe": 0.58}.get(tier, 0.58)
        risk_fraction = {"full": 0.0045, "reduced": 0.0025, "probe": 0.0015}.get(tier, 0.0015)
        return MT5V60EntryDecision(
            action="enter_short",
            confidence=confidence,
            rationale=(
                "Deterministic V6 fast-breakout entry: 3m execution structure is aligned with 2m and 1m bearish "
                f"breakout/continuation pressure, while 5m is treated as {backdrops.get('short', 'neutral')} backdrop only."
            ),
            thesis_tags=["fast_breakdown", "lower_tf_aligned", "trend_continuation"],
            requested_risk_fraction=risk_fraction,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            context_signature=str(packet.get("context_signature") or "") or None,
        )

    return None


async def _process_acks(*, bridge_state: MT5V60BridgeState, registry: MT5V60TicketRegistry) -> None:
    for ack in await bridge_state.drain_acks():
        registry.record_ack(ack)


def _record_closed_tickets(
    *,
    closed_tickets: list[MT5V60TicketRecord],
    agent_name: str,
    reflection_journal: Journal,
    store: SupabaseMT5V60Store | None,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    logger,
) -> None:
    for closed_ticket in closed_tickets:
        reflection = build_mt5_v60_ticket_reflection(
            closed_ticket,
            exit_reason=closed_ticket.last_close_reason or "unknown",
        )
        reflections.append(reflection)
        new_lessons = derive_mt5_v60_lessons(reflection)
        lessons.extend(new_lessons)
        reflection_journal.record(
            {
                "record_type": "mt5_v60_trade_reflection",
                "agent_name": agent_name,
                "reflection": reflection.model_dump(mode="json"),
                "lessons": [lesson.model_dump(mode="json") for lesson in new_lessons],
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v60_trade_reflection",
                store.insert_mt5_v60_trade_reflection,
                agent_name=agent_name,
                reflection=reflection,
                ticket_id=closed_ticket.ticket_id,
                basket_id=closed_ticket.basket_id,
            )
            _safe_store_call(logger, "upsert_mt5_v60_lessons", store.upsert_lessons, new_lessons)


async def _execute_entry_decision(
    *,
    snapshot: MT5V60BridgeSnapshot,
    settings: V60Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V60Store | None,
    registry: MT5V60TicketRegistry,
    entry_builder: MT5V60ImmediateEntryBuilder,
    risk_arbiter: MT5V60RiskArbiter,
    bridge_state: MT5V60BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
    logger,
    decision: MT5V60EntryDecision,
    risk_posture: str,
    analysis_mode: str,
    decision_source: str,
    source_context: dict[str, object],
    raw_response: str,
    prompt_version: str,
    latency_ms: int,
) -> bool:
    execution_snapshot = _execution_snapshot(snapshot, await bridge_state.latest_snapshot())
    pending_symbol_command = await bridge_state.has_pending_symbol(execution_snapshot.symbol)
    _, multiplier = MT5V60RiskPostureEngine().derive(reflections)
    risk_decision = risk_arbiter.evaluate_entry(
        decision=decision,
        snapshot=execution_snapshot,
        registry=registry,
        risk_posture=risk_posture,
        risk_multiplier=multiplier,
        pending_symbol_command=pending_symbol_command,
        allow_stale_snapshot=True,
    )
    event_journal.record(
        {
            "record_type": "mt5_v60_entry_analysis",
            "agent_name": agent_name,
            "decision": decision.model_dump(mode="json"),
            "analysis_mode": analysis_mode,
            "decision_source": decision_source,
            "risk_decision": risk_decision.model_dump(mode="json"),
            "latency_ms": latency_ms,
            "raw_llm_response": raw_response,
        }
    )
    if store is not None:
        _safe_store_call(
            logger,
            "insert_mt5_v60_runtime_decision_entry",
            store.insert_mt5_v60_runtime_decision,
            agent_name=agent_name,
            decision_kind="entry",
            symbol=snapshot.symbol,
            action=decision.action,
            confidence=decision.confidence,
            rationale=decision.rationale,
            risk_posture=risk_posture,
            risk_approved=risk_decision.approved,
            risk_reason=risk_decision.reason,
            context_payload=source_context,
            decision_payload={
                "analysis_mode": analysis_mode,
                "decision_source": decision_source,
                "decision": decision.model_dump(mode="json"),
                "raw_response": raw_response,
                "prompt_version": prompt_version,
                "latency_ms": latency_ms,
            },
        )
    if not risk_decision.approved:
        return False
    build_outcome = entry_builder.build(
        decision=decision,
        snapshot=execution_snapshot,
        risk_decision=risk_decision,
        analysis_mode=analysis_mode,
        ticket_sequence=1,
    )
    if build_outcome.command is None or build_outcome.plan_payload is None:
        reason = build_outcome.rejection_reason or "immediate_entry_builder_returned_none"
        logger.info("v6_0_entry_skipped reason=%s symbol=%s analysis_mode=%s", reason, snapshot.symbol, analysis_mode)
        event_journal.record(
            {
                "record_type": "mt5_v60_entry_command_skipped",
                "agent_name": agent_name,
                "analysis_mode": analysis_mode,
                "reason": reason,
                "decision": decision.model_dump(mode="json"),
            }
        )
        return False
    command = build_outcome.command
    plan_payload = dict(build_outcome.plan_payload)
    followed_lessons = _recent_lessons_for_latest_reflections(reflections=reflections, lessons=lessons)
    metadata_update = {
        **command.metadata,
        "analysis_mode": analysis_mode,
        "decision_source": decision_source,
        "source_server_time": snapshot.server_time.isoformat(),
        "screenshot_fingerprint": source_context.get("screenshot", {}).get("fingerprint") if isinstance(source_context.get("screenshot"), dict) else None,
        "followed_lessons": followed_lessons,
        "thesis_tags": decision.thesis_tags,
        "context_signature": decision.context_signature or str(source_context.get("context_signature") or "") or None,
    }
    command = command.model_copy(
        update={
            "expires_at": _entry_command_expires_at(snapshot, stale_after_seconds=settings.v60_stale_after_seconds),
            "metadata": metadata_update,
        }
    )
    plan_payload.update(
        {
            "thesis_tags": decision.thesis_tags,
            "context_signature": decision.context_signature,
            "followed_lessons": followed_lessons,
            "analysis_mode": analysis_mode,
            "decision_source": decision_source,
            "metadata": metadata_update,
        }
    )
    risk_arbiter.record_approved_entry(execution_snapshot.server_time)
    if shadow_mode:
        event_journal.record(
            {
                "record_type": "mt5_v60_shadow_command",
                "agent_name": agent_name,
                "command_source": analysis_mode,
                "command": command.model_dump(mode="json"),
            }
        )
        return True
    registry.register_pending_entry(command=command, plan_payload=plan_payload)
    await bridge_state.queue_command(command)
    event_journal.record(
        {
            "record_type": "mt5_v60_bridge_command_enqueued",
            "agent_name": agent_name,
            "command_source": analysis_mode,
            "command": command.model_dump(mode="json"),
        }
    )
    if store is not None:
        _safe_store_call(
            logger,
            "insert_mt5_v60_bridge_command_entry",
            store.insert_mt5_v60_bridge_command,
            agent_name=agent_name,
            command=command,
            bridge_id=settings.v60_bridge_id,
        )
    return True


async def _run_entry_cycle(
    *,
    snapshot: MT5V60BridgeSnapshot,
    settings: V60Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V60Store | None,
    registry: MT5V60TicketRegistry,
    entry_builder: MT5V60ImmediateEntryBuilder,
    risk_arbiter: MT5V60RiskArbiter,
    context_builder: MT5V60ContextBuilder,
    posture_engine: MT5V60RiskPostureEngine,
    bridge_state: MT5V60BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    screenshot_state: MT5V60ScreenshotState,
    entry_agent: MT5V60EntryAnalystAgent,
    shadow_mode: bool,
    logger,
    analysis_mode: str,
    reversal_context: dict[str, object] | None = None,
) -> bool:
    if risk_arbiter.snapshot_is_stale(snapshot):
        return False
    if registry.has_open_position(snapshot.symbol):
        return False
    if await bridge_state.has_pending_symbol(snapshot.symbol):
        return False
    risk_posture, _ = posture_engine.derive(reflections)
    packet = context_builder.build_entry_packet(
        snapshot=snapshot,
        registry=registry,
        screenshot_state=screenshot_state,
        reversal_context=reversal_context,
    )
    image_path = screenshot_state.absolute_path if screenshot_state.absolute_path and Path(screenshot_state.absolute_path).exists() else None
    try:
        result = await asyncio.wait_for(
            entry_agent.analyze(packet, image_path=image_path),
            timeout=float(settings.v60_mt5_entry_timeout_seconds),
        )
    except asyncio.TimeoutError:
        event_journal.record(
            {
                "record_type": "mt5_v60_entry_failure",
                "agent_name": agent_name,
                "analysis_mode": analysis_mode,
                "error": "timeout",
            }
        )
        logger.warning("v6_0_entry_timeout analysis_mode=%s symbol=%s", analysis_mode, snapshot.symbol)
        return False
    except Exception as exc:
        event_journal.record(
            {
                "record_type": "mt5_v60_entry_failure",
                "agent_name": agent_name,
                "analysis_mode": analysis_mode,
                "error": str(exc),
            }
        )
        logger.error("v6_0_entry_error analysis_mode=%s symbol=%s error=%s", analysis_mode, snapshot.symbol, exc)
        return False
    event_journal.record(
        {
            "record_type": "mt5_v60_entry_response",
            "agent_name": agent_name,
            "decision": result.decision.model_dump(mode="json"),
            "analysis_mode": analysis_mode,
            "latency_ms": result.latency_ms,
            "raw_llm_response": result.raw_response,
            "image_attached": image_path is not None,
        }
    )
    if result.decision.action == "hold":
        return False
    return await _execute_entry_decision(
        snapshot=snapshot,
        settings=settings,
        agent_name=agent_name,
        event_journal=event_journal,
        store=store,
        registry=registry,
        entry_builder=entry_builder,
        risk_arbiter=risk_arbiter,
        bridge_state=bridge_state,
        reflections=reflections,
        lessons=lessons,
        shadow_mode=shadow_mode,
        logger=logger,
        decision=result.decision,
        risk_posture=risk_posture,
        analysis_mode=analysis_mode,
        decision_source="llm_entry",
        source_context=packet,
        raw_response=result.raw_response,
        prompt_version=entry_agent.prompt_version,
        latency_ms=result.latency_ms,
    )


async def _run_fast_entry_cycle(
    *,
    snapshot: MT5V60BridgeSnapshot,
    settings: V60Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V60Store | None,
    registry: MT5V60TicketRegistry,
    entry_builder: MT5V60ImmediateEntryBuilder,
    risk_arbiter: MT5V60RiskArbiter,
    context_builder: MT5V60ContextBuilder,
    posture_engine: MT5V60RiskPostureEngine,
    bridge_state: MT5V60BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    screenshot_state: MT5V60ScreenshotState,
    shadow_mode: bool,
    logger,
    last_signal_key: str | None,
) -> tuple[bool, str | None]:
    if risk_arbiter.snapshot_is_stale(snapshot):
        return False, last_signal_key
    if registry.has_open_position(snapshot.symbol):
        return False, last_signal_key
    if await bridge_state.has_pending_symbol(snapshot.symbol):
        return False, last_signal_key

    risk_posture, _ = posture_engine.derive(reflections)
    packet = context_builder.build_entry_packet(
        snapshot=snapshot,
        registry=registry,
        screenshot_state=screenshot_state,
    )
    decision = _fast_breakout_entry_decision(snapshot=snapshot, packet=packet)
    if decision is None:
        return False, last_signal_key

    signal_key = _fast_entry_signal_key(snapshot=snapshot, decision=decision)
    if signal_key == last_signal_key:
        return False, last_signal_key

    event_journal.record(
        {
            "record_type": "mt5_v60_fast_entry_signal",
            "agent_name": agent_name,
            "decision": decision.model_dump(mode="json"),
            "decision_source": "deterministic_fast_breakout",
            "analysis_mode": "standard_entry",
            "context": packet,
            "signal_key": signal_key,
        }
    )

    executed = await _execute_entry_decision(
        snapshot=snapshot,
        settings=settings,
        agent_name=agent_name,
        event_journal=event_journal,
        store=store,
        registry=registry,
        entry_builder=entry_builder,
        risk_arbiter=risk_arbiter,
        bridge_state=bridge_state,
        reflections=reflections,
        lessons=lessons,
        shadow_mode=shadow_mode,
        logger=logger,
        decision=decision,
        risk_posture=risk_posture,
        analysis_mode="standard_entry",
        decision_source="deterministic_fast_breakout",
        source_context=packet,
        raw_response="deterministic_fast_breakout_rule",
        prompt_version="v6.0_fast_breakout_v1",
        latency_ms=0,
    )
    return executed, signal_key


def _deterministic_management_commands(
    *,
    snapshot: MT5V60BridgeSnapshot,
    packet: dict[str, object],
    ticket: MT5V60TicketRecord,
    ticket_payload: dict[str, object],
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    same_pressure, opposite_pressure = _aggregate_pressure_scores(packet, ticket_side=ticket.side)
    drawdown_from_peak_r = _coerce_float(ticket_payload.get("drawdown_from_peak_r"))
    max_favorable_r = _coerce_float(ticket_payload.get("max_favorable_r"))
    volume_remaining_fraction = _coerce_float(ticket_payload.get("volume_remaining_fraction"), default=1.0)

    if ticket.partial_stage <= 1 and (ticket.unrealized_r >= 0.70 or (max_favorable_r >= 0.85 and drawdown_from_peak_r >= 0.18)):
        if volume_remaining_fraction > 0.35:
            commands.append(
                {
                    "action": "close_partial",
                    "close_fraction": Decimal("0.60"),
                    "reason": "Stage-two scalp management: lock a larger partial once the move extends.",
                    "metadata": {"action": "stage_two_partial"},
                }
            )
        desired_stop = _more_protective_stop(
            ticket,
            ticket.stop_loss or ticket.initial_stop_loss,
            _more_protective_stop(ticket, _locked_stop_at_r(ticket, Decimal("0.05")), _structure_trailing_stop(snapshot, ticket)),
        )
        commands.append(
            {
                "action": "modify_ticket",
                "stop_loss_price": desired_stop,
                "take_profit_price": ticket.take_profit,
                "reason": "Stage-two scalp protection: trail the stop to breakeven-or-better after extension.",
                "metadata": {"action": "stage_two_trail"},
            }
        )
        return commands

    if ticket.partial_stage == 0 and (ticket.unrealized_r >= 0.35 or (max_favorable_r >= 0.45 and drawdown_from_peak_r >= 0.12)):
        commands.append(
            {
                "action": "close_partial",
                "close_fraction": Decimal("0.25"),
                "reason": "Stage-one scalp management: bank a starter partial after the breakout starts paying.",
                "metadata": {"action": "stage_one_partial"},
            }
        )
        desired_stop = _more_protective_stop(
            ticket,
            ticket.stop_loss or ticket.initial_stop_loss,
            _locked_stop_at_r(ticket, Decimal("-0.55")),
        )
        commands.append(
            {
                "action": "modify_ticket",
                "stop_loss_price": desired_stop,
                "take_profit_price": ticket.take_profit,
                "reason": "Stage-one scalp protection: cut open risk after the first favorable push.",
                "metadata": {"action": "stage_one_trail"},
            }
        )
        return commands

    if ticket.unrealized_r >= 0.55 or ticket.partial_stage >= 1:
        desired_stop = _more_protective_stop(
            ticket,
            ticket.stop_loss or ticket.initial_stop_loss,
            _structure_trailing_stop(snapshot, ticket),
        )
        if ticket.stop_loss is None or desired_stop != ticket.stop_loss:
            commands.append(
                {
                    "action": "modify_ticket",
                    "stop_loss_price": desired_stop,
                    "take_profit_price": ticket.take_profit,
                    "reason": "Active scalp trail: keep tightening protection while the move stays favorable.",
                    "metadata": {"action": "active_trailing_stop"},
                }
            )

    if (
        ticket.unrealized_r > 0.20
        and drawdown_from_peak_r >= 0.25
        and opposite_pressure >= same_pressure + 4
    ):
        if volume_remaining_fraction > 0.35:
            commands.insert(
                0,
                {
                    "action": "close_partial",
                    "close_fraction": Decimal("0.50"),
                    "reason": "Profit-defense trim: the lower timeframes are reversing hard against the open profit.",
                    "metadata": {"action": "reversal_trim"},
                },
            )
            desired_stop = _more_protective_stop(
                ticket,
                ticket.stop_loss or ticket.initial_stop_loss,
                _locked_stop_at_r(ticket, Decimal("0.00")),
            )
            commands.append(
                {
                    "action": "modify_ticket",
                    "stop_loss_price": desired_stop,
                    "take_profit_price": ticket.take_profit,
                    "reason": "Profit-defense stop move: protect the trade after a meaningful giveback from peak.",
                    "metadata": {"action": "reversal_trail"},
                }
            )
        else:
            commands = [
                {
                    "action": "close_ticket",
                    "reason": "Profit-defense exit: the lower timeframes reversed hard after a favorable excursion.",
                    "metadata": {"action": "reversal_exit"},
                }
            ]
        return commands

    if ticket.unrealized_r <= -0.45 and opposite_pressure >= same_pressure + 5:
        return [
            {
                "action": "close_ticket",
                "reason": "Loss-defense exit: the 1m, 2m, and 3m structure turned clearly against the trade before the full stop was needed.",
                "metadata": {"action": "loss_defense_exit"},
            }
        ]

    return commands


async def _run_deterministic_management_cycle(
    *,
    snapshot: MT5V60BridgeSnapshot,
    settings: V60Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V60Store | None,
    registry: MT5V60TicketRegistry,
    planner: MT5V60EntryPlanner,
    context_builder: MT5V60ContextBuilder,
    posture_engine: MT5V60RiskPostureEngine,
    bridge_state: MT5V60BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    screenshot_state: MT5V60ScreenshotState,
    shadow_mode: bool,
    logger,
) -> bool:
    tickets = registry.all(snapshot.symbol)
    if not tickets:
        return False
    if await bridge_state.has_pending_symbol(snapshot.symbol):
        return False

    risk_posture, _ = posture_engine.derive(reflections)
    allowed_actions = {ticket.ticket_id: registry.allowed_actions(ticket.ticket_id) for ticket in tickets}
    packet = context_builder.build_manager_packet(
        snapshot=snapshot,
        registry=registry,
        allowed_actions=allowed_actions,
        risk_posture=risk_posture,
        reflections=reflections,
        lessons=lessons,
        screenshot_state=screenshot_state,
        include_raw_screenshot=False,
    )
    ticket_payloads = {
        str(payload.get("ticket_id")): payload
        for payload in packet.get("tickets", [])
        if isinstance(payload, dict) and isinstance(payload.get("ticket_id"), str)
    }

    for ticket in tickets:
        ticket_payload = ticket_payloads.get(ticket.ticket_id)
        if ticket_payload is None:
            continue
        commands_to_apply = _deterministic_management_commands(
            snapshot=snapshot,
            packet=packet,
            ticket=ticket,
            ticket_payload=ticket_payload,
        )
        if not commands_to_apply:
            continue

        built_commands: list[MT5V60BridgeCommand] = []
        rationale = commands_to_apply[0]["reason"]
        for command_spec in commands_to_apply:
            command = None
            action = str(command_spec.get("action"))
            if action == "modify_ticket":
                command = planner.build_modify_command(
                    ticket=ticket,
                    snapshot=snapshot,
                    stop_loss=command_spec.get("stop_loss_price"),
                    take_profit=command_spec.get("take_profit_price"),
                    reason=str(command_spec.get("reason", rationale)),
                    created_at=snapshot.server_time,
                    expires_at=snapshot.server_time + timedelta(seconds=45),
                    metadata=dict(command_spec.get("metadata") or {}),
                )
            elif action == "close_partial":
                close_fraction = command_spec.get("close_fraction")
                if isinstance(close_fraction, Decimal):
                    close_volume = planner.partial_close_volume(
                        original_volume_lots=ticket.current_volume_lots,
                        close_fraction=close_fraction,
                        snapshot=snapshot,
                    )
                    command = planner.build_close_command(
                        ticket=ticket,
                        volume_lots=close_volume,
                        reason=str(command_spec.get("reason", rationale)),
                        created_at=snapshot.server_time,
                        expires_at=snapshot.server_time + timedelta(seconds=45),
                        metadata={
                            **dict(command_spec.get("metadata") or {}),
                            "close_fraction": float(close_fraction),
                        },
                    )
            elif action == "close_ticket":
                command = planner.build_close_command(
                    ticket=ticket,
                    volume_lots=ticket.current_volume_lots,
                    reason=str(command_spec.get("reason", rationale)),
                    created_at=snapshot.server_time,
                    expires_at=snapshot.server_time + timedelta(seconds=45),
                    metadata=dict(command_spec.get("metadata") or {}),
                )
            if command is not None:
                built_commands.append(command)

        if not built_commands:
            continue

        event_journal.record(
            {
                "record_type": "mt5_v60_management_decision",
                "agent_name": agent_name,
                "ticket_id": ticket.ticket_id,
                "decision_source": "deterministic_scalp_guard",
                "decisions": [command.model_dump(mode="json") for command in built_commands],
                "risk_approved": True,
                "risk_reason": "Deterministic V6 scalp management rule fired.",
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v60_runtime_decision_management_deterministic",
                store.insert_mt5_v60_runtime_decision,
                agent_name=agent_name,
                decision_kind="management",
                symbol=snapshot.symbol,
                action="deterministic_scalp_guard",
                confidence=1.0,
                rationale=rationale,
                risk_posture=risk_posture,
                risk_approved=True,
                risk_reason="Deterministic V6 scalp management rule fired.",
                context_payload=packet,
                decision_payload={
                    "decision_source": "deterministic_scalp_guard",
                    "commands": [command.model_dump(mode="json") for command in built_commands],
                },
            )

        if shadow_mode:
            for command in built_commands:
                event_journal.record(
                    {
                        "record_type": "mt5_v60_shadow_management_command",
                        "agent_name": agent_name,
                        "command": command.model_dump(mode="json"),
                    }
                )
            return True

        for command in built_commands:
            await bridge_state.queue_command(command)
            event_journal.record(
                {
                    "record_type": "mt5_v60_bridge_command_enqueued",
                    "agent_name": agent_name,
                    "command_source": "deterministic_scalp_guard",
                    "command": command.model_dump(mode="json"),
                }
            )
            if store is not None:
                _safe_store_call(
                    logger,
                    "insert_mt5_v60_bridge_command_management_deterministic",
                    store.insert_mt5_v60_bridge_command,
                    agent_name=agent_name,
                    command=command,
                    bridge_id=settings.v60_bridge_id,
                )
        return True

    return False


async def _run_manager_cycle(
    *,
    snapshot: MT5V60BridgeSnapshot,
    settings: V60Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V60Store | None,
    registry: MT5V60TicketRegistry,
    planner: MT5V60EntryPlanner,
    context_builder: MT5V60ContextBuilder,
    posture_engine: MT5V60RiskPostureEngine,
    bridge_state: MT5V60BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    screenshot_state: MT5V60ScreenshotState,
    manager_agent: MT5V60PositionManagerAgent,
    shadow_mode: bool,
    logger,
) -> MT5V60ScreenshotState:
    tickets = registry.all(snapshot.symbol)
    if not tickets:
        return screenshot_state
    if await bridge_state.has_pending_symbol(snapshot.symbol):
        return screenshot_state
    include_raw_image = _manager_should_attach_raw_image(screenshot_state=screenshot_state)
    risk_posture, _ = posture_engine.derive(reflections)
    allowed_actions = {ticket.ticket_id: registry.allowed_actions(ticket.ticket_id) for ticket in tickets}
    packet = context_builder.build_manager_packet(
        snapshot=snapshot,
        registry=registry,
        allowed_actions=allowed_actions,
        risk_posture=risk_posture,
        reflections=reflections,
        lessons=lessons,
        screenshot_state=screenshot_state,
        include_raw_screenshot=include_raw_image,
    )
    image_path = screenshot_state.absolute_path if include_raw_image else None
    try:
        result = await manager_agent.analyze(packet, image_path=image_path)
    except Exception as exc:
        logger.error("v6_0_manager_error symbol=%s error=%s", snapshot.symbol, exc)
        return screenshot_state

    visual_context_update = _extract_visual_context_update(result.decision_batch)
    if include_raw_image:
        screenshot_state = _advance_manager_screenshot_state(
            screenshot_state=screenshot_state,
            delivery_succeeded=True,
            visual_context_update=visual_context_update,
        )

    event_journal.record(
        {
            "record_type": "mt5_v60_management_decision",
            "agent_name": agent_name,
            "decisions": result.decision_batch.model_dump(mode="json"),
            "image_attached": include_raw_image,
            "latency_ms": result.latency_ms,
        }
    )
    if store is not None:
        _safe_store_call(
            logger,
            "insert_mt5_v60_runtime_decision_management",
            store.insert_mt5_v60_runtime_decision,
            agent_name=agent_name,
            decision_kind="management",
            symbol=snapshot.symbol,
            action="management_batch",
            confidence=1.0,
            rationale="Manager sweep completed.",
            risk_posture=risk_posture,
            risk_approved=None,
            risk_reason=None,
            context_payload=packet,
            decision_payload={
                "decision_batch": result.decision_batch.model_dump(mode="json"),
                "raw_response": result.raw_response,
                "prompt_version": manager_agent.prompt_version,
                "latency_ms": result.latency_ms,
                "image_attached": include_raw_image,
            },
        )
    execution_snapshot = _execution_snapshot(snapshot, await bridge_state.latest_snapshot(snapshot.symbol))
    has_fresher_execution_snapshot = execution_snapshot is not snapshot
    live_tickets_by_id = {live_ticket.ticket_id: live_ticket for live_ticket in execution_snapshot.open_tickets}
    for decision in result.decision_batch.decisions:
        ticket = registry.by_ticket_id(decision.ticket_id)
        if ticket is None:
            continue
        live_ticket = live_tickets_by_id.get(decision.ticket_id)
        if live_ticket is None and has_fresher_execution_snapshot:
            close_event = next(
                (
                    event
                    for event in reversed(execution_snapshot.recent_close_events)
                    if (event.ticket_id and event.ticket_id == ticket.ticket_id)
                    or (ticket.basket_id and event.basket_id and event.basket_id == ticket.basket_id)
                ),
                None,
            )
            event_journal.record(
                {
                    "record_type": "mt5_v60_management_command_skipped",
                    "agent_name": agent_name,
                    "ticket_id": ticket.ticket_id,
                    "requested_action": "decision_batch",
                    "effective_action": "ticket_not_open",
                    "requested_command": decision.model_dump(mode="json"),
                    "rationale": decision.rationale,
                    "skip_reason": "ticket_not_open_in_latest_snapshot",
                    "latest_snapshot_server_time": execution_snapshot.server_time.isoformat(),
                    "close_event": (close_event.model_dump(mode="json") if close_event is not None else None),
                }
            )
            continue
        execution_ticket = (
            ticket
            if live_ticket is None
            else ticket.model_copy(
                update={
                    "current_volume_lots": live_ticket.volume_lots,
                    "current_price": (live_ticket.current_price or live_ticket.open_price),
                    "stop_loss": live_ticket.stop_loss,
                    "take_profit": live_ticket.take_profit,
                    "last_seen_at": execution_snapshot.server_time,
                    "unrealized_pnl_usd": live_ticket.unrealized_pnl_usd,
                }
            )
        )
        allowed = set(allowed_actions.get(execution_ticket.ticket_id, ["hold"]))
        effective_actions = [
            _effective_management_action(command_spec=command_spec, ticket=execution_ticket)
            for command_spec in decision.commands
        ]
        reviewed_first_protection_keep = bool(decision.commands) and all(action == "hold" for action in effective_actions)
        reviewed_first_protection_move = False
        for command_spec, effective_action in zip(decision.commands, effective_actions, strict=False):
            if effective_action == "hold":
                continue
            if effective_action not in allowed:
                continue
            command = None
            if effective_action == "modify_ticket":
                command = planner.build_modify_command(
                    ticket=execution_ticket,
                    snapshot=execution_snapshot,
                    stop_loss=command_spec.stop_loss_price,
                    take_profit=command_spec.take_profit_price,
                    reason=decision.rationale,
                    created_at=execution_snapshot.server_time,
                    expires_at=execution_snapshot.server_time + timedelta(seconds=60),
                    metadata={"action": "modify_ticket", "source_action": command_spec.action},
                )
            elif effective_action == "close_partial" and command_spec.close_fraction is not None:
                close_volume = planner.partial_close_volume(
                    original_volume_lots=execution_ticket.current_volume_lots,
                    close_fraction=Decimal(str(command_spec.close_fraction)),
                    snapshot=execution_snapshot,
                )
                command = planner.build_close_command(
                    ticket=execution_ticket,
                    volume_lots=close_volume,
                    reason=decision.rationale,
                    created_at=execution_snapshot.server_time,
                    expires_at=execution_snapshot.server_time + timedelta(seconds=60),
                    metadata={
                        "action": "close_partial",
                        "close_fraction": command_spec.close_fraction,
                        "source_action": command_spec.action,
                    },
                )
            elif effective_action == "close_ticket":
                command = planner.build_close_command(
                    ticket=execution_ticket,
                    volume_lots=execution_ticket.current_volume_lots,
                    reason=decision.rationale,
                    created_at=execution_snapshot.server_time,
                    expires_at=execution_snapshot.server_time + timedelta(seconds=60),
                    metadata={"action": "close_ticket", "source_action": command_spec.action},
                )
            if command is None:
                event_journal.record(
                    {
                        "record_type": "mt5_v60_management_command_skipped",
                        "agent_name": agent_name,
                        "ticket_id": ticket.ticket_id,
                        "requested_action": command_spec.action,
                        "effective_action": effective_action,
                        "requested_command": command_spec.model_dump(mode="json"),
                        "rationale": decision.rationale,
                    }
                )
                continue
            if shadow_mode:
                event_journal.record(
                    {
                    "record_type": "mt5_v60_shadow_management_command",
                    "agent_name": agent_name,
                    "command": command.model_dump(mode="json"),
                }
            )
                if effective_action == "modify_ticket":
                    reviewed_first_protection_move = True
                continue
            await bridge_state.queue_command(command)
            event_journal.record(
                {
                    "record_type": "mt5_v60_bridge_command_enqueued",
                    "agent_name": agent_name,
                    "command_source": "manager",
                    "command": command.model_dump(mode="json"),
                }
            )
            if store is not None:
                _safe_store_call(
                    logger,
                    "insert_mt5_v60_bridge_command_manager",
                    store.insert_mt5_v60_bridge_command,
                    agent_name=agent_name,
                    command=command,
                    bridge_id=settings.v60_bridge_id,
                )
            if effective_action == "modify_ticket":
                reviewed_first_protection_move = True
        if execution_ticket.first_protection_review_pending:
            if reviewed_first_protection_move:
                registry.record_first_protection_review(
                    execution_ticket.ticket_id,
                    outcome="moved",
                    reviewed_at=execution_snapshot.server_time,
                )
            elif reviewed_first_protection_keep:
                registry.record_first_protection_review(
                    execution_ticket.ticket_id,
                    outcome="kept",
                    reviewed_at=execution_snapshot.server_time,
                )
    return screenshot_state


async def _run_entry_protection_cycle(
    *,
    snapshot: MT5V60BridgeSnapshot,
    settings: V60Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V60Store | None,
    registry: MT5V60TicketRegistry,
    planner: MT5V60EntryPlanner,
    bridge_state: MT5V60BridgeState,
    shadow_mode: bool,
    logger,
) -> bool:
    tickets = registry.all(snapshot.symbol)
    if not tickets:
        return False
    if await bridge_state.has_pending_symbol(snapshot.symbol):
        return False

    for ticket in tickets:
        if ticket.stop_loss is not None and ticket.take_profit is not None:
            continue
        command = planner.build_modify_command(
            ticket=ticket,
            snapshot=snapshot,
            stop_loss=ticket.initial_stop_loss,
            take_profit=ticket.hard_take_profit,
            reason=(
                "Attach the first automatic protection from the internal entry anchors after a naked fill. "
                "Manager must review this placement and either keep it or move it."
            ),
            created_at=snapshot.server_time,
            expires_at=snapshot.server_time + timedelta(seconds=60),
            metadata={"action": "attach_first_protection_auto"},
        )
        if command is None:
            continue
        if shadow_mode:
            event_journal.record(
                {
                    "record_type": "mt5_v60_shadow_management_command",
                    "agent_name": agent_name,
                    "command_source": "entry_protection",
                    "command": command.model_dump(mode="json"),
                }
            )
            return True
        await bridge_state.queue_command(command)
        event_journal.record(
            {
                "record_type": "mt5_v60_bridge_command_enqueued",
                "agent_name": agent_name,
                "command_source": "entry_protection",
                "command": command.model_dump(mode="json"),
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v60_bridge_command_entry_protection",
                store.insert_mt5_v60_bridge_command,
                agent_name=agent_name,
                command=command,
                bridge_id=settings.v60_bridge_id,
            )
        return True
    return False


async def _shutdown_flatten_open_tickets(
    *,
    settings: V60Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V60Store | None,
    registry: MT5V60TicketRegistry,
    bridge_state: MT5V60BridgeState,
    shadow_mode: bool,
    logger,
) -> None:
    tickets = registry.all(settings.v60_mt5_symbol)
    if not tickets or shadow_mode:
        return
    snapshot = await bridge_state.latest_snapshot()
    if snapshot is None:
        return
    planner = MT5V60EntryPlanner()
    for ticket in tickets:
        command = planner.build_close_command(
            ticket=ticket,
            volume_lots=ticket.current_volume_lots,
            reason="Timed V6.0 MT5 demo session shutdown flatten.",
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
            _safe_store_call(
                logger,
                "insert_mt5_v60_bridge_command_shutdown_flatten",
                store.insert_mt5_v60_bridge_command,
                agent_name=agent_name,
                command=command,
                bridge_id=settings.v60_bridge_id,
            )


async def _start_bridge_server(*, app, host: str, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.05)
    return server, task


async def run() -> None:
    args = _parse_args()
    settings = get_v60_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)

    if settings.openai_api_key is None:
        raise RuntimeError("V60_OPENAI_API_KEY is required for the V6.0 MT5 runtime.")

    session_tag = args.session_tag or datetime.now(timezone.utc).strftime("v6-0-mt5-%Y%m%d-%H%M%S")
    artifact_dir = Path("var/v6_0") / session_tag
    artifact_dir.mkdir(parents=True, exist_ok=True)
    event_journal = Journal(str(artifact_dir / "events.jsonl"))
    reflection_journal = Journal(str(artifact_dir / "trade_reflections.jsonl"))
    store = SupabaseMT5V60Store(settings.supabase_db_dsn) if settings.supabase_db_dsn is not None else None

    bridge_state = MT5V60BridgeState(settings.v60_bridge_id)
    bridge_app = create_mt5_v60_bridge_app(
        bridge_state,
        journal=event_journal,
        store=store,
        agent_name=args.agent_name or settings.v60_agent_name,
    )
    bridge_server, bridge_task = await _start_bridge_server(
        app=bridge_app,
        host=args.bridge_host or settings.v60_bridge_host,
        port=args.bridge_port or settings.v60_bridge_port,
    )

    entry_agent = MT5V60EntryAnalystAgent(
        api_key=settings.openai_api_key,
        model=settings.v60_openai_model,
        base_url=settings.v60_openai_base_url,
        reasoning_effort=settings.v60_entry_reasoning_effort,
    )
    manager_agent = MT5V60PositionManagerAgent(
        api_key=settings.openai_api_key,
        model=settings.v60_openai_model,
        base_url=settings.v60_openai_base_url,
        reasoning_effort=settings.manager_reasoning_effort,
    )
    planner = MT5V60EntryPlanner()
    entry_builder = MT5V60ImmediateEntryBuilder()
    context_builder = MT5V60ContextBuilder()
    recent_entry_times = (
        store.list_recent_approved_entry_times(
            symbol=settings.v60_mt5_symbol,
            since=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        if store is not None
        else []
    )
    risk_arbiter = MT5V60RiskArbiter(
        symbol=settings.v60_mt5_symbol,
        account_mode=settings.v60_mt5_account_mode,
        min_confidence=settings.v60_min_decision_confidence,
        max_spread_bps=settings.v60_max_spread_bps,
        stale_after_seconds=settings.v60_stale_after_seconds,
        min_risk_fraction=settings.v60_min_risk_fraction,
        max_risk_fraction=settings.v60_max_risk_fraction,
        daily_loss_pct=settings.v60_max_daily_loss_pct,
        max_trades_per_hour=settings.v60_max_trades_per_hour,
        seeded_entry_times=recent_entry_times,
    )
    posture_engine = MT5V60RiskPostureEngine()
    registry = MT5V60TicketRegistry(store=store)
    if store is not None:
        registry.seed(store.list_open_ticket_states(symbol=settings.v60_mt5_symbol))
    reflections: list[TradeReflection] = store.list_recent_trade_reflections(symbol=settings.v60_mt5_symbol, limit=10) if store is not None else []
    lessons: list[LessonRecord] = store.list_recent_lessons(limit=20) if store is not None else []

    last_entry_bar_end: datetime | None = None
    last_manager_run_at: datetime | None = None
    last_fast_entry_key: str | None = None
    screenshot_state = MT5V60ScreenshotState(absolute_path=settings.screenshot_absolute_path)
    commands_enabled = args.enable_trade_commands or settings.v60_mt5_enable_trade_commands
    shadow_mode = settings.v60_mt5_shadow_mode or not commands_enabled
    if args.enable_trade_commands:
        shadow_mode = False
    if args.shadow_mode:
        shadow_mode = True
    end_at = datetime.now(timezone.utc) + timedelta(minutes=args.duration_minutes) if args.duration_minutes > 0 else None

    logger.info(
        "v6_0_mt5_start session_tag=%s symbol=%s bridge=%s:%s shadow_mode=%s",
        session_tag,
        settings.v60_mt5_symbol,
        args.bridge_host or settings.v60_bridge_host,
        args.bridge_port or settings.v60_bridge_port,
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

            context_builder.observe_snapshot(snapshot)
            screenshot_state = _sync_screenshot_state(snapshot=snapshot, settings=settings, current=screenshot_state)
            await _process_acks(bridge_state=bridge_state, registry=registry)
            sync_result = registry.sync(snapshot)
            _record_closed_tickets(
                closed_tickets=sync_result.closed,
                agent_name=args.agent_name or settings.v60_agent_name,
                reflection_journal=reflection_journal,
                store=store,
                reflections=reflections,
                lessons=lessons,
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
                        agent_name=args.agent_name or settings.v60_agent_name,
                        event_journal=event_journal,
                        store=store,
                        registry=registry,
                        entry_builder=entry_builder,
                        risk_arbiter=risk_arbiter,
                        context_builder=context_builder,
                        posture_engine=posture_engine,
                        bridge_state=bridge_state,
                        reflections=reflections,
                        lessons=lessons,
                        screenshot_state=screenshot_state,
                        entry_agent=entry_agent,
                        shadow_mode=shadow_mode,
                        logger=logger,
                        analysis_mode="stop_loss_reversal",
                        reversal_context=_reversal_context(closed_ticket),
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
                    agent_name=args.agent_name or settings.v60_agent_name,
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
                    agent_name=args.agent_name or settings.v60_agent_name,
                    event_journal=event_journal,
                    store=store,
                    registry=registry,
                    planner=planner,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    bridge_state=bridge_state,
                    reflections=reflections,
                    lessons=lessons,
                    screenshot_state=screenshot_state,
                    shadow_mode=shadow_mode,
                    logger=logger,
                )
                if deterministic_managed:
                    continue
                tickets = registry.all(snapshot.symbol)
                manager_due = (
                    last_manager_run_at is None
                    or snapshot.server_time >= last_manager_run_at + timedelta(seconds=settings.v60_mt5_manager_sweep_seconds)
                    or (snapshot_updated and any(ticket.first_protection_review_pending for ticket in tickets))
                )
                if manager_due:
                    screenshot_state = await _run_manager_cycle(
                        snapshot=snapshot,
                        settings=settings,
                        agent_name=args.agent_name or settings.v60_agent_name,
                        event_journal=event_journal,
                        store=store,
                        registry=registry,
                        planner=planner,
                        context_builder=context_builder,
                        posture_engine=posture_engine,
                        bridge_state=bridge_state,
                        reflections=reflections,
                        lessons=lessons,
                        screenshot_state=screenshot_state,
                        manager_agent=manager_agent,
                        shadow_mode=shadow_mode,
                        logger=logger,
                    )
                    last_manager_run_at = snapshot.server_time
                continue

            if await bridge_state.has_pending_symbol(snapshot.symbol):
                continue

            if snapshot_updated:
                fast_entry_executed, last_fast_entry_key = await _run_fast_entry_cycle(
                    snapshot=snapshot,
                    settings=settings,
                    agent_name=args.agent_name or settings.v60_agent_name,
                    event_journal=event_journal,
                    store=store,
                    registry=registry,
                    entry_builder=entry_builder,
                    risk_arbiter=risk_arbiter,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    bridge_state=bridge_state,
                    reflections=reflections,
                    lessons=lessons,
                    screenshot_state=screenshot_state,
                    shadow_mode=shadow_mode,
                    logger=logger,
                    last_signal_key=last_fast_entry_key,
                )
                if fast_entry_executed:
                    continue

            current_bar_end = _latest_entry_bar_end(snapshot)
            if snapshot_updated and current_bar_end is not None and current_bar_end != last_entry_bar_end:
                await _run_entry_cycle(
                    snapshot=snapshot,
                    settings=settings,
                    agent_name=args.agent_name or settings.v60_agent_name,
                    event_journal=event_journal,
                    store=store,
                    registry=registry,
                    entry_builder=entry_builder,
                    risk_arbiter=risk_arbiter,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    bridge_state=bridge_state,
                    reflections=reflections,
                    lessons=lessons,
                    screenshot_state=screenshot_state,
                    entry_agent=entry_agent,
                    shadow_mode=shadow_mode,
                    logger=logger,
                    analysis_mode="standard_entry",
                )
                last_entry_bar_end = current_bar_end
    finally:
        await _shutdown_flatten_open_tickets(
            settings=settings,
            agent_name=args.agent_name or settings.v60_agent_name,
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
