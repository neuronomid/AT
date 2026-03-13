from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from data.schemas import HistoricalBar


class AlpacaHistoricalCryptoService:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = "https://data.alpaca.markets",
    ) -> None:
        headers: dict[str, str] = {}
        if api_key and api_secret:
            headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            }
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0)

    async def fetch_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        location: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[HistoricalBar]:
        bars: list[HistoricalBar] = []
        page_token: str | None = None
        params: dict[str, object] = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "limit": limit,
            "sort": "asc",
        }

        while True:
            if page_token is not None:
                params["page_token"] = page_token
            response = await self._client.get(f"/v1beta3/crypto/{location}/bars", params=params)
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("bars", {}).get(symbol, []):
                bars.append(self._parse_bar(symbol=symbol, timeframe=timeframe, location=location, payload=item))
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return bars

    async def aclose(self) -> None:
        await self._client.aclose()

    def _parse_bar(self, *, symbol: str, timeframe: str, location: str, payload: dict[str, Any]) -> HistoricalBar:
        return HistoricalBar(
            symbol=symbol,
            timeframe=timeframe,
            location=location,
            timestamp=datetime.fromisoformat(str(payload["t"]).replace("Z", "+00:00")),
            open_price=Decimal(str(payload["o"])),
            high_price=Decimal(str(payload["h"])),
            low_price=Decimal(str(payload["l"])),
            close_price=Decimal(str(payload["c"])),
            volume=Decimal(str(payload.get("v", 0))),
            trade_count=int(payload.get("n", 0)),
            vwap=Decimal(str(payload["vw"])) if payload.get("vw") is not None else None,
            raw_bar=payload,
        )
