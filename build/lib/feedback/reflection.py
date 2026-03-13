from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from data.schemas import LessonRecord, TicketState, TradeReflection
from execution.position_tracker import OpenTradeState


def build_trade_reflection(
    trade: OpenTradeState,
    *,
    closed_at: datetime,
    exit_price: Decimal,
    exit_reason: str,
    spread_bps_exit: float | None,
) -> TradeReflection:
    avoid_lessons, reinforce_lessons = _lesson_messages(trade=trade, exit_reason=exit_reason)
    return TradeReflection(
        reflection_id=str(uuid4()),
        symbol=trade.symbol,
        side="long",
        opened_at=trade.opened_at,
        closed_at=closed_at,
        bars_held=trade.bars_held,
        entry_price=trade.entry_price,
        exit_price=exit_price,
        qty=trade.initial_qty,
        realized_pnl_usd=trade.realized_pnl_usd,
        realized_r=trade.realized_r,
        mae_r=trade.max_adverse_r,
        mfe_r=trade.max_favorable_r,
        exit_reason=exit_reason,
        spread_bps_entry=trade.entry_spread_bps,
        spread_bps_exit=spread_bps_exit,
        thesis_tags=list(trade.thesis_tags),
        context_signature=trade.context_signature,
        entry_packet_summary=dict(trade.entry_packet_summary),
        followed_lessons=list(trade.followed_lessons),
        avoid_lessons=avoid_lessons,
        reinforce_lessons=reinforce_lessons,
    )


def derive_lessons(reflection: TradeReflection) -> list[LessonRecord]:
    lessons: list[LessonRecord] = []
    for message in reflection.avoid_lessons[:3]:
        lessons.append(
            LessonRecord(
                lesson_id=str(uuid4()),
                category="v4_feedback",
                message=message,
                confidence=0.68,
                source=reflection.reflection_id,
                metadata={
                    "polarity": "avoid",
                    "context_signature": reflection.context_signature,
                    "thesis_tags": reflection.thesis_tags,
                },
            )
        )
    for message in reflection.reinforce_lessons[:3]:
        lessons.append(
            LessonRecord(
                lesson_id=str(uuid4()),
                category="v4_feedback",
                message=message,
                confidence=0.72,
                source=reflection.reflection_id,
                metadata={
                    "polarity": "reinforce",
                    "context_signature": reflection.context_signature,
                    "thesis_tags": reflection.thesis_tags,
                },
            )
        )
    return lessons


def build_ticket_reflection(
    ticket: TicketState,
    *,
    closed_at: datetime,
    exit_price: Decimal,
    exit_reason: str,
    spread_bps_exit: float | None,
) -> TradeReflection:
    avoid_lessons, reinforce_lessons = _ticket_lesson_messages(ticket=ticket, exit_reason=exit_reason)
    return TradeReflection(
        reflection_id=str(uuid4()),
        symbol=ticket.symbol,
        side=ticket.side,
        opened_at=ticket.opened_at or closed_at,
        closed_at=closed_at,
        bars_held=0,
        entry_price=ticket.open_price,
        exit_price=exit_price,
        qty=ticket.volume_lots,
        realized_pnl_usd=ticket.unrealized_pnl_usd,
        realized_r=ticket.unrealized_r,
        mae_r=0.0,
        mfe_r=max(ticket.unrealized_r, 0.0),
        exit_reason=exit_reason,
        spread_bps_entry=None,
        spread_bps_exit=spread_bps_exit,
        thesis_tags=list(ticket.metadata.get("thesis_tags", [])),
        context_signature=str(ticket.metadata.get("context_signature", "")) or None,
        entry_packet_summary=dict(ticket.metadata),
        followed_lessons=list(ticket.metadata.get("followed_lessons", [])),
        avoid_lessons=avoid_lessons,
        reinforce_lessons=reinforce_lessons,
    )


def select_recent_feedback(
    reflections: Sequence[TradeReflection],
    lessons: Sequence[LessonRecord],
) -> dict[str, object]:
    latest = reflections[-1] if reflections else None
    avoid: list[str] = []
    reinforce: list[str] = []
    for lesson in reversed(list(lessons)[-10:]):
        polarity = str(lesson.metadata.get("polarity", ""))
        if polarity == "avoid" and lesson.message not in avoid:
            avoid.append(lesson.message)
        if polarity == "reinforce" and lesson.message not in reinforce:
            reinforce.append(lesson.message)
        if len(avoid) >= 3 and len(reinforce) >= 3:
            break
    return {
        "latest_reflection": latest,
        "avoid": avoid[:3],
        "reinforce": reinforce[:3],
    }


def _lesson_messages(trade: OpenTradeState, exit_reason: str) -> tuple[list[str], list[str]]:
    avoid: list[str] = []
    reinforce: list[str] = []

    if trade.realized_pnl_usd < 0:
        avoid.append(
            f"Avoid repeating long entries in {trade.context_signature or 'the same context'} when the prior thesis tags were {', '.join(trade.thesis_tags) or 'unspecified'}."
        )
        if trade.entry_spread_bps is not None and trade.entry_spread_bps >= 10:
            avoid.append("Avoid new entries when spread is already wide at entry.")
        if exit_reason == "hard_stop":
            avoid.append("Avoid holding a losing thesis once price invalidates the entry structure by a full 1R.")
    else:
        reinforce.append(
            f"Reinforce long setups tagged {', '.join(trade.thesis_tags) or 'trend_follow'} when the market context signature remains supportive."
        )
        if trade.partial_taken:
            reinforce.append("Reinforce taking partial profits at target before trailing the remainder.")

    if trade.max_favorable_r >= 1.0 and trade.realized_r > 0:
        reinforce.append("Reinforce letting winners expand once price reaches at least 1R in favor.")
    if trade.max_adverse_r >= 0.75 and trade.realized_r < 0:
        avoid.append("Avoid entries that move deep against the position before showing any follow-through.")

    return avoid[:3], reinforce[:3]


def _ticket_lesson_messages(ticket: TicketState, exit_reason: str) -> tuple[list[str], list[str]]:
    direction = "long" if ticket.side == "long" else "short"
    avoid: list[str] = []
    reinforce: list[str] = []

    if ticket.unrealized_pnl_usd < 0:
        avoid.append(
            f"Avoid repeating {direction} entries in {ticket.metadata.get('context_signature', 'the same context')} when the prior thesis tags were {', '.join(ticket.metadata.get('thesis_tags', [])) or 'unspecified'}."
        )
        if exit_reason == "hard_stop":
            avoid.append(f"Avoid holding a losing {direction} thesis once price invalidates the planned structure by a full 1R.")
    else:
        reinforce.append(
            f"Reinforce {direction} setups tagged {', '.join(ticket.metadata.get('thesis_tags', [])) or 'trend_follow'} when the higher timeframes remain aligned."
        )
        if ticket.partial_taken:
            reinforce.append("Reinforce taking partial profits before trailing the remainder.")

    if ticket.unrealized_r >= 1.0 and ticket.unrealized_pnl_usd > 0:
        reinforce.append("Reinforce letting winners expand once price reaches at least 1R in favor.")
    if ticket.unrealized_r <= -0.75:
        avoid.append(f"Avoid {direction} entries that move deep against the position before showing follow-through.")

    return avoid[:3], reinforce[:3]
