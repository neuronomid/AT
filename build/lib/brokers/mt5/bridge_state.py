from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone

from data.schemas import BridgeCommand, BridgeHealth, BridgeSnapshot, ExecutionAck


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MT5BridgeState:
    def __init__(self, bridge_id: str = "mt5-local", *, max_acks: int = 250) -> None:
        self._bridge_id = bridge_id
        self._lock = asyncio.Lock()
        self._snapshot_queue: asyncio.Queue[BridgeSnapshot] = asyncio.Queue()
        self._latest_snapshot: BridgeSnapshot | None = None
        self._pending_commands: OrderedDict[str, BridgeCommand] = OrderedDict()
        self._acks: deque[ExecutionAck] = deque(maxlen=max_acks)
        self._health = BridgeHealth(bridge_id=bridge_id)

    async def publish_snapshot(self, snapshot: BridgeSnapshot) -> BridgeSnapshot:
        received_at = datetime.now(timezone.utc)
        async with self._lock:
            normalized = snapshot.model_copy(
                update={
                    "bridge_id": self._bridge_id,
                    "received_at": received_at,
                    "pending_command_ids": list(self._pending_commands.keys()),
                    "health": self._health.model_copy(
                        update={
                            "connected": True,
                            "last_error": None,
                            "last_snapshot_at": received_at,
                            "pending_command_count": len(self._pending_commands),
                        }
                    ),
                }
            )
            self._latest_snapshot = normalized
            self._health = normalized.health
            self._snapshot_queue.put_nowait(normalized)
            return normalized

    async def wait_for_snapshot(self, timeout: float | None = None) -> BridgeSnapshot:
        if timeout is None:
            return await self._snapshot_queue.get()
        return await asyncio.wait_for(self._snapshot_queue.get(), timeout=timeout)

    async def latest_snapshot(self) -> BridgeSnapshot | None:
        async with self._lock:
            return self._latest_snapshot

    async def queue_command(self, command: BridgeCommand) -> None:
        async with self._lock:
            self._pending_commands[command.command_id] = command
            now = datetime.now(timezone.utc)
            self._health = self._health.model_copy(
                update={
                    "last_command_at": now,
                    "pending_command_count": len(self._pending_commands),
                }
            )

    async def poll_commands(self, limit: int = 10) -> list[BridgeCommand]:
        async with self._lock:
            now = datetime.now(timezone.utc)
            expired_ids = [
                command_id
                for command_id, command in self._pending_commands.items()
                if (expires_at := _ensure_utc(command.expires_at)) is not None and expires_at <= now
            ]
            for command_id in expired_ids:
                self._pending_commands.pop(command_id, None)
                self._acks.append(
                    ExecutionAck(
                        command_id=command_id,
                        status="expired",
                        broker_time=now,
                        message="Command expired before MT5 polled it.",
                    )
                )
            commands = list(self._pending_commands.values())[: max(1, limit)]
            self._health = self._health.model_copy(update={"pending_command_count": len(self._pending_commands)})
            return commands

    async def ack_command(self, ack: ExecutionAck) -> None:
        async with self._lock:
            self._acks.append(ack)
            if ack.status in {"rejected", "filled", "partial_fill", "applied", "expired", "ignored"}:
                self._pending_commands.pop(ack.command_id, None)
            self._health = self._health.model_copy(update={"pending_command_count": len(self._pending_commands)})

    async def recent_acks(self, limit: int = 25) -> list[ExecutionAck]:
        async with self._lock:
            return list(self._acks)[-max(1, limit) :]

    async def drain_acks(self) -> list[ExecutionAck]:
        async with self._lock:
            drained = list(self._acks)
            self._acks.clear()
            return drained

    async def has_pending_symbol(self, symbol: str) -> bool:
        normalized = symbol.strip().upper()
        async with self._lock:
            return any(command.symbol.strip().upper() == normalized for command in self._pending_commands.values())

    async def health(self) -> BridgeHealth:
        async with self._lock:
            return self._health
