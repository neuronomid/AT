from datetime import datetime, timedelta, timezone

from data.schemas import OrderSnapshot, TradeUpdate


class OrderManager:
    """Tracks pending orders and reconciliation state."""

    def __init__(self) -> None:
        self.pending_orders: dict[str, OrderSnapshot] = {}
        self.last_trade_at: datetime | None = None

    def mark_pending(self, order: OrderSnapshot) -> None:
        self.pending_orders[order.id] = order

    def apply_update(self, update: TradeUpdate) -> None:
        self.pending_orders[update.order.id] = update.order
        if update.event in {"fill", "partial_fill"}:
            self.last_trade_at = update.timestamp or datetime.now(timezone.utc)
        if update.event in {"fill", "canceled", "expired", "rejected"}:
            self.pending_orders.pop(update.order.id, None)

    def has_pending_order(self, symbol: str) -> bool:
        normalized = self._normalize_symbol(symbol)
        return any(self._normalize_symbol(order.symbol) == normalized for order in self.pending_orders.values())

    def in_cooldown(self, cooldown_seconds: int, now: datetime | None = None) -> bool:
        if self.last_trade_at is None or cooldown_seconds <= 0:
            return False
        reference = now or datetime.now(timezone.utc)
        return reference < self.last_trade_at + timedelta(seconds=cooldown_seconds)

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "").replace("-", "").upper()
