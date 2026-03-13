from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v51_schemas import MT5V51Bar, MT5V51BridgeSnapshot


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MT5V51Synthetic20sBuilder:
    def __init__(
        self,
        symbol: str,
        *,
        max_bars: int = 90,
        warmup_bars: int = 30,
        bucket_seconds: int = 20,
    ) -> None:
        self._symbol = symbol.strip().upper()
        self._max_bars = max_bars
        self._warmup_bars = warmup_bars
        self._bucket_seconds = bucket_seconds
        self._closed: deque[MT5V51Bar] = deque(maxlen=max_bars)
        self._current: dict[str, object] | None = None
        self._last_snapshot_key: tuple[datetime | None, datetime] | None = None

    @property
    def warmup_bars(self) -> int:
        return self._warmup_bars

    def warmup_complete(self) -> bool:
        return len(self._closed) >= self._warmup_bars

    def closed_bar_count(self) -> int:
        return len(self._closed)

    def enrich_snapshot(self, snapshot: MT5V51BridgeSnapshot) -> MT5V51BridgeSnapshot:
        if snapshot.symbol.strip().upper() != self._symbol:
            return snapshot
        received_at = _ensure_utc(snapshot.received_at) if snapshot.received_at is not None else None
        key = (received_at, _ensure_utc(snapshot.server_time))
        if self._last_snapshot_key != key:
            self._ingest(snapshot)
            self._last_snapshot_key = key
        return snapshot.model_copy(update={"bars_20s": list(self._closed)})

    def _ingest(self, snapshot: MT5V51BridgeSnapshot) -> None:
        timestamp = _ensure_utc(snapshot.server_time)
        price = snapshot.midpoint
        bucket_start = self._bucket_start(timestamp)
        bucket_end = bucket_start + timedelta(seconds=self._bucket_seconds)
        if self._current is None:
            self._current = self._new_bucket(bucket_start=bucket_start, bucket_end=bucket_end, price=price, spread_bps=snapshot.spread_bps)
            return
        current_start = self._current["start_at"]
        if not isinstance(current_start, datetime):
            raise RuntimeError("Synthetic 20s candle bucket is invalid.")
        if bucket_start < current_start:
            return
        if bucket_start != current_start:
            self._closed.append(self._freeze_bucket(self._current))
            self._current = self._new_bucket(bucket_start=bucket_start, bucket_end=bucket_end, price=price, spread_bps=snapshot.spread_bps)
            return
        self._current["high_price"] = max(Decimal(str(self._current["high_price"])), price)
        self._current["low_price"] = min(Decimal(str(self._current["low_price"])), price)
        self._current["close_price"] = price
        self._current["spread_bps"] = snapshot.spread_bps
        self._current["tick_volume"] = int(self._current["tick_volume"]) + 1
        self._current["volume"] = Decimal(str(self._current["volume"])) + Decimal("1")

    def _new_bucket(
        self,
        *,
        bucket_start: datetime,
        bucket_end: datetime,
        price: Decimal,
        spread_bps: float | None,
    ) -> dict[str, object]:
        return {
            "start_at": bucket_start,
            "end_at": bucket_end,
            "open_price": price,
            "high_price": price,
            "low_price": price,
            "close_price": price,
            "volume": Decimal("1"),
            "tick_volume": 1,
            "spread_bps": spread_bps,
        }

    def _freeze_bucket(self, bucket: dict[str, object]) -> MT5V51Bar:
        return MT5V51Bar(
            timeframe="20s",
            start_at=bucket["start_at"],
            end_at=bucket["end_at"],
            open_price=Decimal(str(bucket["open_price"])),
            high_price=Decimal(str(bucket["high_price"])),
            low_price=Decimal(str(bucket["low_price"])),
            close_price=Decimal(str(bucket["close_price"])),
            volume=Decimal(str(bucket["volume"])),
            tick_volume=int(bucket["tick_volume"]),
            spread_bps=bucket["spread_bps"],
            complete=True,
        )

    def _bucket_start(self, timestamp: datetime) -> datetime:
        second_bucket = (timestamp.second // self._bucket_seconds) * self._bucket_seconds
        return timestamp.replace(second=second_bucket, microsecond=0)
