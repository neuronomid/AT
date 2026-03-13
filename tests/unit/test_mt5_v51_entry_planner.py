from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v51_schemas import (
    MT5V51AccountSnapshot,
    MT5V51Bar,
    MT5V51BridgeHealth,
    MT5V51BridgeSnapshot,
    MT5V51EntryDecision,
    MT5V51RiskDecision,
    MT5V51SymbolSpec,
)
from execution.mt5_v51_entry_planner import MT5V51EntryPlanner


def _snapshot() -> MT5V51BridgeSnapshot:
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    bars_1m = []
    price = Decimal("60000")
    for index in range(30):
        end_at = base - timedelta(minutes=(30 - index))
        start_at = end_at - timedelta(minutes=1)
        close = price + (Decimal("10") * Decimal(str(index)))
        bars_1m.append(
            MT5V51Bar(
                timeframe="1m",
                start_at=start_at,
                end_at=end_at,
                open_price=close - Decimal("8"),
                high_price=close + Decimal("12"),
                low_price=close - Decimal("14"),
                close_price=close,
                tick_volume=100 + index,
            )
        )
    return MT5V51BridgeSnapshot(
        server_time=base + timedelta(seconds=10),
        symbol="BTCUSD",
        bid=Decimal("60310"),
        ask=Decimal("60312"),
        spread_bps=0.3,
        symbol_spec=MT5V51SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.50"),
            tick_value=Decimal("1.00"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5.00"),
            stops_level_points=10,
        ),
        bars_1m=bars_1m,
        bars_5m=[],
        bars_15m=[],
        account=MT5V51AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V51BridgeHealth(),
    )


def test_mt5_v51_entry_planner_builds_long_and_short_plans() -> None:
    planner = MT5V51EntryPlanner()
    snapshot = _snapshot()
    risk_decision = MT5V51RiskDecision(approved=True, reason="ok", risk_fraction=0.004, risk_posture="neutral")

    long_plan = planner.plan_entry(
        decision=MT5V51EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"]),
        snapshot=snapshot,
        risk_decision=risk_decision,
        ticket_sequence=1,
    )
    short_plan = planner.plan_entry(
        decision=MT5V51EntryDecision(action="enter_short", confidence=0.7, rationale="trend", thesis_tags=["trend"]),
        snapshot=snapshot,
        risk_decision=risk_decision,
        ticket_sequence=1,
    )

    assert long_plan is not None
    assert short_plan is not None
    assert long_plan.stop_loss < long_plan.entry_price < long_plan.soft_take_profit_1 < long_plan.soft_take_profit_2
    assert short_plan.soft_take_profit_2 < short_plan.soft_take_profit_1 < short_plan.entry_price < short_plan.stop_loss
    assert long_plan.take_profit == long_plan.soft_take_profit_2
    assert short_plan.take_profit == short_plan.soft_take_profit_2
    assert long_plan.soft_take_profit_2 - long_plan.entry_price == long_plan.entry_price - long_plan.stop_loss
    assert short_plan.entry_price - short_plan.soft_take_profit_2 == short_plan.stop_loss - short_plan.entry_price
    assert long_plan.volume_lots >= snapshot.symbol_spec.volume_min
    assert long_plan.stop_loss % snapshot.symbol_spec.tick_size == 0

    long_command = planner.build_entry_command(
        plan=long_plan,
        reason="trend",
        created_at=snapshot.server_time,
        expires_at=snapshot.server_time + timedelta(seconds=5),
        thesis_tags=["trend"],
        context_signature="bull|bull|bull|tight",
        followed_lessons=[],
    )

    assert long_command.stop_loss is None
    assert long_command.take_profit is None
    assert long_command.metadata["initial_stop_loss"] == float(long_plan.stop_loss)
    assert long_command.metadata["attach_protection_after_fill"] is True
