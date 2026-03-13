from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.schemas import MarketSnapshot
from runtime.candle_builder import CandleBuilder


def test_candle_builder_closes_a_minute_bucket() -> None:
    builder = CandleBuilder("ETH/USD")
    base_time = datetime(2026, 3, 12, 12, 0, 5, tzinfo=timezone.utc)

    first = builder.update(
        MarketSnapshot(
            symbol="ETH/USD",
            timestamp=base_time,
            event_type="trade",
            bid_price=Decimal("2999.9"),
            ask_price=Decimal("3000.1"),
            last_trade_price=Decimal("3000.0"),
            last_trade_size=Decimal("0.2"),
        )
    )
    assert first is None

    second = builder.update(
        MarketSnapshot(
            symbol="ETH/USD",
            timestamp=base_time + timedelta(seconds=30),
            event_type="trade",
            bid_price=Decimal("3000.9"),
            ask_price=Decimal("3001.1"),
            last_trade_price=Decimal("3001.0"),
            last_trade_size=Decimal("0.3"),
        )
    )
    assert second is None

    closed = builder.update(
        MarketSnapshot(
            symbol="ETH/USD",
            timestamp=base_time + timedelta(minutes=1),
            event_type="trade",
            bid_price=Decimal("3001.9"),
            ask_price=Decimal("3002.1"),
            last_trade_price=Decimal("3002.0"),
            last_trade_size=Decimal("0.4"),
        )
    )

    assert closed is not None
    assert closed.open_price == Decimal("3000.0")
    assert closed.close_price == Decimal("3001.0")
    assert closed.high_price == Decimal("3001.0")
    assert closed.low_price == Decimal("3000.0")
    assert closed.volume == Decimal("0.5")
    assert closed.trade_count == 2


def test_candle_builder_detects_staleness() -> None:
    builder = CandleBuilder("ETH/USD", stale_after_seconds=30)
    base_time = datetime(2026, 3, 12, 12, 0, 5, tzinfo=timezone.utc)
    builder.update(
        MarketSnapshot(
            symbol="ETH/USD",
            timestamp=base_time,
            event_type="trade",
            last_trade_price=Decimal("3000.0"),
            last_trade_size=Decimal("0.2"),
        )
    )

    assert builder.is_stale(now=base_time + timedelta(seconds=40)) is True


def test_candle_builder_flushes_carry_forward_candle_without_new_snapshot() -> None:
    builder = CandleBuilder("ETH/USD")
    base_time = datetime(2026, 3, 12, 12, 0, 5, tzinfo=timezone.utc)
    builder.update(
        MarketSnapshot(
            symbol="ETH/USD",
            timestamp=base_time,
            event_type="quote",
            bid_price=Decimal("3000.0"),
            ask_price=Decimal("3000.2"),
        )
    )

    closed = builder.flush(now=base_time + timedelta(minutes=1, seconds=5))

    assert len(closed) == 1
    assert closed[0].open_price == closed[0].close_price
    assert closed[0].trade_count == 0
    assert closed[0].volume == Decimal("0")
