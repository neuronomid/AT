import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

import websockets

from data.schemas import MarketSnapshot


class AlpacaMarketDataError(RuntimeError):
    """Raised when the Alpaca market data stream fails protocol validation."""


class AlpacaMarketDataService:
    """Streams live ETH/USD market snapshots from Alpaca's crypto feed."""

    def __init__(self, websocket_url: str, symbol: str, api_key: str, api_secret: str) -> None:
        self.websocket_url = websocket_url
        self.symbol = symbol
        self.api_key = api_key
        self.api_secret = api_secret

    async def connect(self) -> AsyncIterator[MarketSnapshot]:
        async for snapshot in self.stream_snapshots():
            yield snapshot

    async def read_one(self, timeout_seconds: float = 15.0) -> MarketSnapshot:
        stream = self.stream_snapshots()
        try:
            async with asyncio.timeout(timeout_seconds):
                return await anext(stream)
        finally:
            await stream.aclose()

    async def stream_snapshots(self) -> AsyncIterator[MarketSnapshot]:
        async with websockets.connect(self.websocket_url, ping_interval=20, ping_timeout=20) as websocket:
            await self._expect_success_message(websocket, "connected")
            await websocket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.api_key,
                        "secret": self.api_secret,
                    }
                )
            )
            await self._expect_success_message(websocket, "authenticated")
            await websocket.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "quotes": [self.symbol],
                        "trades": [self.symbol],
                    }
                )
            )
            await self._expect_subscription_message(websocket)

            snapshot = MarketSnapshot(symbol=self.symbol, timestamp=datetime.now().astimezone())
            async for raw_message in websocket:
                for payload in self._decode_message(raw_message):
                    next_snapshot = self._apply_payload(snapshot, payload)
                    if next_snapshot is None:
                        continue
                    snapshot = next_snapshot
                    yield snapshot.model_copy(deep=True)

    async def _expect_success_message(self, websocket: Any, expected_message: str) -> None:
        payloads = self._decode_message(await websocket.recv())
        if len(payloads) != 1:
            raise AlpacaMarketDataError(f"Expected a single control message, received {payloads!r}")
        payload = payloads[0]
        if payload.get("T") != "success" or payload.get("msg") != expected_message:
            raise AlpacaMarketDataError(f"Unexpected control message: {payload!r}")

    async def _expect_subscription_message(self, websocket: Any) -> None:
        payloads = self._decode_message(await websocket.recv())
        if len(payloads) != 1 or payloads[0].get("T") != "subscription":
            raise AlpacaMarketDataError(f"Unexpected subscription response: {payloads!r}")

    def _decode_message(self, raw_message: str) -> list[dict[str, Any]]:
        decoded = json.loads(raw_message)
        if not isinstance(decoded, list):
            raise AlpacaMarketDataError(f"Expected a list of payloads, received {decoded!r}")
        if not all(isinstance(payload, dict) for payload in decoded):
            raise AlpacaMarketDataError(f"Expected object payloads, received {decoded!r}")
        return decoded

    def _apply_payload(self, snapshot: MarketSnapshot, payload: dict[str, Any]) -> MarketSnapshot | None:
        message_type = payload.get("T")
        if payload.get("S") != self.symbol:
            return None

        if message_type == "q":
            return snapshot.model_copy(
                update={
                    "timestamp": self._parse_timestamp(payload["t"]),
                    "bid_price": self._to_decimal(payload.get("bp")),
                    "bid_size": self._to_decimal(payload.get("bs")),
                    "ask_price": self._to_decimal(payload.get("ap")),
                    "ask_size": self._to_decimal(payload.get("as")),
                }
            )

        if message_type == "t":
            return snapshot.model_copy(
                update={
                    "timestamp": self._parse_timestamp(payload["t"]),
                    "last_trade_price": self._to_decimal(payload.get("p")),
                    "last_trade_size": self._to_decimal(payload.get("s")),
                }
            )

        if message_type == "error":
            raise AlpacaMarketDataError(f"Alpaca market data error: {payload!r}")

        return None

    def _parse_timestamp(self, value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _to_decimal(self, value: object) -> Decimal | None:
        if value in (None, ""):
            return None
        return Decimal(str(value))
