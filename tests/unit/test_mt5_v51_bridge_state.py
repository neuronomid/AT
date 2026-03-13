import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from brokers.mt5_v51.bridge_state import MT5V51BridgeState
from data.mt5_v51_schemas import (
    MT5V51AccountSnapshot,
    MT5V51BridgeCommand,
    MT5V51BridgeHealth,
    MT5V51BridgeSnapshot,
    MT5V51ExecutionAck,
    MT5V51SymbolSpec,
)


def _snapshot() -> MT5V51BridgeSnapshot:
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    return MT5V51BridgeSnapshot(
        server_time=base + timedelta(seconds=10),
        received_at=base + timedelta(seconds=10),
        symbol="BTCUSD",
        bid=Decimal("60000"),
        ask=Decimal("60002"),
        spread_bps=0.3,
        symbol_spec=MT5V51SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.50"),
            tick_value=Decimal("1"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5"),
            stops_level_points=10,
        ),
        account=MT5V51AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V51BridgeHealth(),
    )


def test_mt5_v51_bridge_state_leases_polled_commands_until_ack() -> None:
    state = MT5V51BridgeState()
    now = datetime.now(timezone.utc)
    command = MT5V51BridgeCommand(
        command_id="cmd-1",
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

    asyncio.run(state.queue_command(command))
    before_poll = asyncio.run(state.publish_snapshot(_snapshot()))
    first_poll = asyncio.run(state.poll_commands())
    second_poll = asyncio.run(state.poll_commands())
    after_poll = asyncio.run(state.publish_snapshot(_snapshot()))

    assert before_poll.pending_command_ids == ["cmd-1"]
    assert [item.command_id for item in first_poll] == ["cmd-1"]
    assert second_poll == []
    assert after_poll.pending_command_ids == ["cmd-1"]
    assert asyncio.run(state.has_pending_symbol("BTCUSD")) is True

    asyncio.run(
        state.ack_command(
            MT5V51ExecutionAck(command_id="cmd-1", status="applied", ticket_id="42", broker_time=after_poll.server_time)
        )
    )

    after_ack = asyncio.run(state.publish_snapshot(_snapshot()))

    assert after_ack.pending_command_ids == []
    assert asyncio.run(state.has_pending_symbol("BTCUSD")) is False
