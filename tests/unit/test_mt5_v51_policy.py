from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v51_schemas import (
    MT5V51AccountSnapshot,
    MT5V51Bar,
    MT5V51BridgeHealth,
    MT5V51BridgeSnapshot,
    MT5V51EntryDecision,
    MT5V51SymbolSpec,
)
from execution.mt5_v51_ticket_registry import MT5V51TicketRegistry
from risk.mt5_v51_policy import MT5V51RiskArbiter, MT5V51RiskPostureEngine
from data.schemas import TradeReflection


def _bars(base: datetime) -> list[MT5V51Bar]:
    bars = []
    price = Decimal("60000")
    for index in range(20):
        end_at = base - timedelta(minutes=(19 - index))
        start_at = end_at - timedelta(minutes=1)
        close = price + (Decimal("10") * Decimal(str(index)))
        bars.append(
            MT5V51Bar(
                timeframe="1m",
                start_at=start_at,
                end_at=end_at,
                open_price=close - Decimal("4"),
                high_price=close + Decimal("7"),
                low_price=close - Decimal("9"),
                close_price=close,
                tick_volume=100 + index,
            )
        )
    return bars


def _snapshot(server_time: datetime) -> MT5V51BridgeSnapshot:
    return MT5V51BridgeSnapshot(
        server_time=server_time,
        received_at=datetime.now(timezone.utc),
        symbol="BTCUSD",
        bid=Decimal("60100"),
        ask=Decimal("60102"),
        spread_bps=0.4,
        symbol_spec=MT5V51SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.50"),
            tick_value=Decimal("1"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5"),
            stops_level_points=10,
        ),
        bars_1m=_bars(server_time),
        bars_5m=[],
        bars_15m=[],
        account=MT5V51AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V51BridgeHealth(),
    )


def test_mt5_v51_risk_policy_allows_fresh_preflight_after_original_bar_window() -> None:
    arbiter = MT5V51RiskArbiter(symbol="BTCUSD")
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    snapshot = _snapshot(base + timedelta(minutes=2))
    snapshot.bars_1m = _bars(base)
    registry = MT5V51TicketRegistry()

    decision = MT5V51EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"])
    risk = arbiter.evaluate_entry(
        decision=decision,
        snapshot=snapshot,
        registry=registry,
        risk_posture="neutral",
        risk_multiplier=1.0,
        pending_symbol_command=False,
    )

    assert risk.approved is True
    assert "deterministic checks" in risk.reason


def test_mt5_v51_risk_policy_blocks_when_trade_cap_is_hit() -> None:
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    times = [base - timedelta(minutes=index) for index in range(15)]
    arbiter = MT5V51RiskArbiter(symbol="BTCUSD", seeded_entry_times=times)
    registry = MT5V51TicketRegistry()
    decision = MT5V51EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"])

    risk = arbiter.evaluate_entry(
        decision=decision,
        snapshot=_snapshot(base),
        registry=registry,
        risk_posture="neutral",
        risk_multiplier=1.0,
        pending_symbol_command=False,
    )

    assert risk.approved is False
    assert "rolling 60-minute" in risk.reason


def test_mt5_v51_risk_policy_rejects_stale_preflight_snapshot() -> None:
    arbiter = MT5V51RiskArbiter(symbol="BTCUSD", stale_after_seconds=5)
    registry = MT5V51TicketRegistry()
    snapshot = _snapshot(datetime.now(timezone.utc))
    snapshot.received_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    decision = MT5V51EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"])

    risk = arbiter.evaluate_entry(
        decision=decision,
        snapshot=snapshot,
        registry=registry,
        risk_posture="neutral",
        risk_multiplier=1.0,
        pending_symbol_command=False,
    )

    assert risk.approved is False
    assert "stale" in risk.reason.lower()


def test_mt5_v51_risk_policy_handles_naive_server_time_with_aware_received_at() -> None:
    arbiter = MT5V51RiskArbiter(symbol="BTCUSD")
    registry = MT5V51TicketRegistry()
    server_time = datetime(2026, 3, 12, 12, 0)
    snapshot = _snapshot(server_time)
    snapshot.received_at = datetime.now(timezone.utc)
    decision = MT5V51EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"])

    risk = arbiter.evaluate_entry(
        decision=decision,
        snapshot=snapshot,
        registry=registry,
        risk_posture="neutral",
        risk_multiplier=1.0,
        pending_symbol_command=False,
    )

    assert risk.approved is True
    assert "deterministic checks" in risk.reason


def test_mt5_v51_risk_policy_handles_naive_server_time_with_aware_seeded_entries() -> None:
    aware_base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    arbiter = MT5V51RiskArbiter(
        symbol="BTCUSD",
        seeded_entry_times=[aware_base - timedelta(minutes=index) for index in range(3)],
    )
    registry = MT5V51TicketRegistry()
    server_time = datetime(2026, 3, 12, 12, 30)
    snapshot = _snapshot(server_time)
    snapshot.received_at = datetime.now(timezone.utc)
    decision = MT5V51EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"])

    risk = arbiter.evaluate_entry(
        decision=decision,
        snapshot=snapshot,
        registry=registry,
        risk_posture="neutral",
        risk_multiplier=1.0,
        pending_symbol_command=False,
    )

    assert risk.approved is True
    assert "deterministic checks" in risk.reason


def test_mt5_v51_risk_posture_engine_detects_reduced_state() -> None:
    engine = MT5V51RiskPostureEngine()
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    reflections = [
        TradeReflection(
            reflection_id=str(index),
            symbol="BTCUSD",
            side="long",
            opened_at=base,
            closed_at=base,
            bars_held=1,
            entry_price=Decimal("60000"),
            exit_price=Decimal("59900"),
            qty=Decimal("0.1"),
            realized_pnl_usd=Decimal("-10"),
            realized_r=-0.5,
            exit_reason="stop",
        )
        for index in range(3)
    ]

    posture, multiplier = engine.derive(reflections)

    assert posture == "reduced"
    assert multiplier == 0.75
