from datetime import datetime, timezone
from decimal import Decimal

from data.schemas import TradeDecision
from execution.position_tracker import PositionTracker
from feedback.reflection import build_trade_reflection, derive_lessons


def test_reflection_and_lessons_capture_positive_trade() -> None:
    tracker = PositionTracker()
    tracker.open_from_fill(
        opened_at=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
        symbol="ETH/USD",
        fill_price=Decimal("3000"),
        filled_qty=Decimal("1"),
        decision=TradeDecision(
            action="buy",
            confidence=0.8,
            rationale="trend",
            risk_fraction_equity=0.01,
            take_profit_r=1.0,
            thesis_tags=["trend"],
            context_signature="bull_stack|mid_atr|inside|tight_spread|trend",
        ),
        risk_amount_usd=Decimal("50"),
        stop_loss_price=Decimal("2990"),
        take_profit_price=Decimal("3010"),
        initial_r_distance=Decimal("10"),
        entry_spread_bps=2.0,
        entry_packet_summary={"indicator_snapshot": {"ema_5": 1}},
        followed_lessons=["Stay with aligned trend structure."],
    )
    tracker.record_candle(Decimal("3012"))
    tracker.apply_sell_fill(
        fill_price=Decimal("3012"),
        filled_qty=Decimal("1"),
        decision=TradeDecision(action="exit", confidence=1.0, rationale="take profit"),
    )
    completed = tracker.apply_sell_fill(
        fill_price=Decimal("3012"),
        filled_qty=Decimal("0"),
        decision=TradeDecision(action="exit", confidence=1.0, rationale="noop"),
    )

    assert completed is None

    tracker = PositionTracker()
    tracker.open_from_fill(
        opened_at=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
        symbol="ETH/USD",
        fill_price=Decimal("3000"),
        filled_qty=Decimal("1"),
        decision=TradeDecision(
            action="buy",
            confidence=0.8,
            rationale="trend",
            risk_fraction_equity=0.01,
            take_profit_r=1.0,
            thesis_tags=["trend"],
            context_signature="bull_stack|mid_atr|inside|tight_spread|trend",
        ),
        risk_amount_usd=Decimal("50"),
        stop_loss_price=Decimal("2990"),
        take_profit_price=Decimal("3010"),
        initial_r_distance=Decimal("10"),
        entry_spread_bps=2.0,
        entry_packet_summary={"indicator_snapshot": {"ema_5": 1}},
        followed_lessons=["Stay with aligned trend structure."],
    )
    tracker.record_candle(Decimal("3015"))
    completed = tracker.apply_sell_fill(
        fill_price=Decimal("3015"),
        filled_qty=Decimal("1"),
        decision=TradeDecision(action="exit", confidence=1.0, rationale="close"),
    )

    assert completed is not None
    reflection = build_trade_reflection(
        completed,
        closed_at=datetime(2026, 3, 12, 12, 15, tzinfo=timezone.utc),
        exit_price=Decimal("3015"),
        exit_reason="llm_exit",
        spread_bps_exit=1.5,
    )
    lessons = derive_lessons(reflection)

    assert reflection.realized_pnl_usd > 0
    assert any(lesson.metadata.get("polarity") == "reinforce" for lesson in lessons)
