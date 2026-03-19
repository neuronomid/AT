from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from data.mt5_v60_schemas import MT5V60BridgeSnapshot


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class MT5V60QuoteSample:
    received_at: datetime
    server_time: datetime
    bid: Decimal
    ask: Decimal
    spread_bps: float | None

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")


class MT5V60QuoteTape:
    def __init__(self, *, max_samples: int = 512) -> None:
        self._samples: deque[MT5V60QuoteSample] = deque(maxlen=max_samples)
        self._last_key: tuple[datetime, datetime, Decimal, Decimal, float | None] | None = None

    def ingest(self, snapshot: MT5V60BridgeSnapshot) -> None:
        received_at = _ensure_utc(snapshot.received_at) if snapshot.received_at is not None else _ensure_utc(snapshot.server_time)
        server_time = _ensure_utc(snapshot.server_time)
        key = (received_at, server_time, snapshot.bid, snapshot.ask, snapshot.spread_bps)
        if key == self._last_key:
            return
        self._last_key = key
        self._samples.append(
            MT5V60QuoteSample(
                received_at=received_at,
                server_time=server_time,
                bid=snapshot.bid,
                ask=snapshot.ask,
                spread_bps=snapshot.spread_bps,
            )
        )
        self._trim(received_at)

    def build_payload(
        self,
        *,
        snapshot: MT5V60BridgeSnapshot,
        primary_atr_bps: float | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not self._samples:
            self.ingest(snapshot)
        current_received_at = _ensure_utc(snapshot.received_at) if snapshot.received_at is not None else _ensure_utc(snapshot.server_time)
        self._trim(current_received_at)
        recent_2m = self._window(current_received_at, seconds=120)
        recent_10s = self._window(current_received_at, seconds=10)
        current_spread = snapshot.spread_bps
        spreads = [sample.spread_bps for sample in recent_2m if sample.spread_bps is not None]
        now_utc = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        source_age_ms = max(0, int((now_utc - current_received_at).total_seconds() * 1000))
        return {
            "spread_percentile_2m": self._spread_percentile(current_spread=current_spread, spreads=spreads),
            "spread_to_3m_atr_ratio": self._spread_to_atr_ratio(current_spread=current_spread, primary_atr_bps=primary_atr_bps),
            "sample_count_2m": len(recent_2m),
            "sample_count_10s": len(recent_10s),
            "source_snapshot_age_ms": source_age_ms,
            "source_snapshot_age_bucket": self._age_bucket(source_age_ms),
        }

    def _trim(self, anchor: datetime) -> None:
        cutoff = anchor - timedelta(minutes=5)
        while self._samples and self._samples[0].received_at < cutoff:
            self._samples.popleft()

    def _window(self, anchor: datetime, *, seconds: int) -> list[MT5V60QuoteSample]:
        cutoff = anchor - timedelta(seconds=seconds)
        return [sample for sample in self._samples if sample.received_at >= cutoff]

    def _spread_percentile(self, *, current_spread: float | None, spreads: list[float | None]) -> float | None:
        numeric_spreads = [value for value in spreads if value is not None]
        if current_spread is None or not numeric_spreads:
            return None
        less_or_equal = sum(1 for value in numeric_spreads if value <= current_spread)
        return round((less_or_equal / len(numeric_spreads)) * 100.0, 2)

    def _spread_to_atr_ratio(self, *, current_spread: float | None, primary_atr_bps: float | None) -> float | None:
        if current_spread is None or primary_atr_bps is None or primary_atr_bps <= 0:
            return None
        return round(current_spread / primary_atr_bps, 4)

    def _age_bucket(self, age_ms: int) -> str:
        if age_ms <= 1000:
            return "fresh"
        if age_ms <= 3000:
            return "aging"
        if age_ms <= 5000:
            return "stale_soon"
        return "stale"
