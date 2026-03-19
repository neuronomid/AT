from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v60_schemas import (
    MT5V60AccountSnapshot,
    MT5V60Bar,
    MT5V60BridgeHealth,
    MT5V60BridgeSnapshot,
    MT5V60EntryDecision,
    MT5V60SymbolSpec,
)
from execution.mt5_v60_ticket_registry import MT5V60TicketRegistry
from risk.mt5_v60_policy import MT5V60RiskArbiter


def _bars(base: datetime) -> list[MT5V60Bar]:
    bars = []
    price = Decimal("70000")
    for index in range(20):
        end_at = base - timedelta(minutes=(19 - index))
        start_at = end_at - timedelta(minutes=1)
        close = price + (Decimal("10") * Decimal(str(index)))
        bars.append(
            MT5V60Bar(
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


def _snapshot(server_time: datetime) -> MT5V60BridgeSnapshot:
    return MT5V60BridgeSnapshot(
        server_time=server_time,
        received_at=datetime.now(timezone.utc),
        symbol="BTCUSD@",
        bid=Decimal("70100"),
        ask=Decimal("70102"),
        spread_bps=0.4,
        symbol_spec=MT5V60SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.01"),
            tick_value=Decimal("0.01"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5"),
            stops_level_points=10,
        ),
        bars_1m=_bars(server_time),
        bars_2m=[],
        bars_3m=[],
        bars_5m=[],
        account=MT5V60AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V60BridgeHealth(),
    )


def test_mt5_v60_risk_policy_rejects_stale_preflight_snapshot() -> None:
    arbiter = MT5V60RiskArbiter(symbol="BTCUSD@", stale_after_seconds=5)
    registry = MT5V60TicketRegistry()
    snapshot = _snapshot(datetime.now(timezone.utc))
    snapshot.received_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    decision = MT5V60EntryDecision(action="enter_short", confidence=0.75, rationale="trend", thesis_tags=["trend"])

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


def test_mt5_v60_risk_policy_can_allow_stale_snapshot_for_post_llm_execution() -> None:
    arbiter = MT5V60RiskArbiter(symbol="BTCUSD@", stale_after_seconds=5)
    registry = MT5V60TicketRegistry()
    snapshot = _snapshot(datetime.now(timezone.utc))
    snapshot.received_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    decision = MT5V60EntryDecision(
        action="enter_short",
        confidence=0.75,
        rationale="trend",
        thesis_tags=["trend"],
        requested_risk_fraction=0.003,
        stop_loss_price=Decimal("70140"),
        take_profit_price=Decimal("70060"),
    )

    risk = arbiter.evaluate_entry(
        decision=decision,
        snapshot=snapshot,
        registry=registry,
        risk_posture="neutral",
        risk_multiplier=1.0,
        pending_symbol_command=False,
        allow_stale_snapshot=True,
    )

    assert risk.approved is True
    assert "deterministic checks" in risk.reason
