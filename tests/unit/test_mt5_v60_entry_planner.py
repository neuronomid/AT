from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v60_schemas import (
    MT5V60AccountSnapshot,
    MT5V60BridgeHealth,
    MT5V60BridgeSnapshot,
    MT5V60EntryDecision,
    MT5V60RiskDecision,
    MT5V60SymbolSpec,
    MT5V60TicketRecord,
)
from execution.mt5_v60_entry_planner import MT5V60EntryPlanner


def _snapshot() -> MT5V60BridgeSnapshot:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V60BridgeSnapshot(
        server_time=now,
        received_at=now,
        symbol="EURUSD@",
        bid=Decimal("70100"),
        ask=Decimal("70102"),
        spread_bps=0.3,
        symbol_spec=MT5V60SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.50"),
            tick_value=Decimal("1.00"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5.00"),
            stops_level_points=10,
        ),
        account=MT5V60AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9000")),
        health=MT5V60BridgeHealth(),
    )


def test_mt5_v60_entry_planner_builds_valid_long_plan() -> None:
    planner = MT5V60EntryPlanner()
    snapshot = _snapshot()
    decision = MT5V60EntryDecision(
        action="enter_long",
        confidence=0.8,
        rationale="trend",
        thesis_tags=["trend"],
        requested_risk_fraction=0.005,
        stop_loss_price=Decimal("70082"),
        take_profit_price=Decimal("70120"),
        context_signature="bull|bull|bull|tight",
    )
    risk = MT5V60RiskDecision(approved=True, reason="ok", risk_fraction=0.005, risk_posture="neutral")

    plan = planner.plan_entry(decision=decision, snapshot=snapshot, risk_decision=risk)

    assert plan is not None
    assert plan.stop_loss < plan.entry_price < plan.take_profit
    assert plan.risk_fraction == 0.005


def test_mt5_v60_entry_planner_rejects_take_profit_beyond_one_r() -> None:
    planner = MT5V60EntryPlanner()
    snapshot = _snapshot()
    decision = MT5V60EntryDecision(
        action="enter_long",
        confidence=0.8,
        rationale="trend",
        thesis_tags=["trend"],
        requested_risk_fraction=0.005,
        stop_loss_price=Decimal("70082"),
        take_profit_price=Decimal("70150"),
        context_signature="bull|bull|bull|tight",
    )
    risk = MT5V60RiskDecision(approved=True, reason="ok", risk_fraction=0.005, risk_posture="neutral")

    plan = planner.plan_entry(decision=decision, snapshot=snapshot, risk_decision=risk)

    assert plan is None


def test_mt5_v60_entry_planner_salvages_valid_take_profit_when_initial_stop_is_too_close() -> None:
    planner = MT5V60EntryPlanner()
    snapshot = _snapshot()
    now = snapshot.server_time
    ticket = MT5V60TicketRecord(
        ticket_id="1001",
        symbol="EURUSD@",
        side="long",
        basket_id="basket-1",
        original_volume_lots=Decimal("0.10"),
        current_volume_lots=Decimal("0.10"),
        open_price=Decimal("70102"),
        current_price=Decimal("70102"),
        stop_loss=None,
        take_profit=None,
        initial_stop_loss=Decimal("70082"),
        hard_take_profit=Decimal("70120"),
        r_distance_price=Decimal("20"),
        risk_amount_usd=Decimal("50"),
        analysis_mode="standard_entry",
        highest_favorable_close=Decimal("70102"),
        lowest_favorable_close=Decimal("70102"),
        opened_at=now,
        last_seen_at=now,
    )

    command = planner.build_modify_command(
        ticket=ticket,
        snapshot=snapshot,
        stop_loss=Decimal("70097"),
        take_profit=Decimal("70115"),
        reason="Too tight",
        created_at=now,
        expires_at=now + timedelta(seconds=30),
    )

    assert command is not None
    assert command.stop_loss is None
    assert command.take_profit == Decimal("70115.00")


def test_mt5_v60_entry_planner_salvages_valid_initial_stop_when_take_profit_is_too_close() -> None:
    planner = MT5V60EntryPlanner()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = MT5V60BridgeSnapshot(
        server_time=now,
        received_at=now,
        symbol="EURUSD@",
        bid=Decimal("70065.00"),
        ask=Decimal("70065.50"),
        spread_bps=0.07,
        symbol_spec=MT5V60SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.01"),
            tick_value=Decimal("0.01"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5.00"),
            stops_level_points=2500,
        ),
        account=MT5V60AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9000")),
        health=MT5V60BridgeHealth(),
    )
    ticket = MT5V60TicketRecord(
        ticket_id="61638764",
        symbol="EURUSD@",
        side="short",
        basket_id="basket-1",
        original_volume_lots=Decimal("0.10"),
        current_volume_lots=Decimal("0.10"),
        open_price=Decimal("70089.50"),
        current_price=Decimal("70065.25"),
        stop_loss=None,
        take_profit=None,
        initial_stop_loss=Decimal("70138.56"),
        hard_take_profit=Decimal("70040.44"),
        r_distance_price=Decimal("49.06"),
        risk_amount_usd=Decimal("50"),
        analysis_mode="standard_entry",
        highest_favorable_close=Decimal("70065.25"),
        lowest_favorable_close=Decimal("70065.25"),
        opened_at=now,
        last_seen_at=now,
    )

    command = planner.build_modify_command(
        ticket=ticket,
        snapshot=snapshot,
        stop_loss=Decimal("70138.56"),
        take_profit=Decimal("70040.44"),
        reason="Attach first protection",
        created_at=now,
        expires_at=now + timedelta(seconds=30),
    )

    assert command is not None
    assert command.stop_loss == Decimal("70138.56")
    assert command.take_profit is None
