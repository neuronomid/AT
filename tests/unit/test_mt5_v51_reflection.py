from datetime import datetime, timezone
from decimal import Decimal

from data.mt5_v51_schemas import MT5V51TicketRecord
from feedback.mt5_v51_reflection import build_mt5_v51_ticket_reflection


def test_mt5_v51_reflection_clamps_close_time_and_sanitizes_extreme_r() -> None:
    opened_at = datetime(2026, 3, 12, 12, 0, 5, tzinfo=timezone.utc)
    last_seen_at = datetime(2026, 3, 12, 12, 0, 1, tzinfo=timezone.utc)
    ticket = MT5V51TicketRecord(
        ticket_id="1001",
        symbol="BTCUSD@",
        side="long",
        basket_id="BTCUSD@-long-1",
        original_volume_lots=Decimal("0.20"),
        current_volume_lots=Decimal("0.20"),
        open_price=Decimal("60100"),
        current_price=Decimal("60034"),
        stop_loss=None,
        take_profit=None,
        initial_stop_loss=Decimal("60100"),
        hard_take_profit=Decimal("60140"),
        soft_take_profit_1=Decimal("60120"),
        soft_take_profit_2=Decimal("60140"),
        r_distance_price=Decimal("0.01"),
        risk_amount_usd=Decimal("40"),
        highest_favorable_close=Decimal("60100"),
        lowest_favorable_close=Decimal("60034"),
        opened_at=opened_at,
        last_seen_at=last_seen_at,
        unrealized_pnl_usd=Decimal("-66"),
        unrealized_r=-6600.0,
    )

    reflection = build_mt5_v51_ticket_reflection(ticket, exit_reason="snapshot_flat")

    assert reflection.closed_at == opened_at
    assert reflection.bars_held == 0
    assert reflection.realized_r == 0.0
