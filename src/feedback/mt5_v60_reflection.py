from __future__ import annotations

from uuid import uuid4

from data.mt5_v60_schemas import MT5V60TicketRecord
from data.schemas import LessonRecord, TradeReflection


def build_mt5_v60_ticket_reflection(ticket: MT5V60TicketRecord, *, exit_reason: str) -> TradeReflection:
    avoid_lessons, reinforce_lessons = _ticket_lesson_messages(ticket=ticket, exit_reason=exit_reason)
    closed_at = ticket.last_seen_at if ticket.last_seen_at >= ticket.opened_at else ticket.opened_at
    held_seconds = max((closed_at - ticket.opened_at).total_seconds(), 0.0)
    bars_held = int(held_seconds // 180)
    realized_r = _sanitized_realized_r(ticket)
    return TradeReflection(
        reflection_id=str(uuid4()),
        symbol=ticket.symbol,
        side=ticket.side,
        opened_at=ticket.opened_at,
        closed_at=closed_at,
        bars_held=bars_held,
        entry_price=ticket.open_price,
        exit_price=ticket.current_price,
        qty=ticket.current_volume_lots,
        realized_pnl_usd=ticket.unrealized_pnl_usd,
        realized_r=realized_r,
        mae_r=0.0,
        mfe_r=max(realized_r, 0.0),
        exit_reason=exit_reason,
        spread_bps_entry=None,
        spread_bps_exit=None,
        thesis_tags=list(ticket.thesis_tags),
        context_signature=ticket.context_signature,
        entry_packet_summary=dict(ticket.metadata),
        followed_lessons=list(ticket.followed_lessons),
        avoid_lessons=avoid_lessons,
        reinforce_lessons=reinforce_lessons,
    )


def derive_mt5_v60_lessons(reflection: TradeReflection) -> list[LessonRecord]:
    lessons: list[LessonRecord] = []
    for message in reflection.avoid_lessons[:3]:
        lessons.append(
            LessonRecord(
                lesson_id=str(uuid4()),
                category="v6_0_feedback",
                message=message,
                confidence=0.68,
                source=reflection.reflection_id,
                metadata={
                    "polarity": "avoid",
                    "context_signature": reflection.context_signature,
                    "thesis_tags": reflection.thesis_tags,
                    "feedback_tags": _feedback_tags_for_message(message, thesis_tags=reflection.thesis_tags),
                },
            )
        )
    for message in reflection.reinforce_lessons[:3]:
        lessons.append(
            LessonRecord(
                lesson_id=str(uuid4()),
                category="v6_0_feedback",
                message=message,
                confidence=0.72,
                source=reflection.reflection_id,
                metadata={
                    "polarity": "reinforce",
                    "context_signature": reflection.context_signature,
                    "thesis_tags": reflection.thesis_tags,
                    "feedback_tags": _feedback_tags_for_message(message, thesis_tags=reflection.thesis_tags),
                },
            )
        )
    return lessons


def _ticket_lesson_messages(ticket: MT5V60TicketRecord, exit_reason: str) -> tuple[list[str], list[str]]:
    direction = "long" if ticket.side == "long" else "short"
    avoid: list[str] = []
    reinforce: list[str] = []
    if ticket.unrealized_pnl_usd < 0:
        avoid.append(
            f"Avoid repeating {direction} BTCUSD 3m setups in {ticket.context_signature or 'the same context'} when the prior thesis tags were {', '.join(ticket.thesis_tags) or 'unspecified'}."
        )
        if exit_reason == "stop_loss":
            avoid.append(f"Avoid holding a losing {direction} BTCUSD thesis once the 3m structure invalidates and the chart loses trend quality.")
    else:
        reinforce.append(
            f"Reinforce {direction} BTCUSD setups tagged {', '.join(ticket.thesis_tags) or 'trend'} when the 3m move stays clean and the screenshot still supports continuation."
        )
    if ticket.unrealized_r <= -0.75:
        avoid.append(f"Avoid {direction} BTCUSD entries that move deep against the position before follow-through.")
    return avoid[:3], reinforce[:3]


def _sanitized_realized_r(ticket: MT5V60TicketRecord) -> float:
    if ticket.r_distance_price > 0:
        if ticket.side == "long":
            realized_r = float((ticket.current_price - ticket.open_price) / ticket.r_distance_price)
        else:
            realized_r = float((ticket.open_price - ticket.current_price) / ticket.r_distance_price)
    else:
        realized_r = ticket.unrealized_r
    if abs(realized_r) > 10:
        return 0.0
    return realized_r


def _feedback_tags_for_message(message: str, *, thesis_tags: list[str]) -> list[str]:
    tags = [tag.strip().lower().replace("-", "_").replace(" ", "_") for tag in thesis_tags if tag.strip()]
    message_lower = message.lower()
    heuristics = {
        "invalidat": "respect_invalidation",
        "losing": "cut_loser_fast",
        "deep against": "avoid_early_heat",
        "continuation": "trend_follow",
        "screenshot": "visual_confirm",
        "repeat": "avoid_repeat_context",
    }
    for needle, tag in heuristics.items():
        if needle in message_lower and tag not in tags:
            tags.append(tag)
    return tags[:3]
