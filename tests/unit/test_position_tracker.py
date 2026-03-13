from datetime import datetime, timezone
from decimal import Decimal

from data.schemas import TradeDecision
from execution.position_tracker import PositionTracker


def _buy_decision() -> TradeDecision:
    return TradeDecision(
        action="buy",
        confidence=0.7,
        rationale="trend continuation",
        risk_fraction_equity=0.01,
        take_profit_r=1.0,
        thesis_tags=["trend"],
    )


def test_position_tracker_syncs_to_broker_qty_after_entry_fee() -> None:
    tracker = PositionTracker()
    tracker.open_from_fill(
        opened_at=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
        symbol="ETH/USD",
        fill_price=Decimal("2000"),
        filled_qty=Decimal("5.000000"),
        decision=_buy_decision(),
        risk_amount_usd=Decimal("50"),
        stop_loss_price=Decimal("1990"),
        take_profit_price=Decimal("2020"),
        initial_r_distance=Decimal("10"),
        entry_spread_bps=1.0,
        entry_packet_summary={},
        followed_lessons=[],
    )

    changed = tracker.sync_with_account(
        qty=Decimal("4.987500"),
        avg_entry_price=Decimal("2000"),
    )

    assert changed is True
    assert tracker.open_trade is not None
    assert tracker.open_trade.initial_qty == Decimal("4.987500")
    assert tracker.open_trade.remaining_qty == Decimal("4.987500")


def test_position_tracker_clears_when_broker_reports_flat() -> None:
    tracker = PositionTracker()
    tracker.open_from_fill(
        opened_at=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
        symbol="ETH/USD",
        fill_price=Decimal("2000"),
        filled_qty=Decimal("5.000000"),
        decision=_buy_decision(),
        risk_amount_usd=Decimal("50"),
        stop_loss_price=Decimal("1990"),
        take_profit_price=Decimal("2020"),
        initial_r_distance=Decimal("10"),
        entry_spread_bps=1.0,
        entry_packet_summary={},
        followed_lessons=[],
    )

    changed = tracker.sync_with_account(
        qty=Decimal("0"),
        avg_entry_price=Decimal("0"),
    )

    assert changed is True
    assert tracker.open_trade is None
    assert tracker.has_position() is False
