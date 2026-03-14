from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import uvicorn

from agents.mt5_v51_entry_analyst import MT5V51EntryAnalysisResult, MT5V51EntryAnalystAgent
from app.v5_1_config import V51Settings, get_v51_settings
from brokers.mt5_v51 import MT5V51BridgeState, create_mt5_v51_bridge_app
from data.mt5_v51_schemas import (
    MT5V51BridgeCommand,
    MT5V51BridgeSnapshot,
    MT5V51EntryDecision,
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


def _record_closed_tickets(
    *,
    closed_tickets: list[MT5V51TicketRecord],
    agent_name: str,
    reflection_journal: Journal,
    store: SupabaseMT5V51Store | None,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    logger,
) -> None:
    for closed_ticket in closed_tickets:
        reflection = build_mt5_v51_ticket_reflection(closed_ticket, exit_reason="snapshot_flat")
        reflections.append(reflection)
        new_lessons = derive_mt5_v51_lessons(reflection)
        lessons.extend(new_lessons)
        reflection_journal.record(
            {
                "record_type": "mt5_v51_trade_reflection",
                "agent_name": agent_name,
                "reflection": reflection.model_dump(mode="json"),
                "lessons": [lesson.model_dump(mode="json") for lesson in new_lessons],
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v51_trade_reflection",
                store.insert_mt5_v51_trade_reflection,
                agent_name=agent_name,
                reflection=reflection,
                ticket_id=closed_ticket.ticket_id,
                basket_id=closed_ticket.basket_id,
            )
            _safe_store_call(logger, "upsert_mt5_v51_lessons", store.upsert_lessons, new_lessons)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the V5.1 MT5 BTCUSD demo-paper orchestrator.")
    parser.add_argument("--agent-name", default=None)
    parser.add_argument("--duration-minutes", type=int, default=0)
    parser.add_argument("--session-tag", default=None)
    parser.add_argument("--enable-trade-commands", action="store_true")
    parser.add_argument("--shadow-mode", action="store_true")
    parser.add_argument("--bridge-host", default=None)
    parser.add_argument("--bridge-port", type=int, default=None)
    return parser.parse_args()


@dataclass
class MT5V51PendingEntrySignal:
    symbol: str
    source_bar_end: datetime
    source_server_time: datetime
    analysis_packet: dict[str, object]
    source_risk_posture: str
    result: MT5V51EntryAnalysisResult


def _latest_entry_bar_end(snapshot: MT5V51BridgeSnapshot) -> datetime | None:
    return snapshot.bars_1m[-1].end_at if snapshot.bars_1m else None


def _entry_analysis_budget_seconds(
    *,
    timeout_seconds: int,
    max_signal_age_seconds: int | None = None,
    execution_grace_seconds: int = 0,
) -> float:
    budget_seconds = max(0.1, float(timeout_seconds))
    if max_signal_age_seconds is None or max_signal_age_seconds <= 0:
        return budget_seconds

    grace_seconds = max(float(execution_grace_seconds), 0.0)
    freshness_budget = max(0.1, float(max_signal_age_seconds) - grace_seconds)
    return min(budget_seconds, freshness_budget)


def _entry_command_expires_at(snapshot: MT5V51BridgeSnapshot, *, stale_after_seconds: int) -> datetime:
    return snapshot.server_time + timedelta(seconds=stale_after_seconds)


def _microbars_ready(snapshot: MT5V51BridgeSnapshot, *, minimum_bars: int) -> bool:
    return len([bar for bar in snapshot.bars_20s if bar.complete]) >= minimum_bars


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


def _held_closed_1m_bars(*, ticket: MT5V51TicketRecord, snapshot: MT5V51BridgeSnapshot) -> int:
    return sum(1 for bar in snapshot.bars_1m if bar.complete and bar.end_at > ticket.opened_at)


def _coerce_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _coerce_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _coerce_bool(value: object) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _regime_payload(packet: dict[str, object]) -> dict[str, object]:
    return _coerce_dict(packet.get("trend_regime"))


def _regime_supports_direction(regime: dict[str, object], *, direction: str) -> bool:
    if not _coerce_bool(regime.get("tradeable")):
        return False
    if str(regime.get("primary_direction", "flat")) != direction:
        return False
    if _coerce_float(regime.get("chop_score")) > 3.0:
        return False
    return _coerce_float(regime.get("trend_quality_score")) >= 8.0


def _regime_supports_action(packet: dict[str, object], *, action: str) -> bool:
    regime = _regime_payload(packet)
    if action == "enter_long":
        return _regime_supports_direction(regime, direction="bull")
    if action == "enter_short":
        return _regime_supports_direction(regime, direction="bear")
    return True


def _regime_is_exceptionally_strong_against_backdrop(regime: dict[str, object], *, direction: str) -> bool:
    return (
        _regime_supports_direction(regime, direction=direction)
        and _coerce_float(regime.get("trend_quality_score")) >= 11.0
        and _coerce_float(regime.get("alignment_score")) >= 2.0
        and _coerce_float(regime.get("chop_score")) <= 2.0
    )


def _freshness_allows_scalp_entry(value: object) -> bool:
    return str(value).strip().lower() in {"fresh", "aging"}


def _freshness_allows_execution(value: object) -> bool:
    return str(value).strip().lower() in {"fresh", "aging", "stale_soon"}


def _spread_cost_allows_scalp_entry(*, quote: dict[str, object], microstructure: dict[str, object]) -> bool:
    spread_bps = _coerce_float(quote.get("spread_bps"), default=999.0)
    if spread_bps > 8.0:
        return False

    spread_to_atr_ratio = microstructure.get("spread_to_1m_atr_ratio")
    if spread_to_atr_ratio is None:
        return True

    spread_to_atr = _coerce_float(spread_to_atr_ratio, default=0.0)
    if spread_to_atr <= 0.55:
        return True

    spread_percentile = microstructure.get("spread_percentile_1m")
    if spread_percentile is None:
        return False
    return _coerce_float(spread_percentile, default=100.0) < 55.0


def _consecutive_candle_run(bars: list[dict[str, object]], *, direction: str) -> int:
    run = 0
    previous_close: float | None = None
    for bar in reversed(bars):
        open_price = _coerce_float(bar.get("open"))
        close_price = _coerce_float(bar.get("close"))
        if direction == "bull":
            if close_price <= open_price:
                break
            if previous_close is not None and close_price < previous_close:
                break
        else:
            if close_price >= open_price:
                break
            if previous_close is not None and close_price > previous_close:
                break
        run += 1
        previous_close = close_price
    return run


def _aggressive_micro_opposition(summary: dict[str, object], *, direction: str) -> bool:
    if direction == "bull":
        return (
            _coerce_bool(summary.get("short_trigger_ready"))
            or (
                str(summary.get("direction", "flat")) == "bear"
                and max(
                    int(_coerce_float(summary.get("consecutive_bear_closes"))),
                    int(_coerce_float(summary.get("consecutive_strong_bear_bars"))),
                )
                >= 2
            )
        )
    return (
        _coerce_bool(summary.get("long_trigger_ready"))
        or (
            str(summary.get("direction", "flat")) == "bull"
            and max(
                int(_coerce_float(summary.get("consecutive_bull_closes"))),
                int(_coerce_float(summary.get("consecutive_strong_bull_bars"))),
            )
            >= 2
        )
    )


def _risk_bounds_for_setup_quality(setup_quality: str) -> tuple[float, float] | None:
    if setup_quality == "strong":
        return 0.005, 0.005
    if setup_quality == "normal":
        return 0.003, 0.004
    if setup_quality == "weak":
        return 0.001, 0.002
    return None


def _default_requested_risk_fraction(setup_quality: str) -> float | None:
    bounds = _risk_bounds_for_setup_quality(setup_quality)
    if bounds is None:
        return None
    lower, upper = bounds
    if lower == upper:
        return upper
    return (lower + upper) / 2.0


def _take_profit_r_for_setup_quality(setup_quality: str) -> float | None:
    if setup_quality == "strong":
        return 0.75
    if setup_quality == "normal":
        return 0.50
    if setup_quality == "weak":
        return 0.25
    return None


def _setup_quality_for_direction(packet: dict[str, object], *, direction: str) -> str:
    regime = _regime_payload(packet)
    if not _regime_supports_direction(regime, direction=direction):
        return "choppy"

    timeframes = _coerce_dict(packet.get("timeframes"))
    one = _coerce_dict(timeframes.get("1m"))
    twenty = _coerce_dict(timeframes.get("20s"))
    if _aggressive_micro_opposition(twenty, direction=direction):
        return "choppy"

    prefix = "long" if direction == "bull" else "short"
    trend_quality = _coerce_float(regime.get("trend_quality_score"))
    alignment = _coerce_float(regime.get("alignment_score"))
    chop_score = _coerce_float(regime.get("chop_score"), default=99.0)
    entry_style = str(regime.get("entry_style", "none"))
    has_entry_signal = (
        _coerce_bool(one.get(f"{prefix}_trigger_ready"))
        or _coerce_bool(one.get(f"{prefix}_continuation_ready"))
        or _coerce_bool(one.get(f"{prefix}_pause_after_impulse_ready"))
        or entry_style in {"impulse_breakout", "stair_step_continuation", "pause_after_impulse", "breakout"}
        or _one_minute_price_action_supports_direction(one, direction=direction)
    )
    if not has_entry_signal:
        return "weak"

    if trend_quality >= 11.0 and alignment >= 3.0 and chop_score <= 1.0 and entry_style != "none":
        return "strong"
    if alignment >= 2.0 and chop_score <= 2.0:
        return "normal"
    return "weak"


def _setup_quality_for_action(packet: dict[str, object], *, action: str) -> str:
    if action == "enter_long":
        return _setup_quality_for_direction(packet, direction="bull")
    if action == "enter_short":
        return _setup_quality_for_direction(packet, direction="bear")
    return "choppy"


def _normalize_requested_risk_fraction(
    decision: MT5V51EntryDecision,
    *,
    packet: dict[str, object],
) -> tuple[MT5V51EntryDecision, str]:
    setup_quality = _setup_quality_for_action(packet, action=decision.action)
    bounds = _risk_bounds_for_setup_quality(setup_quality)
    if bounds is None:
        return decision.model_copy(update={"requested_risk_fraction": None}), setup_quality

    lower, upper = bounds
    requested = (
        decision.requested_risk_fraction
        if decision.requested_risk_fraction is not None
        else _default_requested_risk_fraction(setup_quality)
    )
    if requested is None:
        normalized = upper
    elif lower == upper:
        normalized = upper
    else:
        normalized = max(lower, min(upper, requested))
    return decision.model_copy(update={"requested_risk_fraction": round(normalized, 4)}), setup_quality


def _override_risk_fraction(*, setup_quality: str) -> float:
    default = _default_requested_risk_fraction(setup_quality)
    if default is None:
        return 0.001
    return round(default, 4)


def _continuation_override_decision(packet: dict[str, object]) -> MT5V51EntryDecision | None:
    if str(packet.get("position_state", "flat")) != "flat":
        return None
    freshness = _coerce_dict(packet.get("freshness"))
    if not _freshness_allows_scalp_entry(freshness.get("source_snapshot_age_bucket", "")):
        return None
    quote = _coerce_dict(packet.get("quote"))
    microstructure = _coerce_dict(packet.get("microstructure"))
    timeframes = _coerce_dict(packet.get("timeframes"))
    recent_bars = _coerce_dict(packet.get("recent_bars"))
    regime = _regime_payload(packet)
    one = _coerce_dict(timeframes.get("1m"))
    twenty = _coerce_dict(timeframes.get("20s"))
    recent_1m = recent_bars.get("1m")
    if not isinstance(recent_1m, list) or len(recent_1m) < 3:
        return None

    if not _spread_cost_allows_scalp_entry(quote=quote, microstructure=microstructure):
        return None

    long_run = _consecutive_candle_run(recent_1m, direction="bull")
    short_run = _consecutive_candle_run(recent_1m, direction="bear")

    if _regime_supports_direction(regime, direction="bull") and not _aggressive_micro_opposition(twenty, direction="bull"):
        long_score = 0
        if _coerce_float(regime.get("trend_quality_score")) >= 11.0:
            long_score += 2
        elif _coerce_float(regime.get("trend_quality_score")) >= 8.0:
            long_score += 1
        if _coerce_float(regime.get("alignment_score")) >= 3.0:
            long_score += 1
        if str(regime.get("entry_style", "none")) in {"impulse_breakout", "stair_step_continuation", "pause_after_impulse"}:
            long_score += 1
        if _coerce_float(regime.get("chop_score")) <= 1.0:
            long_score += 1
        if long_run >= 3:
            long_score += 2
        if long_run >= 4:
            long_score += 1
        if _coerce_bool(one.get("long_trigger_ready")):
            long_score += 2
        if _coerce_bool(one.get("long_continuation_ready")):
            long_score += 2
        if _coerce_float(one.get("ema_gap_bps")) > 0:
            long_score += 1
        if _coerce_float(one.get("return_3_bps")) > 0:
            long_score += 1
        if _coerce_float(one.get("return_5_bps")) > 0:
            long_score += 1
        if _coerce_float(one.get("close_range_position")) >= 0.55:
            long_score += 1
        if _coerce_float(one.get("body_pct")) >= 0.40:
            long_score += 1
        if _coerce_float(one.get("latest_range_vs_atr")) >= 0.20:
            long_score += 1
        if long_score >= 9:
            setup_quality = _setup_quality_for_direction(packet, direction="bull")
            if setup_quality not in {"strong", "normal"}:
                return None
            return MT5V51EntryDecision(
                action="enter_long",
                confidence=0.68,
                rationale=(
                    "Deterministic continuation override: the packet marks a tradeable bullish trend regime, "
                    "1m shows clean bullish continuation, and the 20s tape is not aggressively opposing the move."
                ),
                thesis_tags=["momentum", "continuation", "override"],
                requested_risk_fraction=_override_risk_fraction(setup_quality=setup_quality),
                context_signature=str(packet.get("context_signature") or "") or None,
            )

    if _regime_supports_direction(regime, direction="bear") and not _aggressive_micro_opposition(twenty, direction="bear"):
        short_score = 0
        if _coerce_float(regime.get("trend_quality_score")) >= 11.0:
            short_score += 2
        elif _coerce_float(regime.get("trend_quality_score")) >= 8.0:
            short_score += 1
        if _coerce_float(regime.get("alignment_score")) >= 3.0:
            short_score += 1
        if str(regime.get("entry_style", "none")) in {"impulse_breakout", "stair_step_continuation", "pause_after_impulse"}:
            short_score += 1
        if _coerce_float(regime.get("chop_score")) <= 1.0:
            short_score += 1
        if short_run >= 3:
            short_score += 2
        if short_run >= 4:
            short_score += 1
        if _coerce_bool(one.get("short_trigger_ready")):
            short_score += 2
        if _coerce_bool(one.get("short_continuation_ready")):
            short_score += 2
        if _coerce_float(one.get("ema_gap_bps")) < 0:
            short_score += 1
        if _coerce_float(one.get("return_3_bps")) < 0:
            short_score += 1
        if _coerce_float(one.get("return_5_bps")) < 0:
            short_score += 1
        if _coerce_float(one.get("close_range_position"), default=0.5) <= 0.45:
            short_score += 1
        if _coerce_float(one.get("body_pct")) >= 0.40:
            short_score += 1
        if _coerce_float(one.get("latest_range_vs_atr")) >= 0.20:
            short_score += 1
        if short_score >= 9:
            setup_quality = _setup_quality_for_direction(packet, direction="bear")
            if setup_quality not in {"strong", "normal"}:
                return None
            return MT5V51EntryDecision(
                action="enter_short",
                confidence=0.68,
                rationale=(
                    "Deterministic continuation override: the packet marks a tradeable bearish trend regime, "
                    "1m shows clean downside continuation, and the 20s tape is not aggressively opposing the move."
                ),
                thesis_tags=["momentum", "breakdown", "override"],
                requested_risk_fraction=_override_risk_fraction(setup_quality=setup_quality),
                context_signature=str(packet.get("context_signature") or "") or None,
            )

    return None


def _price_delta_bps(*, current: float, reference: float) -> float:
    if reference == 0:
        return 0.0
    return ((current - reference) / reference) * 10000.0


def _normalized_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _five_minute_trend_conflict_reason(
    decision: MT5V51EntryDecision,
    *,
    packet: dict[str, object],
    require_alignment: bool,
) -> str | None:
    if not require_alignment:
        return None
    five = _coerce_dict(_coerce_dict(packet.get("timeframes")).get("5m"))
    regime = _regime_payload(packet)
    ema_gap = _coerce_float(five.get("ema_gap_bps"))
    return_3 = _coerce_float(five.get("return_3_bps"))
    if decision.action == "enter_long" and ema_gap < -2.0 and return_3 < -4.0:
        if _regime_is_exceptionally_strong_against_backdrop(regime, direction="bull"):
            return None
        return "5m backdrop is bearish against the long entry."
    if decision.action == "enter_short" and ema_gap > 2.0 and return_3 > 4.0:
        if _regime_is_exceptionally_strong_against_backdrop(regime, direction="bear"):
            return None
        return "5m backdrop is bullish against the short entry."
    return None


def _execution_alignment_reason(
    decision: MT5V51EntryDecision,
    *,
    packet: dict[str, object],
    require_5m_alignment: bool,
) -> str | None:
    freshness = _coerce_dict(packet.get("freshness"))
    if not _freshness_allows_execution(freshness.get("source_snapshot_age_bucket", "")):
        return "Execution snapshot freshness no longer supports a scalp entry."

    quote = _coerce_dict(packet.get("quote"))
    microstructure = _coerce_dict(packet.get("microstructure"))
    if not _spread_cost_allows_scalp_entry(quote=quote, microstructure=microstructure):
        return "Execution spread cost no longer supports the entry."

    if not _regime_supports_action(packet, action=decision.action):
        return "Current regime is choppy or no longer decisively aligned with the entry."

    trend_conflict_reason = _five_minute_trend_conflict_reason(
        decision,
        packet=packet,
        require_alignment=require_5m_alignment,
    )
    if trend_conflict_reason is not None:
        return trend_conflict_reason

    timeframes = _coerce_dict(packet.get("timeframes"))
    one = _coerce_dict(timeframes.get("1m"))
    twenty = _coerce_dict(timeframes.get("20s"))

    if decision.action == "enter_long":
        if _aggressive_micro_opposition(twenty, direction="bull"):
            return "20s tape is aggressively opposing the long entry."
        if (
            _coerce_float(one.get("ema_gap_bps")) <= 0
            and not _one_minute_price_action_supports_direction(one, direction="bull")
            and not _coerce_bool(one.get("long_trigger_ready"))
            and not _coerce_bool(one.get("long_continuation_ready"))
            and not _coerce_bool(one.get("long_pause_after_impulse_ready"))
        ):
            return "1m structure no longer supports the long entry."
        return None

    if _aggressive_micro_opposition(twenty, direction="bear"):
        return "20s tape is aggressively opposing the short entry."
    if (
        _coerce_float(one.get("ema_gap_bps")) >= 0
        and not _one_minute_price_action_supports_direction(one, direction="bear")
        and not _coerce_bool(one.get("short_trigger_ready"))
        and not _coerce_bool(one.get("short_continuation_ready"))
        and not _coerce_bool(one.get("short_pause_after_impulse_ready"))
    ):
        return "1m structure no longer supports the short entry."
    return None


def _one_minute_price_action_supports_direction(one: dict[str, object], *, direction: str) -> bool:
    if direction == "bull":
        return (
            str(one.get("direction", "")) == "bull"
            and (
                int(_coerce_float(one.get("consecutive_bull_closes"))) >= 3
                or (_coerce_float(one.get("return_3_bps")) > 0 and _coerce_float(one.get("return_5_bps")) > 0)
            )
        )
    return (
        str(one.get("direction", "")) == "bear"
        and (
            int(_coerce_float(one.get("consecutive_bear_closes"))) >= 3
            or (_coerce_float(one.get("return_3_bps")) < 0 and _coerce_float(one.get("return_5_bps")) < 0)
        )
    )


def _analysis_signal_age_reason(
    *,
    source_server_time: datetime | None,
    current_server_time: datetime,
    max_age_seconds: int,
) -> str | None:
    if source_server_time is None or max_age_seconds <= 0:
        return None
    age_seconds = (_normalized_utc(current_server_time) - _normalized_utc(source_server_time)).total_seconds()
    if age_seconds <= max_age_seconds:
        return None
    return f"Analysis signal aged out after {age_seconds:.1f}s."


def _fast_quote_entry_decision(packet: dict[str, object]) -> MT5V51EntryDecision | None:
    if str(packet.get("position_state", "flat")) != "flat":
        return None
    freshness = _coerce_dict(packet.get("freshness"))
    if not _freshness_allows_scalp_entry(freshness.get("source_snapshot_age_bucket", "")):
        return None

    quote = _coerce_dict(packet.get("quote"))
    microstructure = _coerce_dict(packet.get("microstructure"))
    timeframes = _coerce_dict(packet.get("timeframes"))
    recent_bars = _coerce_dict(packet.get("recent_bars"))
    regime = _regime_payload(packet)
    one = _coerce_dict(timeframes.get("1m"))
    twenty = _coerce_dict(timeframes.get("20s"))

    recent_1m = recent_bars.get("1m")
    recent_20s = recent_bars.get("20s")
    if not isinstance(recent_1m, list) or not recent_1m:
        return None
    has_recent_20s = isinstance(recent_20s, list) and bool(recent_20s)
    sample_count_10s = int(_coerce_float(microstructure.get("sample_count_10s")))
    if not has_recent_20s and sample_count_10s < 6:
        return None

    bid = _coerce_float(quote.get("bid"))
    ask = _coerce_float(quote.get("ask"))
    midpoint = (bid + ask) / 2.0
    last_1m_close = _coerce_float(_coerce_dict(recent_1m[-1]).get("close"))
    last_20s_close = (
        _coerce_float(_coerce_dict(recent_20s[-1]).get("close")) if has_recent_20s else last_1m_close
    )
    live_vs_1m_close_bps = _price_delta_bps(current=midpoint, reference=last_1m_close)
    live_vs_20s_close_bps = _price_delta_bps(current=midpoint, reference=last_20s_close)
    spread_bps = _coerce_float(quote.get("spread_bps"), default=999.0)
    bid_drift_bps_10s = _coerce_float(microstructure.get("bid_drift_bps_10s"))
    ask_drift_bps_10s = _coerce_float(microstructure.get("ask_drift_bps_10s"))
    mid_drift_bps_10s = _coerce_float(microstructure.get("mid_drift_bps_10s"))

    if not _spread_cost_allows_scalp_entry(quote=quote, microstructure=microstructure):
        return None

    if _regime_supports_direction(regime, direction="bull") and not _aggressive_micro_opposition(twenty, direction="bull"):
        long_score = 0
        if _coerce_float(regime.get("trend_quality_score")) >= 11.0:
            long_score += 2
        elif _coerce_float(regime.get("trend_quality_score")) >= 8.0:
            long_score += 1
        if _coerce_float(regime.get("alignment_score")) >= 3.0:
            long_score += 1
        if _coerce_float(regime.get("chop_score")) <= 1.0:
            long_score += 1
        if _coerce_bool(one.get("long_trigger_ready")):
            long_score += 2
        if _coerce_bool(one.get("long_continuation_ready")):
            long_score += 2
        if _coerce_bool(twenty.get("long_trigger_ready")):
            long_score += 2
        if _coerce_bool(twenty.get("long_continuation_ready")):
            long_score += 1
        if _coerce_float(one.get("ema_gap_bps")) > 0:
            long_score += 1
        if _coerce_float(one.get("return_3_bps")) > 0:
            long_score += 1
        if _coerce_float(one.get("return_5_bps")) > 0:
            long_score += 1
        if mid_drift_bps_10s >= 1.0:
            long_score += 1
        if bid_drift_bps_10s > 0 and ask_drift_bps_10s > 0:
            long_score += 1
        if live_vs_20s_close_bps >= 0.8:
            long_score += 1
        if live_vs_1m_close_bps >= 2.0:
            long_score += 2
        if long_score >= 9:
            setup_quality = _setup_quality_for_direction(packet, direction="bull")
            if setup_quality == "choppy":
                return None
            if setup_quality == "weak":
                return None
            if setup_quality == "normal" and _coerce_float(regime.get("chop_score")) > 1.0:
                return None
            return MT5V51EntryDecision(
                action="enter_long",
                confidence=0.72,
                rationale=(
                    "Deterministic fast-entry override: the packet marks a tradeable bullish trend regime and "
                    "live quote acceleration is pressing higher before the next 1m close."
                ),
                thesis_tags=["momentum", "continuation", "fast_override"],
                requested_risk_fraction=_override_risk_fraction(setup_quality=setup_quality),
                context_signature=str(packet.get("context_signature") or "") or None,
            )

    if _regime_supports_direction(regime, direction="bear") and not _aggressive_micro_opposition(twenty, direction="bear"):
        short_score = 0
        if _coerce_float(regime.get("trend_quality_score")) >= 11.0:
            short_score += 2
        elif _coerce_float(regime.get("trend_quality_score")) >= 8.0:
            short_score += 1
        if _coerce_float(regime.get("alignment_score")) >= 3.0:
            short_score += 1
        if _coerce_float(regime.get("chop_score")) <= 1.0:
            short_score += 1
        if _coerce_bool(one.get("short_trigger_ready")):
            short_score += 2
        if _coerce_bool(one.get("short_continuation_ready")):
            short_score += 2
        if _coerce_bool(twenty.get("short_trigger_ready")):
            short_score += 2
        if _coerce_bool(twenty.get("short_continuation_ready")):
            short_score += 1
        if _coerce_float(one.get("ema_gap_bps")) < 0:
            short_score += 1
        if _coerce_float(one.get("return_3_bps")) < 0:
            short_score += 1
        if _coerce_float(one.get("return_5_bps")) < 0:
            short_score += 1
        if mid_drift_bps_10s <= -1.0:
            short_score += 1
        if bid_drift_bps_10s < 0 and ask_drift_bps_10s < 0:
            short_score += 1
        if live_vs_20s_close_bps <= -0.8:
            short_score += 1
        if live_vs_1m_close_bps <= -2.0:
            short_score += 2
        if short_score >= 9:
            setup_quality = _setup_quality_for_direction(packet, direction="bear")
            if setup_quality == "choppy":
                return None
            if setup_quality == "weak":
                return None
            if setup_quality == "normal" and _coerce_float(regime.get("chop_score")) > 1.0:
                return None
            return MT5V51EntryDecision(
                action="enter_short",
                confidence=0.72,
                rationale=(
                    "Deterministic fast-entry override: the packet marks a tradeable bearish trend regime and "
                    "live quote acceleration is pressing lower before the next 1m close."
                ),
                thesis_tags=["momentum", "breakdown", "fast_override"],
                requested_risk_fraction=_override_risk_fraction(setup_quality=setup_quality),
                context_signature=str(packet.get("context_signature") or "") or None,
            )

    return None


def _fast_entry_signal_key(*, snapshot: MT5V51BridgeSnapshot, decision: MT5V51EntryDecision) -> str:
    source_bar_end = _latest_entry_bar_end(snapshot)
    source_bucket = (
        source_bar_end.isoformat()
        if source_bar_end is not None
        else snapshot.server_time.replace(second=0, microsecond=0).isoformat()
    )
    return f"{snapshot.symbol}:{source_bucket}:{decision.action}"


async def _execute_entry_decision(
    *,
    snapshot: MT5V51BridgeSnapshot,
    settings: V51Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V51Store | None,
    registry: MT5V51TicketRegistry,
    planner: MT5V51EntryPlanner,
    risk_arbiter: MT5V51RiskArbiter,
    context_builder: MT5V51ContextBuilder,
    posture_engine: MT5V51RiskPostureEngine,
    bridge_state: MT5V51BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
    logger,
    decision: MT5V51EntryDecision,
    decision_source: str,
    source_context: dict[str, object],
    source_kind: str,
    source_bar_end: datetime | None = None,
    source_server_time: datetime | None = None,
    source_risk_posture: str | None = None,
    llm_decision: MT5V51EntryDecision | None = None,
    raw_response: str | None = None,
    prompt_version: str | None = None,
    latency_ms: int | None = None,
) -> bool:
    if decision.action == "hold":
        return False

    pending_symbol_command = await bridge_state.has_pending_symbol(snapshot.symbol)
    risk_posture, multiplier = posture_engine.derive(reflections)
    followed_lessons = _recent_lessons_for_latest_reflections(reflections=reflections, lessons=lessons)
    execution_packet = context_builder.build_entry_packet(
        snapshot=snapshot,
        registry=registry,
        risk_posture=risk_posture,
        reflections=list(reflections),
        lessons=list(lessons),
    )
    decision, setup_quality = _normalize_requested_risk_fraction(decision, packet=execution_packet)
    execution_packet = {
        **execution_packet,
        "setup_quality": setup_quality,
        "normalized_requested_risk_fraction": decision.requested_risk_fraction,
    }

    gate_reason = None
    if source_kind == "analysis":
        gate_reason = _analysis_signal_age_reason(
            source_server_time=source_server_time,
            current_server_time=snapshot.server_time,
            max_age_seconds=settings.v51_analysis_signal_max_age_seconds,
        )
    if gate_reason is None:
        gate_reason = _execution_alignment_reason(
            decision,
            packet=execution_packet,
            require_5m_alignment=settings.v51_require_5m_trend_alignment,
        )

    if gate_reason is None:
        risk_decision = risk_arbiter.evaluate_immediate_entry(
            decision=decision,
            snapshot=snapshot,
            registry=registry,
            risk_posture=risk_posture,
            risk_multiplier=multiplier,
            pending_symbol_command=pending_symbol_command,
        )
    else:
        risk_decision = MT5V51RiskDecision(
            approved=False,
            reason=gate_reason,
            risk_posture=risk_posture,
        )

    execution_record = {
        "record_type": "mt5_v51_entry_execution",
        "agent_name": agent_name,
        "decision": decision.model_dump(mode="json"),
        "decision_source": decision_source,
        "source_kind": source_kind,
        "risk_decision": risk_decision.model_dump(mode="json"),
        "execution_context": execution_packet,
        "setup_quality": setup_quality,
    }
    if source_bar_end is not None:
        execution_record["source_bar_end"] = source_bar_end.isoformat()
    if source_server_time is not None:
        execution_record["source_server_time"] = source_server_time.isoformat()
    if source_risk_posture is not None:
        execution_record["source_risk_posture"] = source_risk_posture
    event_journal.record(execution_record)

    if store is not None:
        decision_payload: dict[str, object] = {
            "stage": "execution",
            "decision": decision.model_dump(mode="json"),
            "decision_source": decision_source,
            "source_kind": source_kind,
            "execution_server_time": snapshot.server_time.isoformat(),
            "execution_context_signature": execution_packet.get("context_signature"),
            "setup_quality": setup_quality,
            "normalized_requested_risk_fraction": decision.requested_risk_fraction,
        }
        if llm_decision is not None:
            decision_payload["llm_decision"] = llm_decision.model_dump(mode="json")
        if raw_response is not None:
            decision_payload["raw_response"] = raw_response
        if prompt_version is not None:
            decision_payload["prompt_version"] = prompt_version
        if latency_ms is not None:
            decision_payload["latency_ms"] = latency_ms
        if source_bar_end is not None:
            decision_payload["source_bar_end"] = source_bar_end.isoformat()
            if source_kind == "analysis":
                decision_payload["analysis_source_bar_end"] = source_bar_end.isoformat()
            elif source_kind == "fast":
                decision_payload["fast_source_bar_end"] = source_bar_end.isoformat()
        if source_server_time is not None:
            decision_payload["source_server_time"] = source_server_time.isoformat()
            if source_kind == "analysis":
                decision_payload["analysis_source_server_time"] = source_server_time.isoformat()
            elif source_kind == "fast":
                decision_payload["fast_source_server_time"] = source_server_time.isoformat()
        if source_risk_posture is not None:
            decision_payload["source_risk_posture"] = source_risk_posture
            if source_kind == "analysis":
                decision_payload["analysis_risk_posture"] = source_risk_posture
            elif source_kind == "fast":
                decision_payload["fast_risk_posture"] = source_risk_posture

        _safe_store_call(
            logger,
            f"insert_mt5_v51_runtime_decision_{source_kind}_entry",
            store.insert_mt5_v51_runtime_decision,
            agent_name=agent_name,
            decision_kind="entry",
            symbol=snapshot.symbol,
            action=decision.action,
            confidence=decision.confidence,
            rationale=decision.rationale,
            risk_posture=risk_posture,
            risk_approved=risk_decision.approved,
            risk_reason=risk_decision.reason,
            context_payload=execution_packet,
            decision_payload=decision_payload,
        )

    if not risk_decision.approved:
        return False

    target_r_multiple = _take_profit_r_for_setup_quality(setup_quality)
    plan = planner.plan_entry(
        decision=decision,
        snapshot=snapshot,
        risk_decision=risk_decision,
        ticket_sequence=1,
        target_r_multiple=Decimal(str(target_r_multiple)) if target_r_multiple is not None else None,
    )
    if plan is None:
        if logger is not None:
            logger.info(
                "v5_1_entry_skipped reason=planner_returned_none symbol=%s source_kind=%s",
                snapshot.symbol,
                source_kind,
            )
        return False

    context_signature = decision.context_signature or source_context.get("context_signature")
    command = planner.build_entry_command(
        plan=plan,
        reason=decision.rationale,
        created_at=snapshot.server_time,
        expires_at=_entry_command_expires_at(
            snapshot,
            stale_after_seconds=settings.v51_stale_after_seconds,
        ),
        thesis_tags=decision.thesis_tags,
        context_signature=context_signature,
        followed_lessons=followed_lessons,
    )
    metadata_update = {
        **command.metadata,
        "decision_source": decision_source,
        "source_kind": source_kind,
        "execution_risk_posture": risk_posture,
        "execution_server_time": snapshot.server_time.isoformat(),
        "execution_context_signature": execution_packet.get("context_signature"),
        "setup_quality": setup_quality,
        "normalized_requested_risk_fraction": decision.requested_risk_fraction,
        "target_r_multiple": target_r_multiple,
    }
    plan_payload = {
        **plan.model_dump(mode="json"),
        "hard_take_profit": plan.take_profit,
        "soft_take_profit_1": plan.soft_take_profit_1,
        "soft_take_profit_2": plan.soft_take_profit_2,
        "thesis_tags": decision.thesis_tags,
        "context_signature": context_signature,
        "followed_lessons": followed_lessons,
        "risk_posture": risk_posture,
        "decision_source": decision_source,
        "source_kind": source_kind,
        "execution_server_time": snapshot.server_time.isoformat(),
        "execution_context_signature": execution_packet.get("context_signature"),
        "setup_quality": setup_quality,
        "normalized_requested_risk_fraction": decision.requested_risk_fraction,
        "target_r_multiple": target_r_multiple,
    }
    if source_bar_end is not None:
        iso_bar_end = source_bar_end.isoformat()
        metadata_update["source_bar_end"] = iso_bar_end
        plan_payload["source_bar_end"] = iso_bar_end
        if source_kind == "analysis":
            metadata_update["analysis_source_bar_end"] = iso_bar_end
            plan_payload["analysis_source_bar_end"] = iso_bar_end
        elif source_kind == "fast":
            metadata_update["fast_source_bar_end"] = iso_bar_end
            plan_payload["fast_source_bar_end"] = iso_bar_end
    if source_server_time is not None:
        iso_source_time = source_server_time.isoformat()
        metadata_update["source_server_time"] = iso_source_time
        plan_payload["source_server_time"] = iso_source_time
        if source_kind == "analysis":
            metadata_update["analysis_source_server_time"] = iso_source_time
            plan_payload["analysis_source_server_time"] = iso_source_time
        elif source_kind == "fast":
            metadata_update["fast_source_server_time"] = iso_source_time
            plan_payload["fast_source_server_time"] = iso_source_time
    if source_risk_posture is not None:
        metadata_update["source_risk_posture"] = source_risk_posture
        plan_payload["source_risk_posture"] = source_risk_posture
        if source_kind == "analysis":
            metadata_update["analysis_risk_posture"] = source_risk_posture
            plan_payload["analysis_risk_posture"] = source_risk_posture
        elif source_kind == "fast":
            metadata_update["fast_risk_posture"] = source_risk_posture
            plan_payload["fast_risk_posture"] = source_risk_posture
    command = command.model_copy(update={"metadata": metadata_update})

    risk_arbiter.record_approved_entry(snapshot.server_time)
    if shadow_mode:
        event_journal.record(
            {
                "record_type": "mt5_v51_shadow_command",
                "agent_name": agent_name,
                "command_source": "entry" if source_kind == "analysis" else f"{source_kind}_entry",
                "command": command.model_dump(mode="json"),
            }
        )
        return True

    registry.register_pending_entry(command=command, plan_payload=plan_payload)
    await bridge_state.queue_command(command)
    event_journal.record(
        {
            "record_type": "mt5_v51_bridge_command_enqueued",
            "agent_name": agent_name,
            "command_source": "entry" if source_kind == "analysis" else f"{source_kind}_entry",
            "command": command.model_dump(mode="json"),
        }
    )
    if store is not None:
        _safe_store_call(
            logger,
            f"insert_mt5_v51_bridge_command_{source_kind}_entry",
            store.insert_mt5_v51_bridge_command,
            agent_name=agent_name,
            command=command,
            bridge_id=settings.v51_bridge_id,
        )
    return True


async def _run_fast_entry_cycle(
    *,
    snapshot: MT5V51BridgeSnapshot,
    settings: V51Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V51Store | None,
    registry: MT5V51TicketRegistry,
    planner: MT5V51EntryPlanner,
    risk_arbiter: MT5V51RiskArbiter,
    context_builder: MT5V51ContextBuilder,
    posture_engine: MT5V51RiskPostureEngine,
    bridge_state: MT5V51BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
    logger,
    last_signal_key: str | None,
) -> tuple[bool, str | None]:
    if not settings.v51_enable_fast_entry_override:
        return False, last_signal_key
    if risk_arbiter.snapshot_is_stale(snapshot):
        return False, last_signal_key

    risk_posture, _ = posture_engine.derive(reflections)
    packet = context_builder.build_entry_packet(
        snapshot=snapshot,
        registry=registry,
        risk_posture=risk_posture,
        reflections=list(reflections),
        lessons=list(lessons),
    )
    decision = _fast_quote_entry_decision(packet)
    if decision is None:
        return False, last_signal_key

    signal_key = _fast_entry_signal_key(snapshot=snapshot, decision=decision)
    if signal_key == last_signal_key:
        return False, last_signal_key

    source_bar_end = _latest_entry_bar_end(snapshot)
    signal_record = {
        "record_type": "mt5_v51_fast_entry_signal",
        "agent_name": agent_name,
        "context": packet,
        "decision": decision.model_dump(mode="json"),
        "decision_source": "deterministic_fast_quote_override",
        "source_server_time": snapshot.server_time.isoformat(),
        "signal_key": signal_key,
    }
    if source_bar_end is not None:
        signal_record["source_bar_end"] = source_bar_end.isoformat()
    event_journal.record(signal_record)

    if store is not None:
        decision_payload: dict[str, object] = {
            "stage": "signal",
            "decision": decision.model_dump(mode="json"),
            "decision_source": "deterministic_fast_quote_override",
            "signal_key": signal_key,
            "source_kind": "fast",
            "source_server_time": snapshot.server_time.isoformat(),
        }
        if source_bar_end is not None:
            decision_payload["source_bar_end"] = source_bar_end.isoformat()
        _safe_store_call(
            logger,
            "insert_mt5_v51_runtime_decision_fast_signal",
            store.insert_mt5_v51_runtime_decision,
            agent_name=agent_name,
            decision_kind="entry",
            symbol=snapshot.symbol,
            action=decision.action,
            confidence=decision.confidence,
            rationale=decision.rationale,
            risk_posture=risk_posture,
            risk_approved=None,
            risk_reason="Awaiting immediate execution on fast intrabar signal.",
            context_payload=packet,
            decision_payload=decision_payload,
        )

    executed = await _execute_entry_decision(
        snapshot=snapshot,
        settings=settings,
        agent_name=agent_name,
        event_journal=event_journal,
        store=store,
        registry=registry,
        planner=planner,
        risk_arbiter=risk_arbiter,
        context_builder=context_builder,
        posture_engine=posture_engine,
        bridge_state=bridge_state,
        reflections=reflections,
        lessons=lessons,
        shadow_mode=shadow_mode,
        logger=logger,
        decision=decision,
        decision_source="deterministic_fast_quote_override",
        source_context=packet,
        source_kind="fast",
        source_bar_end=source_bar_end,
        source_server_time=snapshot.server_time,
        source_risk_posture=risk_posture,
    )
    return executed, signal_key


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
    last_entry_bar_end: datetime | None = None
    last_fast_entry_key: str | None = None
    commands_enabled = args.enable_trade_commands or settings.v51_mt5_enable_trade_commands
    shadow_mode = settings.v51_mt5_shadow_mode or not commands_enabled
    if args.enable_trade_commands:
        shadow_mode = False
    if args.shadow_mode:
        shadow_mode = True
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
            context_builder.observe_snapshot(snapshot)

            await _process_acks(bridge_state=bridge_state, registry=registry)
            sync_result = registry.sync(snapshot)
            _record_closed_tickets(
                closed_tickets=sync_result.closed,
                agent_name=args.agent_name or settings.v51_agent_name,
                reflection_journal=reflection_journal,
                store=store,
                reflections=reflections,
                lessons=lessons,
                logger=logger,
            )

            await _harvest_completed_entry_analyses(
                snapshot=snapshot,
                settings=settings,
                agent_name=args.agent_name or settings.v51_agent_name,
                event_journal=event_journal,
                store=store,
                entry_prompt_version=entry_agent.prompt_version,
                analysis_tasks=entry_analysis_tasks,
                registry=registry,
                planner=planner,
                risk_arbiter=risk_arbiter,
                context_builder=context_builder,
                posture_engine=posture_engine,
                bridge_state=bridge_state,
                reflections=reflections,
                lessons=lessons,
                shadow_mode=shadow_mode,
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

            if snapshot_updated:
                fast_entry_executed, last_fast_entry_key = await _run_fast_entry_cycle(
                    snapshot=snapshot,
                    settings=settings,
                    agent_name=args.agent_name or settings.v51_agent_name,
                    event_journal=event_journal,
                    store=store,
                    registry=registry,
                    planner=planner,
                    risk_arbiter=risk_arbiter,
                    context_builder=context_builder,
                    posture_engine=posture_engine,
                    bridge_state=bridge_state,
                    reflections=reflections,
                    lessons=lessons,
                    shadow_mode=shadow_mode,
                    logger=logger,
                    last_signal_key=last_fast_entry_key,
                )
                if fast_entry_executed:
                    continue

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
                    min_hold_bars=settings.v51_min_hold_bars,
                    shadow_mode=shadow_mode,
                    logger=logger,
                )
    finally:
        for task in entry_analysis_tasks.values():
            task.cancel()
        if entry_analysis_tasks:
            await asyncio.gather(*entry_analysis_tasks.values(), return_exceptions=True)
        await _shutdown_flatten_open_tickets(
            settings=settings,
            agent_name=args.agent_name or settings.v51_agent_name,
            event_journal=event_journal,
            reflection_journal=reflection_journal,
            store=store,
            registry=registry,
            bridge_state=bridge_state,
            context_builder=context_builder,
            micro_bar_builder=micro_bar_builder,
            reflections=reflections,
            lessons=lessons,
            shadow_mode=shadow_mode,
            logger=logger,
        )
        bridge_server.should_exit = True
        await bridge_task


async def _process_acks(*, bridge_state: MT5V51BridgeState, registry: MT5V51TicketRegistry) -> None:
    for ack in await bridge_state.drain_acks():
        registry.record_ack(ack)


async def _shutdown_flatten_open_tickets(
    *,
    settings: V51Settings,
    agent_name: str,
    event_journal: Journal,
    reflection_journal: Journal,
    store: SupabaseMT5V51Store | None,
    registry: MT5V51TicketRegistry,
    bridge_state: MT5V51BridgeState,
    context_builder: MT5V51ContextBuilder,
    micro_bar_builder: MT5V51Synthetic20sBuilder,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
    logger,
) -> None:
    tickets = registry.all(settings.v51_mt5_symbol)
    if not tickets:
        return
    if shadow_mode:
        if logger is not None:
            logger.warning(
                "v5_1_shutdown_left_open_tickets reason=shadow_mode symbol=%s count=%s",
                settings.v51_mt5_symbol,
                len(tickets),
            )
        return

    snapshot = await bridge_state.latest_snapshot()
    if snapshot is None:
        if logger is not None:
            logger.warning(
                "v5_1_shutdown_left_open_tickets reason=no_snapshot symbol=%s count=%s",
                settings.v51_mt5_symbol,
                len(tickets),
            )
        return

    for ticket in tickets:
        command = MT5V51BridgeCommand(
            command_id=f"shutdown-close-{ticket.ticket_id}-{int(snapshot.server_time.timestamp())}",
            command_type="close_ticket",
            symbol=ticket.symbol,
            created_at=snapshot.server_time,
            expires_at=snapshot.server_time + timedelta(seconds=30),
            ticket_id=ticket.ticket_id,
            basket_id=ticket.basket_id,
            volume_lots=ticket.current_volume_lots,
            reason="Timed V5.1 MT5 demo session shutdown flatten.",
            metadata={"action": "shutdown_flatten"},
        )
        await bridge_state.queue_command(command)
        event_journal.record(
            {
                "record_type": "mt5_v51_bridge_command_enqueued",
                "agent_name": agent_name,
                "command_source": "shutdown_flatten",
                "command": command.model_dump(mode="json"),
            }
        )
        if store is not None:
            _safe_store_call(
                logger,
                "insert_mt5_v51_bridge_command_shutdown_flatten",
                store.insert_mt5_v51_bridge_command,
                agent_name=agent_name,
                command=command,
                bridge_id=snapshot.bridge_id,
            )

    deadline = datetime.now(timezone.utc) + timedelta(seconds=12)
    while datetime.now(timezone.utc) < deadline:
        try:
            await bridge_state.wait_for_snapshot(timeout=1.0)
        except TimeoutError:
            pass
        snapshot = await bridge_state.latest_snapshot()
        if snapshot is None:
            continue
        snapshot = micro_bar_builder.enrich_snapshot(snapshot)
        context_builder.observe_snapshot(snapshot)
        await _process_acks(bridge_state=bridge_state, registry=registry)
        sync_result = registry.sync(snapshot)
        _record_closed_tickets(
            closed_tickets=sync_result.closed,
            agent_name=agent_name,
            reflection_journal=reflection_journal,
            store=store,
            reflections=reflections,
            lessons=lessons,
            logger=logger,
        )
        if not registry.all(settings.v51_mt5_symbol):
            return

    remaining = registry.all(settings.v51_mt5_symbol)
    if remaining and logger is not None:
        logger.warning(
            "v5_1_shutdown_flatten_incomplete symbol=%s remaining_tickets=%s",
            settings.v51_mt5_symbol,
            [ticket.ticket_id for ticket in remaining],
        )


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
    analysis_budget_seconds = _entry_analysis_budget_seconds(
        timeout_seconds=settings.v51_mt5_entry_timeout_seconds,
        max_signal_age_seconds=settings.v51_analysis_signal_max_age_seconds,
        execution_grace_seconds=settings.v51_stale_after_seconds,
    )
    if analysis_budget_seconds < float(settings.v51_mt5_entry_timeout_seconds):
        logger.info(
            "v5_1_entry_analysis_budget_capped symbol=%s bar_end=%s timeout_seconds=%s analysis_budget_seconds=%.1f",
            snapshot.symbol,
            source_bar_end.isoformat(),
            settings.v51_mt5_entry_timeout_seconds,
            analysis_budget_seconds,
        )

    analysis_tasks[source_bar_end] = asyncio.create_task(
        _analyze_entry_signal(
            symbol=snapshot.symbol,
            source_bar_end=source_bar_end,
            source_server_time=snapshot.server_time,
            analysis_packet=packet,
            source_risk_posture=risk_posture,
            timeout_seconds=analysis_budget_seconds,
            entry_agent=entry_agent,
        )
    )


async def _analyze_entry_signal(
    *,
    symbol: str,
    source_bar_end: datetime,
    source_server_time: datetime,
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
        analysis_packet=analysis_packet,
        source_risk_posture=source_risk_posture,
        result=result,
    )


async def _harvest_completed_entry_analyses(
    *,
    snapshot: MT5V51BridgeSnapshot,
    settings: V51Settings,
    agent_name: str,
    event_journal: Journal,
    store: SupabaseMT5V51Store | None,
    entry_prompt_version: str,
    analysis_tasks: dict[datetime, asyncio.Task[MT5V51PendingEntrySignal]],
    registry: MT5V51TicketRegistry,
    planner: MT5V51EntryPlanner,
    risk_arbiter: MT5V51RiskArbiter,
    context_builder: MT5V51ContextBuilder,
    posture_engine: MT5V51RiskPostureEngine,
    bridge_state: MT5V51BridgeState,
    reflections: list[TradeReflection],
    lessons: list[LessonRecord],
    shadow_mode: bool,
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
                    "Awaiting immediate execution on analysis completion."
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
                },
            )

        effective_decision = signal.result.decision
        decision_source = "llm"
        if settings.v51_enable_continuation_override and effective_decision.action == "hold":
            override_decision = _continuation_override_decision(signal.analysis_packet)
            if override_decision is not None:
                effective_decision = override_decision
                decision_source = "deterministic_continuation_override"
                event_journal.record(
                    {
                        "record_type": "mt5_v51_entry_override",
                        "agent_name": agent_name,
                        "source_bar_end": signal.source_bar_end.isoformat(),
                        "original_decision": signal.result.decision.model_dump(mode="json"),
                        "override_decision": effective_decision.model_dump(mode="json"),
                        "context_signature": signal.analysis_packet.get("context_signature"),
                    }
                )

        if effective_decision.action == "hold":
            continue

        await _execute_entry_decision(
            snapshot=snapshot,
            settings=settings,
            agent_name=agent_name,
            event_journal=event_journal,
            store=store,
            registry=registry,
            planner=planner,
            risk_arbiter=risk_arbiter,
            context_builder=context_builder,
            posture_engine=posture_engine,
            bridge_state=bridge_state,
            reflections=reflections,
            lessons=lessons,
            shadow_mode=shadow_mode,
            logger=logger,
            decision=effective_decision,
            decision_source=decision_source,
            source_context=signal.analysis_packet,
            source_kind="analysis",
            source_bar_end=signal.source_bar_end,
            source_server_time=signal.source_server_time,
            source_risk_posture=signal.source_risk_posture,
            llm_decision=signal.result.decision,
            raw_response=signal.result.raw_response,
            prompt_version=entry_prompt_version,
            latency_ms=signal.result.latency_ms,
        )


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
    tickets = registry.all(snapshot.symbol)
    if not tickets:
        return False
    pending_symbol_command = await bridge_state.has_pending_symbol(snapshot.symbol)
    if pending_symbol_command:
        return False

    for ticket in tickets:
        planned_stop = ticket.initial_stop_loss
        planned_take_profit = ticket.hard_take_profit
        missing_protection = ticket.stop_loss is None or ticket.take_profit is None
        restore_legacy_drift = bool(ticket.metadata.get("attach_protection_after_fill")) and (
            not _levels_match(ticket.stop_loss, planned_stop, tick_size=snapshot.symbol_spec.tick_size)
            or not _levels_match(ticket.take_profit, planned_take_profit, tick_size=snapshot.symbol_spec.tick_size)
        )
        if not missing_protection and not restore_legacy_drift:
            continue
        command = planner.build_protection_command(
            ticket=ticket,
            snapshot=snapshot,
            reason=(
                "Restore the original fixed entry protection from legacy attach metadata."
                if restore_legacy_drift
                else "Attach the original fixed stop and target after entry fill."
            ),
            created_at=snapshot.server_time,
            expires_at=snapshot.server_time + timedelta(seconds=60),
        )
        if command is None:
            continue
        if _levels_match(ticket.stop_loss, command.stop_loss, tick_size=snapshot.symbol_spec.tick_size) and _levels_match(
            ticket.take_profit,
            command.take_profit,
            tick_size=snapshot.symbol_spec.tick_size,
        ):
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


def _levels_match(current: Decimal | None, desired: Decimal | None, *, tick_size: Decimal) -> bool:
    if current is None or desired is None:
        return current is desired
    return abs(current - desired) < tick_size

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
    min_hold_bars: int,
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
        if not registry.scalp_target_ready(ticket):
            continue
        if min_hold_bars > 0 and _held_closed_1m_bars(ticket=ticket, snapshot=snapshot) < max(min_hold_bars - 1, 0):
            continue
        trigger = None
        rationale = None
        commands: list[MT5V51BridgeCommand] = []
        if registry.scalp_target_ready(ticket):
            target_r = registry.scalp_target_r(ticket)
            trigger = f"tp{target_r:.2f}_full"
            rationale = (
                f"Automatic scalp exit at {target_r:.2f}R. "
                "V5.1 fully exits at the first target and does not keep a runner."
            )
            commands.append(
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
                    metadata={"action": "auto_scalp_full_exit", "target_r": round(target_r, 2)},
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


async def _start_bridge_server(*, app, host: str, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.05)
    return server, task


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
