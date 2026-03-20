from datetime import datetime, timezone
from decimal import Decimal

from data.mt5_v60_schemas import (
    MT5V60AccountSnapshot,
    MT5V60BridgeHealth,
    MT5V60BridgeSnapshot,
    MT5V60LiveTicket,
    MT5V60SymbolSpec,
    MT5V60TicketRecord,
)
from execution.mt5_v60_ticket_registry import MT5V60TicketRegistry


def _snapshot(*, symbol: str, ticket_id: str, side: str = "long") -> MT5V60BridgeSnapshot:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V60BridgeSnapshot(
        server_time=now,
        received_at=now,
        symbol=symbol,
        bid=Decimal("1.1000"),
        ask=Decimal("1.1002"),
        spread_bps=1.8,
        symbol_spec=MT5V60SymbolSpec(
            digits=5,
            point=Decimal("0.00001"),
            tick_size=Decimal("0.00001"),
            tick_value=Decimal("1.00"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5.00"),
            stops_level_points=15,
        ),
        account=MT5V60AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        open_tickets=[
            MT5V60LiveTicket(
                ticket_id=ticket_id,
                symbol=symbol,
                side=side,
                volume_lots=Decimal("0.10"),
                open_price=Decimal("1.1000"),
                current_price=Decimal("1.1001"),
                unrealized_pnl_usd=Decimal("10"),
            )
        ],
        health=MT5V60BridgeHealth(),
    )


def _ticket(*, ticket_id: str, symbol: str, side: str = "long") -> MT5V60TicketRecord:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V60TicketRecord(
        ticket_id=ticket_id,
        symbol=symbol,
        side=side,
        basket_id=f"{symbol}-{side}-1",
        original_volume_lots=Decimal("0.10"),
        current_volume_lots=Decimal("0.10"),
        open_price=Decimal("1.1000"),
        current_price=Decimal("1.1000"),
        stop_loss=Decimal("1.0950"),
        take_profit=Decimal("1.1050"),
        initial_stop_loss=Decimal("1.0950"),
        hard_take_profit=Decimal("1.1050"),
        r_distance_price=Decimal("0.0050"),
        risk_amount_usd=Decimal("50"),
        highest_favorable_close=Decimal("1.1000"),
        lowest_favorable_close=Decimal("1.1000"),
        opened_at=now,
        last_seen_at=now,
    )


def test_v6_1_symbol_scoped_registry_sync_leaves_other_symbols_open() -> None:
    registry = MT5V60TicketRegistry()
    registry.seed(
        [
            _ticket(ticket_id="eur-1", symbol="EURUSD@"),
            _ticket(ticket_id="btc-1", symbol="BTCUSD"),
        ]
    )

    result = registry.sync(_snapshot(symbol="EURUSD@", ticket_id="eur-1"), scope_symbol="EURUSD@")

    assert result.closed == []
    assert registry.by_ticket_id("eur-1") is not None
    assert registry.by_ticket_id("btc-1") is not None
