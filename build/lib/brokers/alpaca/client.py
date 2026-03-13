from collections.abc import Mapping

import httpx

from app.config import Settings


class AlpacaClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=self._normalize_base_url(settings.alpaca_paper_base_url),
            headers=self._build_headers(),
            timeout=10.0,
        )

    def _build_headers(self) -> Mapping[str, str]:
        api_key = self._settings.alpaca_api_key.get_secret_value() if self._settings.alpaca_api_key else ""
        api_secret = (
            self._settings.alpaca_api_secret.get_secret_value() if self._settings.alpaca_api_secret else ""
        )
        return {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }

    async def get(self, path: str, params: Mapping[str, object] | None = None) -> httpx.Response:
        return await self._client.get(path, params=params)

    async def post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        return await self._client.post(path, json=payload)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _normalize_base_url(self, base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/v2"):
            return normalized[:-3]
        return normalized
