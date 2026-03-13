from collections import deque
from decimal import Decimal
from statistics import pstdev

from data.schemas import MarketSnapshot


class FeatureEngine:
    """Builds deterministic market features from rolling ETH/USD state."""

    def __init__(self, max_snapshots: int = 480) -> None:
        self._reference_prices: deque[Decimal] = deque(maxlen=max_snapshots)
        self._reference_sizes: deque[Decimal] = deque(maxlen=max_snapshots)
        self._high_prices: deque[Decimal] = deque(maxlen=max_snapshots)
        self._low_prices: deque[Decimal] = deque(maxlen=max_snapshots)
        self._true_ranges_bps: deque[float] = deque(maxlen=max_snapshots)
        self._atr_30_history: deque[float] = deque(maxlen=max_snapshots)
        self._ema_values: dict[int, float] = {}
        self._ema_history: dict[int, deque[float]] = {
            20: deque(maxlen=6),
            60: deque(maxlen=6),
            240: deque(maxlen=6),
        }

    def build_features(self, snapshot: MarketSnapshot) -> dict[str, float]:
        features: dict[str, float] = {}
        mid_price = self._mid_price(snapshot)
        reference_price = snapshot.last_trade_price or mid_price
        high_price = snapshot.high_price or reference_price
        low_price = snapshot.low_price or reference_price

        previous_close = self._reference_prices[-1] if self._reference_prices else None
        if reference_price is not None:
            self._reference_prices.append(reference_price)
            self._update_emas(reference_price)
        if high_price is not None:
            self._high_prices.append(high_price)
        if low_price is not None:
            self._low_prices.append(low_price)
        if snapshot.last_trade_size is not None:
            self._reference_sizes.append(snapshot.last_trade_size)
        elif reference_price is not None:
            self._reference_sizes.append(Decimal("0"))

        if previous_close is not None and high_price is not None and low_price is not None:
            self._true_ranges_bps.append(self._true_range_bps(previous_close, high_price, low_price))
            if len(self._true_ranges_bps) >= 30:
                self._atr_30_history.append(self._window_average_float(list(self._true_ranges_bps)[-30:]))

        if snapshot.bid_price is not None and snapshot.ask_price is not None:
            spread = snapshot.ask_price - snapshot.bid_price
            features["spread"] = float(spread)
            if mid_price is not None and mid_price > 0:
                features["spread_bps"] = float((spread / mid_price) * Decimal("10000"))

        if mid_price is not None:
            features["mid_price"] = float(mid_price)
        if reference_price is not None:
            features["reference_price"] = float(reference_price)

        price_series = list(self._reference_prices)
        size_series = list(self._reference_sizes)
        features["sample_count"] = float(len(price_series))

        self._populate_returns_and_volatility(features, price_series)
        self._populate_volume_features(features, size_series)
        self._populate_breakout_features(features, price_series)
        self._populate_ema_features(features)
        self._populate_atr_features(features)
        return features

    def _populate_returns_and_volatility(self, features: dict[str, float], price_series: list[Decimal]) -> None:
        if len(price_series) >= 2:
            features["return_1_bps"] = self._return_bps(price_series[-2], price_series[-1])
        if len(price_series) >= 3:
            features["return_3_bps"] = self._return_bps(price_series[-3], price_series[-1])
        if len(price_series) >= 5:
            features["return_5_bps"] = self._return_bps(price_series[-5], price_series[-1])
            one_step_returns = [
                self._return_bps(previous, current)
                for previous, current in zip(price_series[-5:-1], price_series[-4:])
            ]
            features["volatility_5_bps"] = float(pstdev(one_step_returns)) if len(one_step_returns) > 1 else 0.0
        if len(price_series) >= 15:
            features["return_15_bps"] = self._return_bps(price_series[-15], price_series[-1])
            features["volatility_15_bps"] = self._window_volatility(price_series[-15:])
            features["range_15_bps"] = self._window_range_bps(price_series[-15:])
        if len(price_series) >= 30:
            features["return_30_bps"] = self._return_bps(price_series[-30], price_series[-1])
            features["volatility_30_bps"] = self._window_volatility(price_series[-30:])
            features["range_30_bps"] = self._window_range_bps(price_series[-30:])
            features["zscore_30"] = self._zscore(price_series[-30:])
            volatility_30 = features.get("volatility_30_bps", 0.0)
            if volatility_30 > 0:
                features["volatility_ratio_5_30"] = features.get("volatility_5_bps", 0.0) / volatility_30
            features["trend_strength_bps"] = (
                (features.get("return_5_bps", 0.0) * 0.5)
                + (features.get("return_15_bps", 0.0) * 0.75)
                + features.get("return_30_bps", 0.0)
            ) / 2.25
        if len(price_series) >= 60:
            features["return_60_bps"] = self._return_bps(price_series[-60], price_series[-1])
            features["volatility_60_bps"] = self._window_volatility(price_series[-60:])
        if len(price_series) >= 240:
            features["return_240_bps"] = self._return_bps(price_series[-240], price_series[-1])
            features["volatility_240_bps"] = self._window_volatility(price_series[-240:])

    def _populate_volume_features(self, features: dict[str, float], size_series: list[Decimal]) -> None:
        if len(size_series) >= 5:
            features["volume_avg_5"] = self._window_average_decimal(size_series[-5:])
        if len(size_series) >= 30:
            features["volume_avg_30"] = self._window_average_decimal(size_series[-30:])
            volume_avg_30 = features.get("volume_avg_30", 0.0)
            if volume_avg_30 > 0:
                features["volume_ratio_5_30"] = features.get("volume_avg_5", volume_avg_30) / volume_avg_30
            features["volume_zscore_30"] = self._zscore_decimal(size_series[-30:])

    def _populate_breakout_features(self, features: dict[str, float], price_series: list[Decimal]) -> None:
        if len(price_series) < 21:
            return
        recent_window = price_series[-21:-1]
        current_price = price_series[-1]
        recent_high = max(recent_window)
        recent_low = min(recent_window)
        features["breakout_up_20_bps"] = self._breakout_bps(current_price, recent_high, above=True)
        features["breakdown_20_bps"] = self._breakout_bps(current_price, recent_low, above=False)

    def _populate_ema_features(self, features: dict[str, float]) -> None:
        for period in (20, 60, 240):
            current = self._ema_values.get(period)
            history = self._ema_history[period]
            if current is None:
                continue
            features[f"ema_{period}"] = current
            if len(history) >= 6:
                features[f"ema_slope_{period}_bps"] = self._float_return_bps(history[0], history[-1])
        ema_60 = features.get("ema_60")
        ema_240 = features.get("ema_240")
        if ema_60 is not None and ema_240 is not None:
            features["ema_gap_60_240_bps"] = self._float_return_bps(ema_240, ema_60)

    def _populate_atr_features(self, features: dict[str, float]) -> None:
        if len(self._true_ranges_bps) >= 14:
            features["atr_14_bps"] = self._window_average_float(list(self._true_ranges_bps)[-14:])
        if len(self._true_ranges_bps) >= 30:
            features["atr_30_bps"] = self._window_average_float(list(self._true_ranges_bps)[-30:])
        if self._atr_30_history:
            features["atr_30_percentile"] = self._percentile_rank(list(self._atr_30_history), self._atr_30_history[-1])

    def _update_emas(self, reference_price: Decimal) -> None:
        current_price = float(reference_price)
        for period in (20, 60, 240):
            previous = self._ema_values.get(period)
            if previous is None:
                updated = current_price
            else:
                multiplier = 2.0 / (period + 1)
                updated = ((current_price - previous) * multiplier) + previous
            self._ema_values[period] = updated
            self._ema_history[period].append(updated)

    def _mid_price(self, snapshot: MarketSnapshot) -> Decimal | None:
        if snapshot.bid_price is None or snapshot.ask_price is None:
            return None
        return (snapshot.bid_price + snapshot.ask_price) / Decimal("2")

    def _return_bps(self, start: Decimal, end: Decimal) -> float:
        if start <= 0:
            return 0.0
        return float(((end - start) / start) * Decimal("10000"))

    def _float_return_bps(self, start: float, end: float) -> float:
        if start <= 0:
            return 0.0
        return ((end - start) / start) * 10000.0

    def _window_volatility(self, prices: list[Decimal]) -> float:
        returns = [self._return_bps(previous, current) for previous, current in zip(prices[:-1], prices[1:])]
        if len(returns) <= 1:
            return 0.0
        return float(pstdev(returns))

    def _window_range_bps(self, prices: list[Decimal]) -> float:
        low = min(prices)
        high = max(prices)
        midpoint = (low + high) / Decimal("2")
        if midpoint <= 0:
            return 0.0
        return float(((high - low) / midpoint) * Decimal("10000"))

    def _zscore(self, prices: list[Decimal]) -> float:
        latest = float(prices[-1])
        values = [float(price) for price in prices]
        mean = sum(values) / len(values)
        std = float(pstdev(values)) if len(values) > 1 else 0.0
        if std == 0:
            return 0.0
        return (latest - mean) / std

    def _window_average_decimal(self, values: list[Decimal]) -> float:
        if not values:
            return 0.0
        return float(sum(values) / Decimal(len(values)))

    def _window_average_float(self, values: list[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _zscore_decimal(self, values: list[Decimal]) -> float:
        latest = float(values[-1])
        numeric = [float(value) for value in values]
        mean = sum(numeric) / len(numeric)
        std = float(pstdev(numeric)) if len(numeric) > 1 else 0.0
        if std == 0:
            return 0.0
        return (latest - mean) / std

    def _breakout_bps(self, current: Decimal, boundary: Decimal, *, above: bool) -> float:
        if boundary <= 0:
            return 0.0
        if above and current > boundary:
            return float(((current - boundary) / boundary) * Decimal("10000"))
        if not above and current < boundary:
            return float(((boundary - current) / boundary) * Decimal("10000"))
        return 0.0

    def _true_range_bps(self, previous_close: Decimal, high_price: Decimal, low_price: Decimal) -> float:
        true_range = max(
            high_price - low_price,
            abs(high_price - previous_close),
            abs(low_price - previous_close),
        )
        if previous_close <= 0:
            return 0.0
        return float((true_range / previous_close) * Decimal("10000"))

    def _percentile_rank(self, values: list[float], target: float) -> float:
        if not values:
            return 0.0
        less_or_equal = sum(1 for value in values if value <= target)
        return less_or_equal / len(values)
