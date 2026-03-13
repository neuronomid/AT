import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import websockets

from data.schemas import OrderSnapshot, TradeUpdate


class AlpacaTradingStreamError(RuntimeError):
    """Raised when the Alpaca trade update stream returns an invalid payload."""


class AlpacaTradingStreamService:
    def __init__(self, websocket_url: str, api_key: str, api_secret: str) -> None:
        self.websocket_url = websocket_url
        self.api_key = api_key
        self.api_secret = api_secret

    async def handshake(self) -> None:
        async with websockets.connect(self.websocket_url, ping_interval=20, ping_timeout=20) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.api_key,
                        "secret": self.api_secret,
                    }
                )
            )
            await self._expect_authorized(websocket)
            await websocket.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
            await self._expect_listening(websocket)

    async def read_order_update(self, order_id: str, timeout_seconds: float = 15.0) -> TradeUpdate:
        async with asyncio.timeout(timeout_seconds):
            async for update in self.stream_trade_updates():
                if update.order.id == order_id:
                    return update
        raise TimeoutError(f"No trade update received for order {order_id} within {timeout_seconds} seconds.")

    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdate]:
        async with websockets.connect(self.websocket_url, ping_interval=20, ping_timeout=20) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.api_key,
                        "secret": self.api_secret,
                    }
                )
            )
            await self._expect_authorized(websocket)
            await websocket.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
            await self._expect_listening(websocket)

            async for raw_message in websocket:
                payload = self._decode_message(raw_message)
                if payload.get("stream") != "trade_updates":
                    continue
                yield self._parse_trade_update(payload["data"])

    async def _expect_authorized(self, websocket: Any) -> None:
        payload = self._decode_message(await websocket.recv())
        if payload.get("stream") != "authorization" or payload.get("data", {}).get("status") != "authorized":
            raise AlpacaTradingStreamError(f"Unexpected authorization response: {payload!r}")

    async def _expect_listening(self, websocket: Any) -> None:
        payload = self._decode_message(await websocket.recv())
        streams = payload.get("data", {}).get("streams", [])
        if payload.get("stream") != "listening" or "trade_updates" not in streams:
            raise AlpacaTradingStreamError(f"Unexpected listen response: {payload!r}")

    def _decode_message(self, raw_message: str | bytes) -> dict[str, Any]:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        decoded = json.loads(raw_message)
        if not isinstance(decoded, dict):
            raise AlpacaTradingStreamError(f"Expected object payload, received {decoded!r}")
        return decoded

    def _parse_trade_update(self, payload: dict[str, Any]) -> TradeUpdate:
        return TradeUpdate(
            event=str(payload["event"]),
            order=self._parse_order(payload["order"]),
            timestamp=self._parse_datetime(payload.get("timestamp")),
            price=self._to_decimal(payload.get("price")),
            qty=self._to_decimal(payload.get("qty")),
        )

    def _parse_order(self, payload: dict[str, Any]) -> OrderSnapshot:
        return OrderSnapshot(
            id=str(payload["id"]),
            client_order_id=str(payload.get("client_order_id", "")),
            symbol=str(payload.get("symbol", "")),
            side=str(payload.get("side", "")),
            type=str(payload.get("order_type", payload.get("type", ""))),
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
