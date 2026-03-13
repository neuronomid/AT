import asyncio
from decimal import Decimal

from brokers.alpaca.client import AlpacaClient
from data.schemas import AccountSnapshot


class AlpacaAccountService:
    _min_position_qty = Decimal("0.000001")

    def __init__(self, client: AlpacaClient) -> None:
        self._client = client

    async def fetch_account(self) -> dict[str, object]:
        response = await self._client.get("/v2/account")
        response.raise_for_status()
        return response.json()

    async def fetch_positions(self) -> list[dict[str, object]]:
        response = await self._client.get("/v2/positions")
        response.raise_for_status()
        return response.json()

    async def fetch_account_snapshot(self, symbol: str) -> AccountSnapshot:
        account, positions = await asyncio.gather(self.fetch_account(), self.fetch_positions())
        target_position = next((position for position in positions if self._symbols_match(position.get("symbol"), symbol)), None)
        open_position_qty = self._to_decimal((target_position or {}).get("qty"))
        avg_entry_price = self._to_decimal((target_position or {}).get("avg_entry_price"))
        market_value = self._to_decimal((target_position or {}).get("market_value"))
        unrealized_pl = self._to_decimal((target_position or {}).get("unrealized_pl"))
        if abs(open_position_qty) < self._min_position_qty:
            open_position_qty = Decimal("0")
            avg_entry_price = Decimal("0")
            market_value = Decimal("0")
            unrealized_pl = Decimal("0")

        return AccountSnapshot(
            equity=self._to_decimal(account.get("equity")),
            cash=self._to_decimal(account.get("cash")),
            buying_power=self._to_decimal(account.get("buying_power")),
            open_position_qty=open_position_qty,
            avg_entry_price=avg_entry_price,
            market_value=market_value,
            unrealized_pl=unrealized_pl,
            trading_blocked=bool(account.get("trading_blocked", False)),
            account_status=str(account.get("status", "")),
            crypto_status=str(account.get("crypto_status", "")),
        )

    def _symbols_match(self, candidate: object, target: str) -> bool:
        if not isinstance(candidate, str):
            return False
        return self._normalize_symbol(candidate) == self._normalize_symbol(target)

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "").replace("-", "").upper()

    def _to_decimal(self, value: object) -> Decimal:
        if value in (None, ""):
            return Decimal("0")
        return Decimal(str(value))
