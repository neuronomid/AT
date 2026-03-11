import pytest
from decimal import Decimal

from brokers.alpaca.account import AlpacaAccountService


class StubClient:
    async def get(self, path: str) -> None:
        raise NotImplementedError(path)


def test_account_service_normalizes_eth_usd_position() -> None:
    service = AlpacaAccountService(StubClient())

    snapshot = service._symbols_match("ETHUSD", "ETH/USD")
    assert snapshot is True


class FakeAccountService(AlpacaAccountService):
    async def fetch_account(self) -> dict[str, object]:
        return {
            "equity": "100000.12",
            "cash": "95000.10",
            "buying_power": "95000.10",
            "trading_blocked": False,
            "status": "ACTIVE",
            "crypto_status": "ACTIVE",
        }

    async def fetch_positions(self) -> list[dict[str, object]]:
        return [
            {
                "symbol": "ETHUSD",
                "qty": "0.2500",
            }
        ]


@pytest.mark.anyio
async def test_account_service_builds_snapshot_from_raw_payloads() -> None:
    service = FakeAccountService(StubClient())
    snapshot = await service.fetch_account_snapshot("ETH/USD")

    assert snapshot.equity == Decimal("100000.12")
    assert snapshot.cash == Decimal("95000.10")
    assert snapshot.buying_power == Decimal("95000.10")
    assert snapshot.open_position_qty == Decimal("0.2500")
    assert snapshot.crypto_status == "ACTIVE"


def test_account_service_converts_missing_values_to_zero() -> None:
    service = AlpacaAccountService(StubClient())
    assert service._to_decimal(None) == Decimal("0")
