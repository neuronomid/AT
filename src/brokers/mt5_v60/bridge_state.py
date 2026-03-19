from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone

from data.mt5_v60_schemas import (
    MT5V60BridgeCommand,
    MT5V60BridgeHealth,
    MT5V60BridgeSnapshot,
    MT5V60ExecutionAck,
)
from runtime.mt5_v60_symbols import normalize_mt5_v60_symbol


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MT5V60BridgeState:
    def __init__(self, bridge_id: str = "mt5-v60-local", *, max_acks: int = 250) -> None:
        self._bridge_id = bridge_id
        self._lock = asyncio.Lock()
        self._snapshot_queue: asyncio.Queue[MT5V60BridgeSnapshot] = asyncio.Queue()
        self._latest_snapshot: MT5V60BridgeSnapshot | None = None
        self._pending_commands: OrderedDict[str, MT5V60BridgeCommand] = OrderedDict()
        self._inflight_commands: OrderedDict[str, MT5V60BridgeCommand] = OrderedDict()
        self._acks: deque[MT5V60ExecutionAck] = deque(maxlen=max_acks)
        self._health = MT5V60BridgeHealth(bridge_id=bridge_id)

    async def publish_snapshot(self, snapshot: MT5V60BridgeSnapshot) -> MT5V60BridgeSnapshot:
        received_at = datetime.now(timezone.utc)
        async with self._lock:
            normalized = snapshot.model_copy(
                update={
                    "bridge_id": self._bridge_id,
                    "received_at": received_at,
                    "pending_command_ids": self._pending_command_ids(),
                    "health": self._health.model_copy(
                        update={
                            "connected": True,
                            "last_error": None,
                            "last_snapshot_at": received_at,
                            "pending_command_count": self._pending_command_count(),
                        }
                    ),
                }
            )
            self._latest_snapshot = normalized
            self._health = normalized.health
            self._snapshot_queue.put_nowait(normalized)
            return normalized

    async def wait_for_snapshot(self, timeout: float | None = None) -> MT5V60BridgeSnapshot:
        if timeout is None:
            return await self._snapshot_queue.get()
        return await asyncio.wait_for(self._snapshot_queue.get(), timeout=timeout)

    async def latest_snapshot(self) -> MT5V60BridgeSnapshot | None:
        async with self._lock:
            return self._latest_snapshot

    async def queue_command(self, command: MT5V60BridgeCommand) -> None:
        async with self._lock:
            self._pending_commands[command.command_id] = command
            now = datetime.now(timezone.utc)
            self._health = self._health.model_copy(update={"last_command_at": now, "pending_command_count": self._pending_command_count()})

    async def poll_commands(self, limit: int = 10) -> list[MT5V60BridgeCommand]:
        async with self._lock:
            now = datetime.now(timezone.utc)
            expired_ids = [
                command_id
                for commands in (self._pending_commands, self._inflight_commands)
                for command_id, command in commands.items()
                if (expires_at := _ensure_utc(command.expires_at)) is not None and expires_at <= now
            ]
            for command_id in expired_ids:
                self._pending_commands.pop(command_id, None)
                self._inflight_commands.pop(command_id, None)
                self._acks.append(
                    MT5V60ExecutionAck(
                        command_id=command_id,
                        status="expired",
                        broker_time=now,
                        message="Command expired before MT5 polled it.",
                    )
                )
            commands = list(self._pending_commands.values())[: max(1, limit)]
            for command in commands:
                self._pending_commands.pop(command.command_id, None)
                self._inflight_commands[command.command_id] = command
            self._health = self._health.model_copy(update={"pending_command_count": self._pending_command_count()})
            return commands

    async def ack_command(self, ack: MT5V60ExecutionAck) -> None:
        async with self._lock:
            self._acks.append(ack)
            if ack.status in {"rejected", "filled", "partial_fill", "applied", "expired", "ignored"}:
                self._pending_commands.pop(ack.command_id, None)
                self._inflight_commands.pop(ack.command_id, None)
            self._health = self._health.model_copy(update={"pending_command_count": self._pending_command_count()})

    async def drain_acks(self) -> list[MT5V60ExecutionAck]:
        async with self._lock:
            drained = list(self._acks)
            self._acks.clear()
            return drained

    async def has_pending_symbol(self, symbol: str) -> bool:
        normalized = normalize_mt5_v60_symbol(symbol)
        async with self._lock:
            return any(
                normalize_mt5_v60_symbol(command.symbol) == normalized
                for command in [*self._pending_commands.values(), *self._inflight_commands.values()]
            )

    async def health(self) -> MT5V60BridgeHealth:
        async with self._lock:
            return self._health

    def _pending_command_ids(self) -> list[str]:
        return [*self._pending_commands.keys(), *self._inflight_commands.keys()]

    def _pending_command_count(self) -> int:
        return len(self._pending_commands) + len(self._inflight_commands)
