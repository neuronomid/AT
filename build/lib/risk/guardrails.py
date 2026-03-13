from decimal import Decimal

from data.schemas import MarketSnapshot


class Guardrails:
    def check_trade_frequency(self, trades_this_hour: int, max_trades_per_hour: int) -> bool:
        return trades_this_hour < max_trades_per_hour

    def calculate_spread_bps(self, snapshot: MarketSnapshot) -> Decimal | None:
        if snapshot.bid_price is None or snapshot.ask_price is None:
            return None
        midpoint = (snapshot.bid_price + snapshot.ask_price) / Decimal("2")
        if midpoint <= 0:
            return None
        return ((snapshot.ask_price - snapshot.bid_price) / midpoint) * Decimal("10000")
