from decimal import Decimal

from brokers.alpaca.trading_stream import AlpacaTradingStreamService


def test_decode_bytes_message() -> None:
    service = AlpacaTradingStreamService("wss://example.com", "key", "secret")
    decoded = service._decode_message(b'{"stream":"authorization","data":{"status":"authorized"}}')

    assert decoded["stream"] == "authorization"


def test_parse_trade_update() -> None:
    service = AlpacaTradingStreamService("wss://example.com", "key", "secret")
    update = service._parse_trade_update(
        {
            "event": "fill",
            "timestamp": "2026-01-01T00:00:06.000000Z",
            "price": "2000.50",
            "qty": "0.01",
            "order": {
                "id": "order-1",
                "client_order_id": "client-1",
                "symbol": "ETH/USD",
                "side": "buy",
                "order_type": "market",
                "time_in_force": "gtc",
                "status": "filled",
                "filled_qty": "0.01",
                "filled_avg_price": "2000.50",
            },
        }
    )

    assert update.event == "fill"
    assert update.price == Decimal("2000.50")
    assert update.order.filled_qty == Decimal("0.01")
