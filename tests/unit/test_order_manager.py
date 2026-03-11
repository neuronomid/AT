from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.schemas import OrderSnapshot, TradeUpdate
from execution.order_manager import OrderManager


def _order(status: str) -> OrderSnapshot:
    return OrderSnapshot(
        id="order-1",
        client_order_id="client-1",
        symbol="ETH/USD",
        side="buy",
        type="market",
        time_in_force="gtc",
        status=status,
        qty=Decimal("0.01"),
    )


def test_order_manager_tracks_pending_orders() -> None:
    manager = OrderManager()
    manager.mark_pending(_order("new"))

    assert manager.has_pending_order("ETH/USD") is True


def test_order_manager_clears_filled_orders_and_starts_cooldown() -> None:
    manager = OrderManager()
    manager.mark_pending(_order("new"))
    fill_time = datetime.now(timezone.utc)
    manager.apply_update(
        TradeUpdate(
            event="fill",
            order=_order("filled"),
            timestamp=fill_time,
            price=Decimal("2000"),
            qty=Decimal("0.01"),
        )
    )

    assert manager.has_pending_order("ETH/USD") is False
    assert manager.in_cooldown(60, now=fill_time + timedelta(seconds=30)) is True
