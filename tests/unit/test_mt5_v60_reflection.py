from datetime import datetime, timezone
from decimal import Decimal

from data.mt5_v60_schemas import MT5V60TicketRecord
from feedback.mt5_v60_reflection import build_mt5_v60_ticket_reflection


def test_mt5_v60_reflection_uses_ticket_symbol_in_lessons() -> None:
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
    ticket = MT5V60TicketRecord(
        ticket_id="1001",
        symbol="EURUSD@",
        side="short",
        basket_id="EURUSD-short-1",
        original_volume_lots=Decimal("0.10"),
        current_volume_lots=Decimal("0.10"),
        open_price=Decimal("1.08420"),
        current_price=Decimal("1.08490"),
        stop_loss=Decimal("1.08480"),
        take_profit=Decimal("1.08360"),
        initial_stop_loss=Decimal("1.08480"),
        hard_take_profit=Decimal("1.08360"),
        r_distance_price=Decimal("0.00060"),
        risk_amount_usd=Decimal("50"),
        highest_favorable_close=Decimal("1.08410"),
        lowest_favorable_close=Decimal("1.08390"),
        opened_at=now,
        last_seen_at=now,
        thesis_tags=["breakdown"],
        context_signature="bear|bear|bear|clean",
        unrealized_pnl_usd=Decimal("-50"),
        unrealized_r=-1.0,
    )

    reflection = build_mt5_v60_ticket_reflection(ticket, exit_reason="stop_loss")

    assert reflection.avoid_lessons
    assert any("EURUSD" in message for message in reflection.avoid_lessons)
    assert any("short EURUSD thesis" in message or "short EURUSD entries" in message for message in reflection.avoid_lessons)
