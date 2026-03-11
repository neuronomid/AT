from datetime import datetime, timezone
from decimal import Decimal

from brokers.alpaca.market_data import AlpacaMarketDataService
from data.schemas import MarketSnapshot


def test_market_data_quote_payload_updates_snapshot() -> None:
    service = AlpacaMarketDataService(
        websocket_url="wss://example.com",
        symbol="ETH/USD",
        api_key="key",
        api_secret="secret",
    )
    snapshot = MarketSnapshot(
        symbol="ETH/USD",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    updated = service._apply_payload(
        snapshot=snapshot,
        payload={
            "T": "q",
            "S": "ETH/USD",
            "t": "2026-01-01T00:00:05.000000Z",
            "bp": 3000.10,
            "bs": 1.25,
            "ap": 3000.20,
            "as": 0.75,
        },
    )

    assert updated is not None
    assert updated.bid_price == Decimal("3000.1")
    assert updated.ask_price == Decimal("3000.2")
    assert updated.bid_size == Decimal("1.25")
    assert updated.ask_size == Decimal("0.75")


def test_market_data_trade_payload_updates_last_trade_fields() -> None:
    service = AlpacaMarketDataService(
        websocket_url="wss://example.com",
        symbol="ETH/USD",
        api_key="key",
        api_secret="secret",
    )
    snapshot = MarketSnapshot(
        symbol="ETH/USD",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        bid_price=Decimal("3000.1"),
        ask_price=Decimal("3000.2"),
    )

    updated = service._apply_payload(
        snapshot=snapshot,
        payload={
            "T": "t",
            "S": "ETH/USD",
            "t": "2026-01-01T00:00:06.000000Z",
            "p": 3000.15,
            "s": 0.10,
        },
    )

    assert updated is not None
    assert updated.last_trade_price == Decimal("3000.15")
    assert updated.last_trade_size == Decimal("0.1")


def test_market_data_ignores_other_symbols() -> None:
    service = AlpacaMarketDataService(
        websocket_url="wss://example.com",
        symbol="ETH/USD",
        api_key="key",
        api_secret="secret",
    )
    snapshot = MarketSnapshot(
        symbol="ETH/USD",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    updated = service._apply_payload(
        snapshot=snapshot,
        payload={
            "T": "q",
            "S": "BTC/USD",
            "t": "2026-01-01T00:00:05.000000Z",
            "bp": 3000.10,
            "bs": 1.25,
            "ap": 3000.20,
            "as": 0.75,
        },
    )

    assert updated is None
