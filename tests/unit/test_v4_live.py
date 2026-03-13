from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.v4_live import _market_stale_age_seconds
from data.schemas import MarketSnapshot
from runtime.candle_builder import CandleBuilder


def test_market_stale_age_uses_freshest_market_evidence() -> None:
    builder = CandleBuilder("ETH/USD")
    base_time = datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
    builder.update(
        MarketSnapshot(
            symbol="ETH/USD",
            timestamp=base_time,
            event_type="quote",
            bid_price=Decimal("2000"),
            ask_price=Decimal("2000.5"),
        )
    )

    now = base_time + timedelta(seconds=100)
    last_processed_candle_at = now - timedelta(seconds=5)

    assert _market_stale_age_seconds(
        candle_builder=builder,
        last_processed_candle_at=last_processed_candle_at,
        now=now,
    ) == 5.0
