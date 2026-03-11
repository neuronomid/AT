from decimal import Decimal

from brokers.alpaca.trading import AlpacaTradingService
from data.schemas import OrderRequest


class StubClient:
    async def post(self, path: str, payload: dict[str, object]) -> None:
        raise NotImplementedError(path)

    async def get(self, path: str, params: dict[str, object] | None = None) -> None:
        del params
        raise NotImplementedError(path)


def test_parse_order_snapshot() -> None:
    service = AlpacaTradingService(StubClient())
    order = service._parse_order(
        {
            "id": "order-123",
            "client_order_id": "client-123",
            "symbol": "ETH/USD",
            "side": "buy",
            "type": "market",
            "time_in_force": "gtc",
            "status": "new",
            "notional": "25",
            "filled_qty": "0.0",
        }
    )

    assert order.id == "order-123"
    assert order.notional == Decimal("25")


def test_order_request_serializes_without_none_fields() -> None:
    request = OrderRequest(symbol="ETH/USD", side="buy", notional=Decimal("25"))
    payload = request.model_dump(mode="json", exclude_none=True)

    assert payload["symbol"] == "ETH/USD"
    assert payload["notional"] == "25"
    assert "qty" not in payload
