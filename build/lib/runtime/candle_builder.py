from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.schemas import LiveCandle, MarketSnapshot


class CandleBuilder:
    """Aggregates live market snapshots into closed 1-minute candles."""

    def __init__(self, symbol: str, *, max_candles: int = 480, stale_after_seconds: int = 75) -> None:
        self.symbol = symbol
        self._closed_candles: deque[LiveCandle] = deque(maxlen=max_candles)
        self._current: dict[str, object] | None = None
        self._last_snapshot_at: datetime | None = None
        self._stale_after_seconds = stale_after_seconds

    def update(self, snapshot: MarketSnapshot) -> LiveCandle | None:
        if snapshot.symbol != self.symbol:
            return None

        reference_price = self._reference_price(snapshot)
        if reference_price is None or reference_price <= 0:
            return None

        self._last_snapshot_at = snapshot.timestamp.astimezone(timezone.utc)
        minute_start = self._last_snapshot_at.replace(second=0, microsecond=0)
        current = self._current

        if current is None:
            self._current = self._new_bucket(minute_start, snapshot, reference_price)
            return None

        current_start = current["start_at"]
        if not isinstance(current_start, datetime):
            raise RuntimeError("Current candle bucket is invalid.")

        if minute_start != current_start:
            closed = self._freeze_bucket(current)
            self._closed_candles.append(closed)
            self._current = self._new_bucket(minute_start, snapshot, reference_price)
            return closed

        current["end_at"] = self._last_snapshot_at
        current["close_price"] = reference_price
        current["high_price"] = max(current["high_price"], reference_price)
        current["low_price"] = min(current["low_price"], reference_price)
        current["bid_price"] = snapshot.bid_price or current.get("bid_price")
        current["ask_price"] = snapshot.ask_price or current.get("ask_price")
        spread_bps = self._spread_bps(snapshot)
        if spread_bps is not None:
            current["spread_bps"] = spread_bps
        if snapshot.event_type == "trade" and snapshot.last_trade_size is not None:
            current["volume"] += snapshot.last_trade_size
            current["trade_count"] += 1
            current["vwap_notional"] += reference_price * snapshot.last_trade_size
        return None

    def latest_candles(self, limit: int) -> list[LiveCandle]:
        return list(self._closed_candles)[-limit:]

    def latest_snapshot_at(self) -> datetime | None:
        return self._last_snapshot_at

    def latest_snapshot_age_seconds(self, now: datetime | None = None) -> float | None:
        if self._last_snapshot_at is None:
            return None
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return max(0.0, (reference - self._last_snapshot_at).total_seconds())

    def is_stale(self, now: datetime | None = None) -> bool:
        age = self.latest_snapshot_age_seconds(now=now)
        if age is None:
            return True
        return age > self._stale_after_seconds

    def flush(self, now: datetime | None = None) -> list[LiveCandle]:
        if self._current is None:
            return []

        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        target_minute = reference.replace(second=0, microsecond=0)
        closed: list[LiveCandle] = []
        current = self._current
        current_start = current.get("start_at")
        if not isinstance(current_start, datetime):
            raise RuntimeError("Current candle bucket is invalid.")

        while current_start < target_minute:
            closed_candle = self._freeze_bucket(current)
            self._closed_candles.append(closed_candle)
            closed.append(closed_candle)
            next_minute = current_start + timedelta(minutes=1)
            current = self._carry_forward_bucket(next_minute, current)
            current_start = next_minute

        self._current = current
        return closed

    def _new_bucket(
        self,
        minute_start: datetime,
        snapshot: MarketSnapshot,
        reference_price: Decimal,
    ) -> dict[str, object]:
        trade_count = 1 if snapshot.event_type == "trade" and snapshot.last_trade_size is not None else 0
        volume = snapshot.last_trade_size if snapshot.event_type == "trade" and snapshot.last_trade_size is not None else Decimal("0")
        vwap_notional = (reference_price * volume) if volume > 0 else Decimal("0")
        return {
            "symbol": snapshot.symbol,
            "start_at": minute_start,
            "end_at": snapshot.timestamp.astimezone(timezone.utc),
            "open_price": reference_price,
            "high_price": reference_price,
            "low_price": reference_price,
            "close_price": reference_price,
            "volume": volume,
            "trade_count": trade_count,
            "vwap_notional": vwap_notional,
            "bid_price": snapshot.bid_price,
            "ask_price": snapshot.ask_price,
            "spread_bps": self._spread_bps(snapshot),
        }

    def _carry_forward_bucket(self, minute_start: datetime, previous: dict[str, object]) -> dict[str, object]:
        close_price = Decimal(str(previous["close_price"]))
        return {
            "symbol": previous["symbol"],
            "start_at": minute_start,
            "end_at": minute_start + timedelta(minutes=1),
            "open_price": close_price,
            "high_price": close_price,
            "low_price": close_price,
            "close_price": close_price,
            "volume": Decimal("0"),
            "trade_count": 0,
            "vwap_notional": Decimal("0"),
            "bid_price": previous.get("bid_price"),
            "ask_price": previous.get("ask_price"),
            "spread_bps": previous.get("spread_bps"),
        }

    def _freeze_bucket(self, bucket: dict[str, object]) -> LiveCandle:
        open_price = Decimal(str(bucket["open_price"]))
        high_price = Decimal(str(bucket["high_price"]))
        low_price = Decimal(str(bucket["low_price"]))
        close_price = Decimal(str(bucket["close_price"]))
        volume = Decimal(str(bucket["volume"]))
        total_range = high_price - low_price
        body = abs(close_price - open_price)
        upper_wick = max(high_price - max(open_price, close_price), Decimal("0"))
        lower_wick = max(min(open_price, close_price) - low_price, Decimal("0"))
        range_float = float(total_range) if total_range > 0 else 0.0
        vwap = None
        if volume > 0:
            vwap = Decimal(str(bucket["vwap_notional"])) / volume
        close_range_position = 0.5
        if total_range > 0:
            close_range_position = float((close_price - low_price) / total_range)
        return LiveCandle(
            symbol=str(bucket["symbol"]),
            start_at=bucket["start_at"],
            end_at=bucket["end_at"],
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            close_price=close_price,
            volume=volume,
            trade_count=int(bucket["trade_count"]),
            vwap=vwap,
            bid_price=bucket["bid_price"],
            ask_price=bucket["ask_price"],
            spread_bps=bucket["spread_bps"],
            body_pct=(float(body / total_range) if range_float > 0 else 0.0),
            upper_wick_pct=(float(upper_wick / total_range) if range_float > 0 else 0.0),
            lower_wick_pct=(float(lower_wick / total_range) if range_float > 0 else 0.0),
            close_range_position=close_range_position,
        )

    def _reference_price(self, snapshot: MarketSnapshot) -> Decimal | None:
        if snapshot.last_trade_price is not None:
            return snapshot.last_trade_price
        if snapshot.bid_price is not None and snapshot.ask_price is not None:
            return (snapshot.bid_price + snapshot.ask_price) / Decimal("2")
        return snapshot.bid_price or snapshot.ask_price

    def _spread_bps(self, snapshot: MarketSnapshot) -> float | None:
        if snapshot.bid_price is None or snapshot.ask_price is None:
            return None
        midpoint = (snapshot.bid_price + snapshot.ask_price) / Decimal("2")
        if midpoint <= 0:
            return None
        return float(((snapshot.ask_price - snapshot.bid_price) / midpoint) * Decimal("10000"))


def bucket_age_minutes(candle: LiveCandle, now: datetime | None = None) -> float:
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return max(0.0, (reference - candle.end_at).total_seconds() / 60.0)
