from datetime import datetime
from decimal import Decimal
from typing import Any

from brokers.alpaca.client import AlpacaClient
from data.schemas import OrderRequest, OrderSnapshot


class AlpacaTradingService:
    def __init__(self, client: AlpacaClient) -> None:
        self._client = client

    async def submit_order(self, order_request: OrderRequest) -> OrderSnapshot:
        response = await self._client.post("/v2/orders", order_request.model_dump(mode="json", exclude_none=True))
        response.raise_for_status()
        return self._parse_order(response.json())

    async def fetch_order(self, order_id: str) -> OrderSnapshot:
        response = await self._client.get(f"/v2/orders/{order_id}")
        response.raise_for_status()
        return self._parse_order(response.json())

    async def list_open_orders(self) -> list[OrderSnapshot]:
        response = await self._client.get("/v2/orders", params={"status": "open", "nested": "false"})
        response.raise_for_status()
        return [self._parse_order(payload) for payload in response.json()]

    def _parse_order(self, payload: dict[str, Any]) -> OrderSnapshot:
        return OrderSnapshot(
            id=str(payload["id"]),
            client_order_id=str(payload.get("client_order_id", "")),
            symbol=str(payload.get("symbol", "")),
            side=str(payload.get("side", "")),
            type=str(payload.get("type", "")),
            time_in_force=str(payload.get("time_in_force", "")),
            status=str(payload.get("status", "")),
            created_at=self._parse_datetime(payload.get("created_at")),
            updated_at=self._parse_datetime(payload.get("updated_at")),
            qty=self._to_decimal(payload.get("qty")),
            notional=self._to_decimal(payload.get("notional")),
            filled_qty=self._to_decimal(payload.get("filled_qty")),
            filled_avg_price=self._to_decimal(payload.get("filled_avg_price")),
        )

    def _parse_datetime(self, value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _to_decimal(self, value: object) -> Decimal | None:
        if value in (None, ""):
            return None
        return Decimal(str(value))
