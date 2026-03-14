from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from data.mt5_v51_schemas import (
    MT5V51BridgeCommand,
    MT5V51BridgeSnapshot,
    MT5V51ExecutionAck,
    MT5V51LiveTicket,
    MT5V51TicketRecord,
)
from memory.supabase_mt5_v51 import SupabaseMT5V51Store
from runtime.mt5_v51_symbols import normalize_mt5_v51_symbol


@dataclass
class MT5V51RegistrySyncResult:
    opened: list[MT5V51TicketRecord] = field(default_factory=list)
    closed: list[MT5V51TicketRecord] = field(default_factory=list)
    changed: list[MT5V51TicketRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Setup-quality-aware TP stages configuration.
# Each stage: (tp_r_threshold, close_fraction, sl_move_fraction_toward_entry)
#   - tp_r_threshold: the unrealized-R that triggers this stage
#   - close_fraction: fraction of *original* volume to close
#   - sl_move_fraction_toward_entry: 0.0 = no move, 0.3 = 30% closer, 1.0 = at entry
# ---------------------------------------------------------------------------
_SETUP_TP_STAGES: dict[str, list[tuple[float, float, float]]] = {
    "strong": [
        (0.3, 0.25, 0.30),   # TP1: 0.3R → close 25%, SL 30% closer to entry
        (0.5, 0.50, 1.00),   # TP2: 0.5R → close 50%, SL to entry (breakeven)
    ],
    "normal": [
        (0.3, 0.25, 0.30),   # TP1: 0.3R → close 25%, SL 30% closer to entry
    ],
    "weak": [
        (0.15, 0.25, 0.50),  # TP1: 0.15R → close 25%, SL 50% closer to entry
    ],
}


class MT5V51TicketRegistry:
    def __init__(
        self,
        store: SupabaseMT5V51Store | None = None,
        *,
        partial_target_r: Decimal = Decimal("0.5"),
        final_target_r: Decimal = Decimal("1.0"),
        post_partial_stop_lock_r: Decimal = Decimal("0.0"),
    ) -> None:
        self._store = store
        self._partial_target_r = partial_target_r
        self._final_target_r = final_target_r
        self._post_partial_stop_lock_r = post_partial_stop_lock_r
        self._records: dict[str, MT5V51TicketRecord] = {}
        self._pending_entries: dict[str, dict[str, Any]] = {}
        # Track the highest SL we have set per ticket so SL never moves backward
        self._best_stop_loss: dict[str, Decimal] = {}

    def seed(self, tickets: list[MT5V51TicketRecord]) -> None:
        self._records = {ticket.ticket_id: ticket for ticket in tickets if ticket.is_open}

    def register_pending_entry(
        self,
        *,
        command: MT5V51BridgeCommand,
        plan_payload: dict[str, Any],
    ) -> None:
        self._pending_entries[command.command_id] = {
            "command": command.model_dump(mode="json"),
            "plan": dict(plan_payload),
        }

    def record_ack(self, ack: MT5V51ExecutionAck) -> None:
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
        entry_stop_loss = command.get("stop_loss")
        entry_take_profit = command.get("take_profit")
        record = MT5V51TicketRecord(
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
            stop_loss=Decimal(str(entry_stop_loss)) if entry_stop_loss is not None else None,
            take_profit=Decimal(str(entry_take_profit)) if entry_take_profit is not None else None,
            initial_stop_loss=Decimal(str(plan["stop_loss"])),
            hard_take_profit=Decimal(str(plan["hard_take_profit"])),
            soft_take_profit_1=Decimal(str(plan["soft_take_profit_1"])),
            soft_take_profit_2=Decimal(str(plan["soft_take_profit_2"])),
            r_distance_price=Decimal(str(plan["r_distance_price"])),
            risk_amount_usd=Decimal(str(plan["risk_amount_usd"])),
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
            self._store.upsert_mt5_v51_ticket_state(record)
        self._pending_entries.pop(ack.command_id, None)

    def sync(self, snapshot: MT5V51BridgeSnapshot) -> MT5V51RegistrySyncResult:
        incoming = {ticket.ticket_id: ticket for ticket in snapshot.open_tickets}
        result = MT5V51RegistrySyncResult()

        for ticket_id, record in list(self._records.items()):
            if ticket_id not in incoming:
                closed = record.model_copy(update={"is_open": False, "last_seen_at": snapshot.server_time})
                result.closed.append(closed)
                if self._store is not None:
                    self._store.upsert_mt5_v51_ticket_state(closed)
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
                self._store.upsert_mt5_v51_ticket_state(updated)

        return result

    def all(self, symbol: str | None = None) -> list[MT5V51TicketRecord]:
        if symbol is None:
            return list(self._records.values())
        normalized = normalize_mt5_v51_symbol(symbol)
        return [ticket for ticket in self._records.values() if normalize_mt5_v51_symbol(ticket.symbol) == normalized]

    def by_ticket_id(self, ticket_id: str) -> MT5V51TicketRecord | None:
        return self._records.get(ticket_id)

    def has_open_position(self, symbol: str) -> bool:
        return bool(self.all(symbol))

    def total_open_risk_usd(self, symbol: str) -> Decimal:
        total = Decimal("0")
        for ticket in self.all(symbol):
            total += ticket.risk_amount_usd
        return total

    def allowed_actions(self, ticket_id: str) -> list[str]:
        ticket = self._records.get(ticket_id)
        if ticket is None:
            return ["hold"]
        return ["hold", "close_ticket"]

    def signature(self, symbol: str) -> str:
        parts = []
        for ticket in sorted(self.all(symbol), key=lambda item: item.ticket_id):
            parts.append(
                "|".join(
                    [
                        ticket.ticket_id,
                        ticket.side,
                        str(ticket.current_volume_lots),
                        str(ticket.stop_loss or ""),
                        str(ticket.take_profit or ""),
                        str(ticket.partial_stage),
                        f"{ticket.unrealized_r:.2f}",
                    ]
                )
            )
        return ";".join(parts)

    def quarter_r_buckets(self, symbol: str) -> dict[str, float]:
        return {ticket.ticket_id: ticket.quarter_r_bucket() for ticket in self.all(symbol)}

    def stop_target_for_action(self, *, ticket: MT5V51TicketRecord, snapshot: MT5V51BridgeSnapshot) -> Decimal | None:
        """Return the best-known SL for this ticket, or None if no action needed."""
        best = self._best_stop_loss.get(ticket.ticket_id)
        if best is not None:
            return best
        return None

    def setup_quality(self, ticket: MT5V51TicketRecord) -> str:
        """Retrieve the setup quality stored in the ticket metadata."""
        return str(ticket.metadata.get("setup_quality", "normal"))

    def tp_stages(self, ticket: MT5V51TicketRecord) -> list[tuple[float, float, float]]:
        """Return the TP stage definitions for this ticket's setup quality."""
        quality = self.setup_quality(ticket)
        return list(_SETUP_TP_STAGES.get(quality, _SETUP_TP_STAGES["normal"]))

    def next_tp_stage(self, ticket: MT5V51TicketRecord) -> tuple[float, float, float] | None:
        """Return the next unfired TP stage for this ticket, or None if all stages are complete."""
        stages = self.tp_stages(ticket)
        stage_index = ticket.partial_stage
        if stage_index >= len(stages):
            return None
        return stages[stage_index]

    def scalp_target_ready(self, ticket: MT5V51TicketRecord) -> bool:
        """True if the ticket's unrealized_r has reached the next TP stage threshold."""
        stage = self.next_tp_stage(ticket)
        if stage is None:
            return False
        tp_r_threshold, _, _ = stage
        return ticket.unrealized_r >= tp_r_threshold

    def scalp_partial_ready(self, ticket: MT5V51TicketRecord) -> bool:
        return self.scalp_target_ready(ticket)

    def scalp_final_ready(self, ticket: MT5V51TicketRecord) -> bool:
        """No more TP stages remain."""
        stages = self.tp_stages(ticket)
        return ticket.partial_stage >= len(stages)

    def partial_close_fraction(self, ticket: MT5V51TicketRecord) -> Decimal:
        """Fraction of original volume to close at the current stage."""
        stage = self.next_tp_stage(ticket)
        if stage is None:
            return Decimal("0")
        _, close_frac, _ = stage
        return Decimal(str(close_frac))

    def compute_new_stop_loss(
        self,
        ticket: MT5V51TicketRecord,
    ) -> Decimal | None:
        """Compute the new SL after the current TP stage fires.
        
        SL can only move closer to entry, never back away.
        Returns None if no SL change is needed.
        """
        stage = self.next_tp_stage(ticket)
        if stage is None:
            return None
        _, _, sl_move_fraction = stage
        if sl_move_fraction <= 0:
            return None

        entry_price = ticket.open_price
        initial_sl = ticket.initial_stop_loss
        sl_distance = abs(entry_price - initial_sl)

        if sl_move_fraction >= 1.0:
            # Move SL all the way to entry (breakeven)
            new_sl = entry_price
        else:
            # Move SL a fraction closer to entry
            move_amount = sl_distance * Decimal(str(sl_move_fraction))
            if ticket.side == "long":
                new_sl = initial_sl + move_amount
            else:
                new_sl = initial_sl - move_amount

        # Enforce monotonic tightening: SL can only get closer to entry
        current_best = self._best_stop_loss.get(ticket.ticket_id)
        if current_best is not None:
            if ticket.side == "long":
                new_sl = max(new_sl, current_best)
            else:
                new_sl = min(new_sl, current_best)

        # Also ensure the new SL is at least as favorable as the current broker SL
        if ticket.stop_loss is not None:
            if ticket.side == "long":
                new_sl = max(new_sl, ticket.stop_loss)
            else:
                new_sl = min(new_sl, ticket.stop_loss)

        return new_sl

    def record_tp_stage_fired(self, ticket: MT5V51TicketRecord, new_sl: Decimal | None) -> None:
        """Record that a TP stage has fired: advance the stage counter and lock the SL."""
        new_stage = ticket.partial_stage + 1
        ticket_update = {"partial_stage": new_stage}
        updated = ticket.model_copy(update=ticket_update)
        self._records[ticket.ticket_id] = updated

        if new_sl is not None:
            self._best_stop_loss[ticket.ticket_id] = new_sl

        if self._store is not None:
            self._store.upsert_mt5_v51_ticket_state(updated)

    def scalp_target_r(self, ticket: MT5V51TicketRecord) -> float:
        """Return the R threshold for the next TP stage."""
        stage = self.next_tp_stage(ticket)
        if stage is not None:
            tp_r_threshold, _, _ = stage
            return tp_r_threshold
        # All stages complete; return a large number so scalp_target_ready returns False
        return 999.0

    def _hydrate_record(self, *, live: MT5V51LiveTicket, snapshot: MT5V51BridgeSnapshot) -> MT5V51TicketRecord:
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
            payload = self._store.find_entry_command_payload(
                symbol=live.symbol,
                basket_id=live.basket_id,
                magic_number=live.magic_number,
            )
        return self._record_from_payload_or_live(
            live=live,
            snapshot=snapshot,
            payload=payload,
            entry_command_id=entry_command_id,
        )

    def _record_from_payload_or_live(
        self,
        *,
        live: MT5V51LiveTicket,
        snapshot: MT5V51BridgeSnapshot,
        payload: dict[str, Any] | None,
        entry_command_id: str | None,
    ) -> MT5V51TicketRecord:
        normalized_payload = self._normalize_plan_payload(plan=dict(payload or {}), fill_price=None) if payload else None
        metadata = self._payload_metadata(normalized_payload)
        entry_price = (
            Decimal(str((normalized_payload or {}).get("acked_fill_price", metadata.get("entry_price", live.open_price)))
            )
            if normalized_payload
            else live.open_price
        )
        desired_stop_loss = None
        if "initial_stop_loss" in metadata:
            desired_stop_loss = Decimal(str(metadata["initial_stop_loss"]))
        elif "stop_loss" in metadata:
            desired_stop_loss = Decimal(str(metadata["stop_loss"]))
        else:
            desired_stop_loss = live.stop_loss
        desired_hard_tp = None
        if "hard_take_profit" in metadata:
            desired_hard_tp = Decimal(str(metadata["hard_take_profit"]))
        elif "take_profit" in metadata:
            desired_hard_tp = Decimal(str(metadata["take_profit"]))
        else:
            desired_hard_tp = live.take_profit
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
            desired_hard_tp = self._default_soft_target(entry_price, desired_stop_loss, side=live.side, multiple=Decimal("1.0"))
        soft_tp1 = Decimal(
            str(
                metadata.get(
                    "soft_take_profit_1",
                    self._default_soft_target(entry_price, desired_stop_loss, side=live.side, multiple=Decimal("0.5")),
                )
            )
        )
        soft_tp2 = Decimal(
            str(
                metadata.get(
                    "soft_take_profit_2",
                    self._default_soft_target(entry_price, desired_stop_loss, side=live.side, multiple=Decimal("1.0")),
                )
            )
        )
        risk_amount_usd = Decimal(str(metadata.get("risk_amount_usd", self._fallback_risk_amount(live=live, snapshot=snapshot, r_distance=r_distance))))
        volume = Decimal(str((normalized_payload or {}).get("acked_fill_volume_lots", (normalized_payload or {}).get("volume_lots", live.volume_lots))))
        opened_at = (
            (normalized_payload or {}).get("acked_opened_at")
            if normalized_payload and (normalized_payload or {}).get("acked_opened_at") is not None
            else (live.opened_at or snapshot.server_time)
        )
        return MT5V51TicketRecord(
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
            soft_take_profit_1=soft_tp1,
            soft_take_profit_2=soft_tp2,
            r_distance_price=r_distance,
            risk_amount_usd=risk_amount_usd,
            highest_favorable_close=entry_price,
            lowest_favorable_close=entry_price,
            thesis_tags=list(metadata.get("thesis_tags", [])),
            context_signature=metadata.get("context_signature"),
            followed_lessons=list(metadata.get("followed_lessons", [])),
            metadata=metadata,
            opened_at=opened_at,
            last_seen_at=snapshot.server_time,
            unrealized_pnl_usd=live.unrealized_pnl_usd,
            unrealized_r=0.0,
        )

    def _payload_metadata(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        metadata = dict(payload)
        nested = metadata.pop("metadata", {})
        if isinstance(nested, dict):
            metadata.update(nested)
        return metadata

    def _update_record(
        self,
        *,
        record: MT5V51TicketRecord,
        live: MT5V51LiveTicket,
        snapshot: MT5V51BridgeSnapshot,
    ) -> MT5V51TicketRecord:
        current_price = live.current_price or live.open_price
        latest_close = snapshot.bars_1m[-1].close_price if snapshot.bars_1m else current_price
        highest_favorable = record.highest_favorable_close
        lowest_favorable = record.lowest_favorable_close
        if record.side == "long":
            highest_favorable = max(highest_favorable, latest_close)
        else:
            lowest_favorable = min(lowest_favorable, latest_close)
        unrealized_r = self._unrealized_r(record=record, current_price=current_price)
        inferred_stage = self._infer_partial_stage(record.original_volume_lots, live.volume_lots)
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
            }
        )

    def _infer_partial_stage(self, original_volume: Decimal, current_volume: Decimal) -> int:
        if original_volume <= 0:
            return 0
        closed_fraction = 1 - float(current_volume / original_volume)
        # Rough inference from volume: each ~25% closed = 1 stage
        if closed_fraction >= 0.65:
            return 2
        if closed_fraction >= 0.20:
            return 1
        return 0

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _unrealized_r(self, *, record: MT5V51TicketRecord, current_price: Decimal) -> float:
        if record.r_distance_price <= 0:
            return 0.0
        if record.side == "long":
            return float((current_price - record.open_price) / record.r_distance_price)
        return float((record.open_price - current_price) / record.r_distance_price)

    def _lock_price(self, ticket: MT5V51TicketRecord, locked_r: Decimal) -> Decimal:
        if ticket.side == "long":
            return ticket.open_price + (ticket.r_distance_price * locked_r)
        return ticket.open_price - (ticket.r_distance_price * locked_r)

    def _default_soft_target(self, entry_price: Decimal, stop_loss: Decimal, *, side: str, multiple: Decimal) -> Decimal:
        r_distance = abs(entry_price - stop_loss)
        if side == "long":
            return entry_price + (r_distance * multiple)
        return entry_price - (r_distance * multiple)

    def _fallback_risk_amount(self, *, live: MT5V51LiveTicket, snapshot: MT5V51BridgeSnapshot, r_distance: Decimal) -> Decimal:
        ticks = r_distance / snapshot.symbol_spec.tick_size
        return ticks * snapshot.symbol_spec.tick_value * live.volume_lots

    def _resolve_r_distance(
        self,
        *,
        live: MT5V51LiveTicket,
        snapshot: MT5V51BridgeSnapshot,
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

    def _normalize_plan_payload(
        self,
        *,
        plan: dict[str, Any],
        fill_price: Decimal | None,
    ) -> dict[str, Any]:
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
        if stop_loss is not None:
            metadata.setdefault("initial_stop_loss", float(Decimal(str(stop_loss))))
        hard_take_profit = normalized.get("hard_take_profit", normalized.get("take_profit"))
        if hard_take_profit is not None:
            metadata.setdefault("hard_take_profit", float(Decimal(str(hard_take_profit))))
        soft_take_profit_1 = normalized.get("soft_take_profit_1")
        if soft_take_profit_1 is not None:
            metadata.setdefault("soft_take_profit_1", float(Decimal(str(soft_take_profit_1))))
        soft_take_profit_2 = normalized.get("soft_take_profit_2")
        if soft_take_profit_2 is not None:
            metadata.setdefault("soft_take_profit_2", float(Decimal(str(soft_take_profit_2))))
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
