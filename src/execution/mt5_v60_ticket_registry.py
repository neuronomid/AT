from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from data.mt5_v60_schemas import (
    MT5V60BridgeCommand,
    MT5V60BridgeSnapshot,
    MT5V60CloseEvent,
    MT5V60ExecutionAck,
    MT5V60LiveTicket,
    MT5V60TicketRecord,
)
from memory.supabase_mt5_v60 import SupabaseMT5V60Store
from runtime.mt5_v60_symbols import normalize_mt5_v60_symbol


@dataclass
class MT5V60RegistrySyncResult:
    opened: list[MT5V60TicketRecord] = field(default_factory=list)
    closed: list[MT5V60TicketRecord] = field(default_factory=list)
    changed: list[MT5V60TicketRecord] = field(default_factory=list)


class MT5V60TicketRegistry:
    def __init__(self, store: SupabaseMT5V60Store | None = None) -> None:
        self._store = store
        self._records: dict[str, MT5V60TicketRecord] = {}
        self._pending_entries: dict[str, dict[str, Any]] = {}

    def seed(self, tickets: list[MT5V60TicketRecord]) -> None:
        self._records = {ticket.ticket_id: ticket for ticket in tickets if ticket.is_open}

    def register_pending_entry(self, *, command: MT5V60BridgeCommand, plan_payload: dict[str, Any]) -> None:
        self._pending_entries[command.command_id] = {
            "command": command.model_dump(mode="json"),
            "plan": dict(plan_payload),
        }

    def record_ack(self, ack: MT5V60ExecutionAck) -> None:
        pending = self._pending_entries.get(ack.command_id)
        if pending is None:
            return
        if ack.status in {"rejected", "expired", "ignored"}:
            self._pending_entries.pop(ack.command_id, None)
            return
        if not self._has_live_ticket_id(ack.ticket_id):
            plan = pending["plan"]
            if ack.fill_price is not None:
                normalized = self._normalize_plan_payload(plan=plan, fill_price=Decimal(str(ack.fill_price)))
                plan.clear()
                plan.update(normalized)
            if ack.fill_volume_lots is not None:
                plan["acked_fill_volume_lots"] = ack.fill_volume_lots
            if ack.broker_time is not None:
                plan["acked_opened_at"] = ack.broker_time
            return
        fill_price = Decimal(str(ack.fill_price or pending["plan"]["entry_price"]))
        plan = self._normalize_plan_payload(plan=dict(pending["plan"]), fill_price=fill_price)
        command = dict(pending["command"])
        opened_at = ack.broker_time or datetime.now(timezone.utc)
        record = MT5V60TicketRecord(
            ticket_id=ack.ticket_id,
            symbol=str(plan["symbol"]),
            side=str(plan["side"]),
            basket_id=str(plan["basket_id"]),
            entry_command_id=ack.command_id,
            magic_number=int(plan["magic_number"]),
            original_volume_lots=Decimal(str(plan["volume_lots"])),
            current_volume_lots=Decimal(str(ack.fill_volume_lots or plan["volume_lots"])),
            open_price=fill_price,
            current_price=fill_price,
            stop_loss=Decimal(str(command["stop_loss"])) if command.get("stop_loss") is not None else None,
            take_profit=Decimal(str(command["take_profit"])) if command.get("take_profit") is not None else None,
            initial_stop_loss=Decimal(str(plan["stop_loss"])),
            hard_take_profit=Decimal(str(plan["take_profit"])),
            r_distance_price=Decimal(str(plan["r_distance_price"])),
            risk_amount_usd=Decimal(str(plan["risk_amount_usd"])),
            analysis_mode=str(plan.get("analysis_mode", "standard_entry")),
            highest_favorable_close=fill_price,
            lowest_favorable_close=fill_price,
            thesis_tags=list(plan.get("thesis_tags", [])),
            context_signature=plan.get("context_signature"),
            followed_lessons=list(plan.get("followed_lessons", [])),
            metadata=self._payload_metadata(plan),
            opened_at=opened_at,
            last_seen_at=opened_at,
            unrealized_pnl_usd=Decimal("0"),
            unrealized_r=0.0,
        )
        self._records[record.ticket_id] = record
        if self._store is not None:
            self._store.upsert_mt5_v60_ticket_state(record)
        self._pending_entries.pop(ack.command_id, None)

    def sync(self, snapshot: MT5V60BridgeSnapshot) -> MT5V60RegistrySyncResult:
        incoming = {ticket.ticket_id: ticket for ticket in snapshot.open_tickets}
        result = MT5V60RegistrySyncResult()
        for ticket_id, record in list(self._records.items()):
            if ticket_id not in incoming:
                close_event = self._matching_close_event(snapshot=snapshot, record=record)
                closed = record.model_copy(
                    update={
                        "is_open": False,
                        "last_seen_at": snapshot.server_time,
                        "current_price": (
                            close_event.exit_price if close_event is not None and close_event.exit_price is not None else record.current_price
                        ),
                        "unrealized_pnl_usd": (
                            close_event.realized_pnl_usd
                            if close_event is not None and close_event.realized_pnl_usd is not None
                            else record.unrealized_pnl_usd
                        ),
                        "last_close_reason": (close_event.close_reason if close_event is not None else "unknown"),
                    }
                )
                result.closed.append(closed)
                if self._store is not None:
                    self._store.upsert_mt5_v60_ticket_state(closed)
                self._records.pop(ticket_id, None)
        for live in incoming.values():
            previous = self._records.get(live.ticket_id)
            record = previous or self._hydrate_record(live=live, snapshot=snapshot)
            updated = self._update_record(record=record, live=live, snapshot=snapshot)
            if previous is None:
                result.opened.append(updated)
            elif previous.model_dump(mode="json") != updated.model_dump(mode="json"):
                result.changed.append(updated)
            self._records[live.ticket_id] = updated
            if self._store is not None:
                self._store.upsert_mt5_v60_ticket_state(updated)
        return result

    def all(self, symbol: str | None = None) -> list[MT5V60TicketRecord]:
        if symbol is None:
            return list(self._records.values())
        normalized = normalize_mt5_v60_symbol(symbol)
        return [ticket for ticket in self._records.values() if normalize_mt5_v60_symbol(ticket.symbol) == normalized]

    def by_ticket_id(self, ticket_id: str) -> MT5V60TicketRecord | None:
        return self._records.get(ticket_id)

    def has_open_position(self, symbol: str) -> bool:
        return bool(self.all(symbol))

    def allowed_actions(self, ticket_id: str) -> list[str]:
        ticket = self._records.get(ticket_id)
        if ticket is None:
            return ["hold"]
        return ["hold", "modify_ticket", "close_partial", "close_ticket"]

    def record_first_protection_review(
        self,
        ticket_id: str,
        *,
        outcome: str,
        reviewed_at: datetime,
    ) -> None:
        ticket = self._records.get(ticket_id)
        if ticket is None or not ticket.first_protection_review_pending:
            return
        metadata = dict(ticket.metadata)
        metadata["first_protection_review_outcome"] = outcome
        metadata["first_protection_reviewed_at"] = reviewed_at.isoformat()
        updated = ticket.model_copy(
            update={
                "metadata": metadata,
                "first_protection_review_pending": False,
            }
        )
        self._records[ticket_id] = updated
        if self._store is not None:
            self._store.upsert_mt5_v60_ticket_state(updated)

    def _hydrate_record(self, *, live: MT5V60LiveTicket, snapshot: MT5V60BridgeSnapshot) -> MT5V60TicketRecord:
        matched_pending = None
        for command_id, pending in list(self._pending_entries.items()):
            command = pending["command"]
            if live.basket_id and command.get("basket_id") == live.basket_id:
                matched_pending = (command_id, pending)
                break
            if live.magic_number is not None and command.get("magic_number") == live.magic_number:
                matched_pending = (command_id, pending)
                break
        payload = None
        entry_command_id = None
        if matched_pending is not None:
            entry_command_id, pending = matched_pending
            payload = dict(pending["plan"])
            self._pending_entries.pop(entry_command_id, None)
        elif self._store is not None:
            payload = self._store.find_entry_command_payload(symbol=live.symbol, basket_id=live.basket_id, magic_number=live.magic_number)
        return self._record_from_payload_or_live(live=live, snapshot=snapshot, payload=payload, entry_command_id=entry_command_id)

    def _record_from_payload_or_live(
        self,
        *,
        live: MT5V60LiveTicket,
        snapshot: MT5V60BridgeSnapshot,
        payload: dict[str, Any] | None,
        entry_command_id: str | None,
    ) -> MT5V60TicketRecord:
        normalized_payload = self._normalize_plan_payload(plan=dict(payload or {}), fill_price=None) if payload else None
        metadata = self._payload_metadata(normalized_payload)
        entry_price = (
            Decimal(str((normalized_payload or {}).get("acked_fill_price", metadata.get("entry_price", live.open_price))))
            if normalized_payload
            else live.open_price
        )
        desired_stop_loss = (
            Decimal(str(metadata.get("initial_stop_loss", metadata.get("stop_loss", live.stop_loss or "0"))))
            if (metadata.get("initial_stop_loss") is not None or metadata.get("stop_loss") is not None or live.stop_loss is not None)
            else None
        )
        desired_hard_tp = (
            Decimal(str(metadata.get("hard_take_profit", metadata.get("take_profit", live.take_profit or "0"))))
            if (metadata.get("hard_take_profit") is not None or metadata.get("take_profit") is not None or live.take_profit is not None)
            else None
        )
        r_distance = self._resolve_r_distance(
            live=live,
            snapshot=snapshot,
            entry_price=entry_price,
            stop_loss=desired_stop_loss,
            metadata=metadata,
        )
        if desired_stop_loss is None:
            desired_stop_loss = self._default_stop_from_r(entry_price=entry_price, r_distance=r_distance, side=live.side)
        if desired_hard_tp is None:
            desired_hard_tp = self._default_take_profit(entry_price=entry_price, stop_loss=desired_stop_loss, side=live.side)
        risk_amount_usd = Decimal(str(metadata.get("risk_amount_usd", self._fallback_risk_amount(live=live, snapshot=snapshot, r_distance=r_distance))))
        volume = Decimal(str((normalized_payload or {}).get("acked_fill_volume_lots", (normalized_payload or {}).get("volume_lots", live.volume_lots))))
        opened_at = (
            (normalized_payload or {}).get("acked_opened_at")
            if normalized_payload and (normalized_payload or {}).get("acked_opened_at") is not None
            else (live.opened_at or snapshot.server_time)
        )
        return MT5V60TicketRecord(
            ticket_id=live.ticket_id,
            symbol=live.symbol,
            side=live.side,
            basket_id=live.basket_id,
            entry_command_id=entry_command_id,
            magic_number=live.magic_number,
            original_volume_lots=volume,
            current_volume_lots=live.volume_lots,
            open_price=entry_price,
            current_price=live.current_price or live.open_price,
            stop_loss=live.stop_loss,
            take_profit=live.take_profit,
            initial_stop_loss=desired_stop_loss,
            hard_take_profit=desired_hard_tp,
            r_distance_price=r_distance,
            risk_amount_usd=risk_amount_usd,
            analysis_mode=str(metadata.get("analysis_mode", "standard_entry")),
            partial_stage=int(metadata.get("partial_stage", 0)),
            highest_favorable_close=entry_price,
            lowest_favorable_close=entry_price,
            thesis_tags=list(metadata.get("thesis_tags", [])),
            context_signature=metadata.get("context_signature"),
            followed_lessons=list(metadata.get("followed_lessons", [])),
            metadata=metadata,
            opened_at=opened_at,
            last_seen_at=snapshot.server_time,
            first_protection_attached=bool(metadata.get("first_protection_attached", False)),
            first_protection_review_pending=bool(metadata.get("first_protection_review_pending", False)),
            unrealized_pnl_usd=live.unrealized_pnl_usd,
            unrealized_r=0.0,
        )

    def _update_record(
        self,
        *,
        record: MT5V60TicketRecord,
        live: MT5V60LiveTicket,
        snapshot: MT5V60BridgeSnapshot,
    ) -> MT5V60TicketRecord:
        current_price = live.current_price or live.open_price
        latest_close = snapshot.bars_3m[-1].close_price if snapshot.bars_3m else current_price
        highest_favorable = record.highest_favorable_close
        lowest_favorable = record.lowest_favorable_close
        if record.side == "long":
            highest_favorable = max(highest_favorable, latest_close)
        else:
            lowest_favorable = min(lowest_favorable, latest_close)
        unrealized_r = self._unrealized_r(record=record, current_price=current_price)
        inferred_stage = self._infer_partial_stage(record.original_volume_lots, live.volume_lots)
        first_protection_attached = record.first_protection_attached
        first_protection_review_pending = record.first_protection_review_pending
        if (
            not first_protection_attached
            and bool(record.metadata.get("entry_submitted_without_broker_protection"))
            and (live.stop_loss is not None or live.take_profit is not None)
        ):
            first_protection_attached = True
            first_protection_review_pending = True
        return record.model_copy(
            update={
                "current_volume_lots": live.volume_lots,
                "current_price": current_price,
                "stop_loss": live.stop_loss,
                "take_profit": live.take_profit,
                "last_seen_at": snapshot.server_time,
                "is_open": True,
                "unrealized_pnl_usd": live.unrealized_pnl_usd,
                "unrealized_r": unrealized_r,
                "partial_stage": max(record.partial_stage, inferred_stage),
                "highest_favorable_close": highest_favorable,
                "lowest_favorable_close": lowest_favorable,
                "first_protection_attached": first_protection_attached,
                "first_protection_review_pending": first_protection_review_pending,
            }
        )

    def _matching_close_event(self, *, snapshot: MT5V60BridgeSnapshot, record: MT5V60TicketRecord) -> MT5V60CloseEvent | None:
        for event in reversed(snapshot.recent_close_events):
            if event.ticket_id and event.ticket_id == record.ticket_id:
                return event
            if record.basket_id and event.basket_id and event.basket_id == record.basket_id:
                return event
        return None

    def _payload_metadata(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        metadata = dict(payload)
        nested = metadata.pop("metadata", {})
        if isinstance(nested, dict):
            metadata.update(nested)
        return metadata

    def _unrealized_r(self, *, record: MT5V60TicketRecord, current_price: Decimal) -> float:
        if record.r_distance_price <= 0:
            return 0.0
        if record.side == "long":
            return float((current_price - record.open_price) / record.r_distance_price)
        return float((record.open_price - current_price) / record.r_distance_price)

    def _infer_partial_stage(self, original_volume: Decimal, current_volume: Decimal) -> int:
        if original_volume <= 0:
            return 0
        closed_fraction = 1 - float(current_volume / original_volume)
        if closed_fraction >= 0.65:
            return 2
        if closed_fraction >= 0.20:
            return 1
        return 0

    def _default_take_profit(self, *, entry_price: Decimal, stop_loss: Decimal, side: str) -> Decimal:
        r_distance = abs(entry_price - stop_loss)
        if side == "long":
            return entry_price + r_distance
        return entry_price - r_distance

    def _fallback_risk_amount(self, *, live: MT5V60LiveTicket, snapshot: MT5V60BridgeSnapshot, r_distance: Decimal) -> Decimal:
        ticks = r_distance / snapshot.symbol_spec.tick_size
        return ticks * snapshot.symbol_spec.tick_value * live.volume_lots

    def _resolve_r_distance(
        self,
        *,
        live: MT5V60LiveTicket,
        snapshot: MT5V60BridgeSnapshot,
        entry_price: Decimal,
        stop_loss: Decimal | None,
        metadata: dict[str, Any],
    ) -> Decimal:
        if "r_distance_price" in metadata:
            r_distance = Decimal(str(metadata["r_distance_price"]))
            if r_distance > 0:
                return r_distance
        if stop_loss is not None:
            r_distance = abs(entry_price - stop_loss)
            if r_distance > 0:
                return r_distance
        fallback = max(
            snapshot.symbol_spec.min_stop_distance_price,
            snapshot.symbol_spec.tick_size * Decimal("100"),
            entry_price * Decimal("0.0007"),
        )
        ticks = (fallback / snapshot.symbol_spec.tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return ticks * snapshot.symbol_spec.tick_size

    def _default_stop_from_r(self, *, entry_price: Decimal, r_distance: Decimal, side: str) -> Decimal:
        if side == "long":
            return entry_price - r_distance
        return entry_price + r_distance

    def _normalize_plan_payload(self, *, plan: dict[str, Any], fill_price: Decimal | None) -> dict[str, Any]:
        if not plan:
            return {}
        normalized = dict(plan)
        if fill_price is None and normalized.get("acked_fill_price") is not None:
            fill_price = Decimal(str(normalized["acked_fill_price"]))
        if fill_price is None:
            return normalized
        stop_loss = normalized.get("stop_loss", normalized.get("initial_stop_loss"))
        if stop_loss is None and isinstance(normalized.get("metadata"), dict):
            stop_loss = normalized["metadata"].get("initial_stop_loss", normalized["metadata"].get("stop_loss"))
        if stop_loss is not None:
            r_distance = abs(fill_price - Decimal(str(stop_loss)))
        else:
            r_distance = Decimal(str(normalized.get("r_distance_price", "0")))
        if r_distance <= 0:
            return normalized
        normalized.update(
            {
                "entry_price": fill_price,
                "acked_fill_price": fill_price,
                "r_distance_price": r_distance,
                "normalized_after_fill": True,
            }
        )
        metadata = dict(normalized.get("metadata", {})) if isinstance(normalized.get("metadata"), dict) else {}
        metadata.update(
            {
                "entry_price": float(fill_price),
                "r_distance_price": float(r_distance),
                "normalized_after_fill": True,
            }
        )
        normalized["metadata"] = metadata
        return normalized

    def _has_live_ticket_id(self, ticket_id: str | None) -> bool:
        if ticket_id is None:
            return False
        stripped = ticket_id.strip()
        if not stripped:
            return False
        if stripped.isdigit():
            return int(stripped) > 0
        return stripped != "0"
