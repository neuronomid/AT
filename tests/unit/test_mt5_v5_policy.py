from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.schemas import BridgeHealth, BridgeSnapshot, EntryDecision, MT5AccountSnapshot, MT5Bar, TicketState, TradeReflection
from execution.mt5_ticket_book import MT5TicketBook
from risk.mt5_v5_policy import MT5RiskPostureEngine, MT5V5RiskArbiter


def _bars(base: datetime) -> list[MT5Bar]:
    bars = []
    price = Decimal("1.1000")
    for index in range(15):
        end_at = base - timedelta(minutes=(14 - index) * 5)
        start_at = end_at - timedelta(minutes=5)
        close = price + (Decimal("0.0001") * Decimal(str(index)))
        bars.append(
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
    return bars


def _snapshot(server_time: datetime, *, tickets: list[TicketState] | None = None) -> BridgeSnapshot:
    return BridgeSnapshot(
        server_time=server_time,
        symbol="EURUSD",
        bid=Decimal("1.1010"),
        ask=Decimal("1.1012"),
        spread_bps=1.8,
        bars_5m=_bars(server_time),
        bars_15m=[],
        bars_4h=[],
        account=MT5AccountSnapshot(
            balance=Decimal("10000"),
            equity=Decimal("10000"),
            free_margin=Decimal("9500"),
            account_mode="hedging",
        ),
        open_tickets=tickets or [],
        health=BridgeHealth(),
    )


def test_v5_risk_policy_rejects_when_bar_window_is_missed() -> None:
    arbiter = MT5V5RiskArbiter(symbol="EURUSD")
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    snapshot = _snapshot(base + timedelta(minutes=1, seconds=1))
    snapshot.bars_5m = _bars(base)
    book = MT5TicketBook()
    book.sync([])

    decision = EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"])
    risk = arbiter.evaluate_entry(
        decision=decision,
        snapshot=snapshot,
        ticket_book=book,
        risk_posture="neutral",
        risk_multiplier=1.0,
        pending_symbol_command=False,
        new_entries_this_bar=0,
    )

    assert risk.approved is False
    assert "60-second" in risk.reason


def test_v5_risk_policy_blocks_second_ticket_without_protection() -> None:
    arbiter = MT5V5RiskArbiter(symbol="EURUSD")
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    ticket = TicketState(
        ticket_id="1",
        symbol="EURUSD",
        side="long",
        volume_lots=Decimal("0.10"),
        open_price=Decimal("1.1000"),
        current_price=Decimal("1.1005"),
        stop_loss=Decimal("1.0990"),
        risk_amount_usd=Decimal("35"),
        opened_at=base - timedelta(minutes=5),
        protected=False,
        basket_id="basket-1",
    )
    snapshot = _snapshot(base + timedelta(seconds=20), tickets=[ticket])
    book = MT5TicketBook()
    book.sync([ticket])

    decision = EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"])
    risk = arbiter.evaluate_entry(
        decision=decision,
        snapshot=snapshot,
        ticket_book=book,
        risk_posture="neutral",
        risk_multiplier=1.0,
        pending_symbol_command=False,
        new_entries_this_bar=0,
    )

    assert risk.approved is False
    assert "Second-ticket" in risk.reason


def test_risk_posture_engine_detects_reduced_state() -> None:
    engine = MT5RiskPostureEngine()
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    reflections = [
        TradeReflection(
            reflection_id=str(index),
            symbol="EURUSD",
            side="long",
            opened_at=base,
            closed_at=base,
            bars_held=1,
            entry_price=Decimal("1.1"),
            exit_price=Decimal("1.0"),
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
