import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from brokers.mt5.bridge_state import MT5BridgeState
from data.schemas import BridgeCommand, BridgeHealth, BridgeSnapshot, ExecutionAck, MT5AccountSnapshot, MT5Bar


def _snapshot() -> BridgeSnapshot:
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    bars_5m = [
        MT5Bar(
            timeframe="5m",
            start_at=base - timedelta(minutes=5),
            end_at=base,
            open_price=Decimal("1.1000"),
            high_price=Decimal("1.1005"),
            low_price=Decimal("1.0995"),
            close_price=Decimal("1.1002"),
        )
    ]
    return BridgeSnapshot(
        server_time=base + timedelta(seconds=10),
        symbol="EURUSD",
        bid=Decimal("1.1001"),
        ask=Decimal("1.1003"),
        spread_bps=1.8,
        bars_5m=bars_5m,
        bars_15m=[],
        bars_4h=[],
        account=MT5AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=BridgeHealth(),
    )


def test_bridge_state_queues_and_acks_commands() -> None:
    state = MT5BridgeState()
    now = datetime.now(timezone.utc)
    command = BridgeCommand(
        command_id="cmd-1",
        command_type="place_entry",
        symbol="EURUSD",
        created_at=now,
        expires_at=now + timedelta(minutes=1),
        side="long",
        volume_lots=Decimal("0.10"),
        stop_loss=Decimal("1.0990"),
        take_profit=Decimal("1.1015"),
        reason="test",
    )

    asyncio.run(state.queue_command(command))
    snapshot = asyncio.run(state.publish_snapshot(_snapshot()))
    polled = asyncio.run(state.poll_commands())

    assert snapshot.pending_command_ids == ["cmd-1"]
    assert polled[0].command_id == "cmd-1"

    asyncio.run(
        state.ack_command(
            ExecutionAck(command_id="cmd-1", status="filled", ticket_id="42", broker_time=snapshot.server_time)
        )
    )
    assert asyncio.run(state.has_pending_symbol("EURUSD")) is False
