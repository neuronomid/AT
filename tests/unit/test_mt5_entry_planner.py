from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.schemas import BridgeHealth, BridgeSnapshot, EntryDecision, MT5AccountSnapshot, MT5Bar, MT5RiskDecision
from execution.mt5_entry_planner import MT5EntryPlanner


def _snapshot() -> BridgeSnapshot:
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    bars_5m = []
    price = Decimal("1.1000")
    for index in range(20):
        end_at = base - timedelta(minutes=(20 - index) * 5)
        start_at = end_at - timedelta(minutes=5)
        close = price + (Decimal("0.0002") * Decimal(str(index)))
        bars_5m.append(
            MT5Bar(
                timeframe="5m",
                start_at=start_at,
                end_at=end_at,
                open_price=close - Decimal("0.0001"),
                high_price=close + Decimal("0.0002"),
                low_price=close - Decimal("0.0002"),
                close_price=close,
            )
        )
    return BridgeSnapshot(
        server_time=base + timedelta(seconds=20),
        symbol="EURUSD",
        bid=Decimal("1.1038"),
        ask=Decimal("1.1040"),
        spread_bps=1.8,
        bars_5m=bars_5m,
        bars_15m=[],
        bars_4h=[],
        account=MT5AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=BridgeHealth(),
    )


def test_entry_planner_builds_long_and_short_plans() -> None:
    planner = MT5EntryPlanner()
    snapshot = _snapshot()
    risk_decision = MT5RiskDecision(approved=True, reason="ok", risk_fraction=0.005, risk_posture="neutral")

    long_plan = planner.plan_entry(
        decision=EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"]),
        snapshot=snapshot,
        risk_decision=risk_decision,
        ticket_sequence=1,
    )
    short_plan = planner.plan_entry(
        decision=EntryDecision(action="enter_short", confidence=0.7, rationale="trend", thesis_tags=["trend"]),
        snapshot=snapshot,
        risk_decision=risk_decision,
        ticket_sequence=1,
    )

    assert long_plan is not None
    assert short_plan is not None
    assert long_plan.stop_loss < long_plan.entry_price < long_plan.take_profit
    assert short_plan.take_profit < short_plan.entry_price < short_plan.stop_loss
