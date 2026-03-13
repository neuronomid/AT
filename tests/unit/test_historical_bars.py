from datetime import datetime, timezone
from decimal import Decimal

from brokers.alpaca.historical import AlpacaHistoricalCryptoService


def test_parse_historical_bar() -> None:
    service = AlpacaHistoricalCryptoService()
    bar = service._parse_bar(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        payload={
            "t": "2026-03-10T00:00:00Z",
            "o": 100.0,
            "h": 101.0,
            "l": 99.5,
            "c": 100.5,
            "v": 0,
            "n": 0,
            "vw": 100.2,
        },
    )

    assert bar.timestamp == datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
    assert bar.close_price == Decimal("100.5")
    snapshot = bar.to_market_snapshot()
    assert snapshot.last_trade_price == Decimal("100.5")
