from __future__ import annotations

from uuid import uuid4

from data.mt5_v51_schemas import MT5V51TicketRecord
from data.schemas import LessonRecord, TradeReflection


def build_mt5_v51_ticket_reflection(
    ticket: MT5V51TicketRecord,
    *,
    exit_reason: str,
) -> TradeReflection:
    avoid_lessons, reinforce_lessons = _ticket_lesson_messages(ticket=ticket, exit_reason=exit_reason)
    closed_at = ticket.last_seen_at if ticket.last_seen_at >= ticket.opened_at else ticket.opened_at
    held_seconds = max((closed_at - ticket.opened_at).total_seconds(), 0.0)
    bars_held = int(held_seconds // 60)
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


def derive_mt5_v51_lessons(reflection: TradeReflection) -> list[LessonRecord]:
    lessons: list[LessonRecord] = []
    for message in reflection.avoid_lessons[:3]:
        lessons.append(
            LessonRecord(
                lesson_id=str(uuid4()),
                category="v5_1_feedback",
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
                category="v5_1_feedback",
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


def _ticket_lesson_messages(ticket: MT5V51TicketRecord, exit_reason: str) -> tuple[list[str], list[str]]:
    direction = "long" if ticket.side == "long" else "short"
    avoid: list[str] = []
    reinforce: list[str] = []
    if ticket.unrealized_pnl_usd < 0:
        avoid.append(
            f"Avoid repeating {direction} BTC setups in {ticket.context_signature or 'the same context'} when the prior thesis tags were {', '.join(ticket.thesis_tags) or 'unspecified'}."
        )
        if exit_reason in {"hard_stop", "snapshot_flat"}:
            avoid.append(f"Avoid holding a losing {direction} BTC thesis once the 1m structure invalidates the entry.")
    else:
        reinforce.append(
            f"Reinforce {direction} BTC scalp setups tagged {', '.join(ticket.thesis_tags) or 'momentum'} when the 1m move keeps pressing and the 20s tape does not reverse."
        )
        if ticket.partial_stage >= 1:
            reinforce.append("Reinforce harvesting partial BTC profits before trailing the remainder.")
    if ticket.unrealized_r >= 1.0 and ticket.unrealized_pnl_usd > 0:
        reinforce.append("Reinforce letting BTC winners breathe only after banking part of the move.")
    if ticket.unrealized_r <= -0.75:
        avoid.append(f"Avoid {direction} BTC entries that move deep against the position before follow-through.")
    return avoid[:3], reinforce[:3]


def _sanitized_realized_r(ticket: MT5V51TicketRecord) -> float:
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
        "partial": "partial_then_trail",
        "breathe": "let_winner_breathe",
        "does not reverse": "micro_confirm",
        "follow-through": "wait_for_follow_through",
        "repeat": "avoid_repeat_context",
    }
    for needle, tag in heuristics.items():
        if needle in message_lower and tag not in tags:
            tags.append(tag)
    return tags[:3]
