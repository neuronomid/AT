from collections import deque
from decimal import Decimal
from statistics import pstdev

from data.schemas import MarketSnapshot


class FeatureEngine:
    """Builds deterministic market features from rolling ETH/USD state."""

    def __init__(self, max_snapshots: int = 120) -> None:
        self._snapshots: deque[MarketSnapshot] = deque(maxlen=max_snapshots)

    def build_features(self, snapshot: MarketSnapshot) -> dict[str, float]:
        self._snapshots.append(snapshot)
        features: dict[str, float] = {}
        mid_price = self._mid_price(snapshot)
        reference_price = snapshot.last_trade_price or mid_price

        if snapshot.bid_price is not None and snapshot.ask_price is not None:
            spread = snapshot.ask_price - snapshot.bid_price
            features["spread"] = float(spread)
            if mid_price is not None and mid_price > 0:
                features["spread_bps"] = float((spread / mid_price) * Decimal("10000"))

        if mid_price is not None:
            features["mid_price"] = float(mid_price)
        if reference_price is not None:
            features["reference_price"] = float(reference_price)

        price_series = [price for item in self._snapshots if (price := item.last_trade_price or self._mid_price(item)) is not None]
        features["sample_count"] = float(len(price_series))

        if len(price_series) >= 2:
            features["return_1_bps"] = self._return_bps(price_series[-2], price_series[-1])
        if len(price_series) >= 3:
            features["return_3_bps"] = self._return_bps(price_series[-3], price_series[-1])
        if len(price_series) >= 5:
            features["return_5_bps"] = self._return_bps(price_series[-5], price_series[-1])
            one_step_returns = [self._return_bps(previous, current) for previous, current in zip(price_series[-5:-1], price_series[-4:])]
            features["volatility_5_bps"] = float(pstdev(one_step_returns)) if len(one_step_returns) > 1 else 0.0

        return features

    def _mid_price(self, snapshot: MarketSnapshot) -> Decimal | None:
        if snapshot.bid_price is None or snapshot.ask_price is None:
            return None
        return (snapshot.bid_price + snapshot.ask_price) / Decimal("2")

    def _return_bps(self, start: Decimal, end: Decimal) -> float:
        if start <= 0:
            return 0.0
        return float(((end - start) / start) * Decimal("10000"))
