import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from brokers.mt5_v60.bridge_state import MT5V60BridgeState
from data.mt5_v60_schemas import (
    MT5V60AccountSnapshot,
    MT5V60BridgeCommand,
    MT5V60BridgeHealth,
    MT5V60BridgeSnapshot,
    MT5V60SymbolSpec,
)


def _snapshot(symbol: str) -> MT5V60BridgeSnapshot:
    base = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
    return MT5V60BridgeSnapshot(
        server_time=base + timedelta(seconds=10),
        received_at=base + timedelta(seconds=10),
        symbol=symbol,
        bid=Decimal("60000"),
        ask=Decimal("60002"),
        spread_bps=0.3,
        symbol_spec=MT5V60SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.50"),
            tick_value=Decimal("1"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5"),
            stops_level_points=10,
        ),
        account=MT5V60AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V60BridgeHealth(),
    )


def test_mt5_v60_bridge_state_polls_commands_by_symbol() -> None:
    state = MT5V60BridgeState()
    now = datetime.now(timezone.utc)
    eur_command = MT5V60BridgeCommand(
        command_id="cmd-eur",
        command_type="place_entry",
        symbol="EURUSD@",
        created_at=now,
        expires_at=now + timedelta(minutes=1),
        side="long",
        volume_lots=Decimal("0.10"),
        stop_loss=Decimal("1.0950"),
        take_profit=Decimal("1.1050"),
        reason="test",
    )
    btc_command = MT5V60BridgeCommand(
        command_id="cmd-btc",
        command_type="place_entry",
        symbol="BTCUSD",
        created_at=now,
        expires_at=now + timedelta(minutes=1),
        side="short",
        volume_lots=Decimal("0.10"),
        stop_loss=Decimal("60050"),
        take_profit=Decimal("59900"),
        reason="test",
    )

    asyncio.run(state.queue_command(eur_command))
    asyncio.run(state.queue_command(btc_command))

    first = asyncio.run(state.poll_commands(symbol="EURUSD@", limit=5))
    second = asyncio.run(state.poll_commands(symbol="BTCUSD", limit=5))

    assert [command.command_id for command in first] == ["cmd-eur"]
    assert [command.command_id for command in second] == ["cmd-btc"]


def test_mt5_v60_bridge_state_tracks_latest_snapshot_per_symbol() -> None:
    state = MT5V60BridgeState()

    asyncio.run(state.publish_snapshot(_snapshot("EURUSD@")))
    asyncio.run(state.publish_snapshot(_snapshot("BTCUSD")))

    eur_snapshot = asyncio.run(state.latest_snapshot("EURUSD@"))
    btc_snapshot = asyncio.run(state.latest_snapshot("BTCUSD"))
    snapshots = asyncio.run(state.latest_snapshots())

    assert eur_snapshot is not None
    assert btc_snapshot is not None
    assert eur_snapshot.symbol == "EURUSD@"
    assert btc_snapshot.symbol == "BTCUSD"
    assert set(snapshots) == {"EURUSD", "BTCUSD"}
