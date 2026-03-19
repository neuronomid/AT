from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR
from hashlib import blake2s

from data.mt5_v60_schemas import (
    MT5V60BridgeCommand,
    MT5V60BridgeSnapshot,
    MT5V60EntryDecision,
    MT5V60EntryPlan,
    MT5V60RiskDecision,
    MT5V60TicketRecord,
)


class MT5V60EntryPlanner:
    def __init__(
        self,
        *,
        broker_stop_buffer_ticks: Decimal = Decimal("0"),
    ) -> None:
        self._broker_stop_buffer_ticks = broker_stop_buffer_ticks

    def plan_entry(
        self,
        *,
        decision: MT5V60EntryDecision,
        snapshot: MT5V60BridgeSnapshot,
        risk_decision: MT5V60RiskDecision,
        analysis_mode: str = "standard_entry",
        ticket_sequence: int = 1,
    ) -> MT5V60EntryPlan | None:
        if not risk_decision.approved or risk_decision.risk_fraction is None:
            return None
        if decision.stop_loss_price is None or decision.take_profit_price is None:
            return None
        side = "long" if decision.action == "enter_long" else "short"
        entry_price = snapshot.ask if side == "long" else snapshot.bid
        levels = self.validate_entry_levels(
            side=side,
            entry_price=entry_price,
            snapshot=snapshot,
            stop_loss=decision.stop_loss_price,
            take_profit=decision.take_profit_price,
        )
        if levels is None:
            return None
        stop_loss, take_profit, r_distance = levels
        account_base = snapshot.account.balance if snapshot.account.balance > 0 else snapshot.account.equity
        risk_amount_usd = account_base * Decimal(str(risk_decision.risk_fraction))
        risk_per_lot = self._risk_per_lot(r_distance, snapshot)
        if risk_per_lot <= 0:
            return None
        volume_lots = self._round_down_volume(risk_amount_usd / risk_per_lot, snapshot)
        if volume_lots < snapshot.symbol_spec.volume_min:
            return None
        basket_id = f"{snapshot.symbol}-{side}-{snapshot.server_time:%Y%m%d%H%M%S}"
        magic_number = self._magic_number(basket_id=basket_id, ticket_sequence=ticket_sequence)
        comment = f"v60|{basket_id}|{risk_decision.risk_posture}|{analysis_mode}"
        return MT5V60EntryPlan(
            symbol=snapshot.symbol,
            side=side,
            volume_lots=volume_lots,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_fraction=risk_decision.risk_fraction,
            risk_amount_usd=risk_amount_usd.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
            r_distance_price=r_distance,
            analysis_mode="stop_loss_reversal" if analysis_mode == "stop_loss_reversal" else "standard_entry",
            basket_id=basket_id,
            magic_number=magic_number,
            comment=comment,
        )

    def build_entry_command(
        self,
        *,
        plan: MT5V60EntryPlan,
        reason: str,
        created_at: datetime,
        expires_at: datetime,
        thesis_tags: list[str],
        context_signature: str | None,
        followed_lessons: list[str],
    ) -> MT5V60BridgeCommand:
        return MT5V60BridgeCommand(
            command_id=f"{plan.basket_id}-{plan.magic_number}",
            command_type="place_entry",
            symbol=plan.symbol,
            created_at=created_at,
            expires_at=expires_at,
            basket_id=plan.basket_id,
            side=plan.side,
            volume_lots=plan.volume_lots,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            comment=plan.comment,
            magic_number=plan.magic_number,
            reason=reason,
            metadata={
                "risk_fraction": plan.risk_fraction,
                "risk_amount_usd": float(plan.risk_amount_usd),
                "entry_price": float(plan.entry_price),
                "initial_stop_loss": float(plan.stop_loss),
                "hard_take_profit": float(plan.take_profit),
                "r_distance_price": float(plan.r_distance_price),
                "analysis_mode": plan.analysis_mode,
                "thesis_tags": thesis_tags,
                "context_signature": context_signature,
                "followed_lessons": followed_lessons,
            },
        )

    def build_modify_command(
        self,
        *,
        ticket: MT5V60TicketRecord,
        snapshot: MT5V60BridgeSnapshot,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
        reason: str,
        created_at: datetime,
        expires_at: datetime,
        metadata: dict[str, object] | None = None,
    ) -> MT5V60BridgeCommand | None:
        levels = self.validate_modify_levels(ticket=ticket, snapshot=snapshot, stop_loss=stop_loss, take_profit=take_profit)
        if levels is None:
            return None
        resolved_stop, resolved_take_profit = levels
        if resolved_stop == ticket.stop_loss and resolved_take_profit == ticket.take_profit:
            return None
        return MT5V60BridgeCommand(
            command_id=f"modify-{ticket.ticket_id}-{int(created_at.timestamp())}",
            command_type="modify_ticket",
            symbol=ticket.symbol,
            created_at=created_at,
            expires_at=expires_at,
            ticket_id=ticket.ticket_id,
            basket_id=ticket.basket_id,
            stop_loss=resolved_stop,
            take_profit=resolved_take_profit,
            reason=reason,
            metadata=dict(metadata or {}),
        )

    def build_close_command(
        self,
        *,
        ticket: MT5V60TicketRecord,
        volume_lots: Decimal,
        reason: str,
        created_at: datetime,
        expires_at: datetime,
        metadata: dict[str, object] | None = None,
    ) -> MT5V60BridgeCommand | None:
        if volume_lots <= 0:
            return None
        close_volume = min(volume_lots, ticket.current_volume_lots)
        if close_volume <= 0:
            return None
        return MT5V60BridgeCommand(
            command_id=f"close-{ticket.ticket_id}-{int(created_at.timestamp())}",
            command_type="close_ticket",
            symbol=ticket.symbol,
            created_at=created_at,
            expires_at=expires_at,
            ticket_id=ticket.ticket_id,
            basket_id=ticket.basket_id,
            volume_lots=close_volume,
            reason=reason,
            metadata=dict(metadata or {}),
        )

    def validate_entry_levels(
        self,
        *,
        side: str,
        entry_price: Decimal,
        snapshot: MT5V60BridgeSnapshot,
        stop_loss: Decimal,
        take_profit: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal] | None:
        tick_size = snapshot.symbol_spec.tick_size
        min_distance = self._minimum_broker_protection_distance(snapshot)
        if side == "long":
            resolved_stop = self._round_down_to_tick(stop_loss, tick_size)
            resolved_take_profit = self._round_up_to_tick(take_profit, tick_size)
            max_valid_stop = self._round_down_to_tick(snapshot.bid - min_distance, tick_size)
            min_valid_tp = self._round_up_to_tick(snapshot.ask + min_distance, tick_size)
            if resolved_stop <= 0 or resolved_stop > max_valid_stop:
                return None
            if resolved_take_profit <= min_valid_tp:
                return None
            r_distance = entry_price - resolved_stop
            tp_distance = resolved_take_profit - entry_price
        else:
            resolved_stop = self._round_up_to_tick(stop_loss, tick_size)
            resolved_take_profit = self._round_down_to_tick(take_profit, tick_size)
            min_valid_stop = self._round_up_to_tick(snapshot.ask + min_distance, tick_size)
            max_valid_tp = self._round_down_to_tick(snapshot.bid - min_distance, tick_size)
            if resolved_stop <= min_valid_stop:
                return None
            if resolved_take_profit >= max_valid_tp or resolved_take_profit <= 0:
                return None
            r_distance = resolved_stop - entry_price
            tp_distance = entry_price - resolved_take_profit
        if r_distance <= 0 or tp_distance <= 0 or tp_distance > r_distance:
            return None
        return resolved_stop, resolved_take_profit, r_distance

    def validate_modify_levels(
        self,
        *,
        ticket: MT5V60TicketRecord,
        snapshot: MT5V60BridgeSnapshot,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
    ) -> tuple[Decimal | None, Decimal | None] | None:
        resolved_stop = ticket.stop_loss
        resolved_take_profit = ticket.take_profit
        if stop_loss is not None:
            resolved_stop = self._validated_modify_stop_loss(
                ticket=ticket,
                snapshot=snapshot,
                requested_stop_loss=stop_loss,
            )
        if take_profit is not None:
            resolved_take_profit = self._validated_modify_take_profit(
                ticket=ticket,
                snapshot=snapshot,
                requested_take_profit=take_profit,
            )
        return resolved_stop, resolved_take_profit

    def _validated_modify_stop_loss(
        self,
        *,
        ticket: MT5V60TicketRecord,
        snapshot: MT5V60BridgeSnapshot,
        requested_stop_loss: Decimal,
    ) -> Decimal | None:
        tick_size = snapshot.symbol_spec.tick_size
        min_distance = self._minimum_broker_protection_distance(snapshot)
        if ticket.side == "long":
            candidate = self._round_down_to_tick(requested_stop_loss, tick_size)
            if candidate < ticket.initial_stop_loss:
                return ticket.stop_loss
            if ticket.stop_loss is not None and candidate < ticket.stop_loss:
                return ticket.stop_loss
            if candidate > snapshot.bid - min_distance:
                return ticket.stop_loss
        else:
            candidate = self._round_up_to_tick(requested_stop_loss, tick_size)
            if candidate > ticket.initial_stop_loss:
                return ticket.stop_loss
            if ticket.stop_loss is not None and candidate > ticket.stop_loss:
                return ticket.stop_loss
            if candidate < snapshot.ask + min_distance:
                return ticket.stop_loss
        if self._stop_distance_from_market(ticket=ticket, snapshot=snapshot, stop_loss=candidate) < self._minimum_manager_stop_distance(
            ticket=ticket,
            snapshot=snapshot,
        ):
            return ticket.stop_loss
        return candidate

    def _validated_modify_take_profit(
        self,
        *,
        ticket: MT5V60TicketRecord,
        snapshot: MT5V60BridgeSnapshot,
        requested_take_profit: Decimal,
    ) -> Decimal | None:
        tick_size = snapshot.symbol_spec.tick_size
        min_distance = self._minimum_broker_protection_distance(snapshot)
        if ticket.side == "long":
            candidate = self._round_up_to_tick(requested_take_profit, tick_size)
            if candidate <= snapshot.ask + min_distance:
                return ticket.take_profit
            if candidate <= ticket.open_price:
                return ticket.take_profit
            if candidate > self._round_up_to_tick(ticket.hard_take_profit, tick_size):
                return ticket.take_profit
            return candidate

        candidate = self._round_down_to_tick(requested_take_profit, tick_size)
        if candidate >= snapshot.bid - min_distance or candidate <= 0:
            return ticket.take_profit
        if candidate >= ticket.open_price:
            return ticket.take_profit
        if candidate < self._round_down_to_tick(ticket.hard_take_profit, tick_size):
            return ticket.take_profit
        return candidate

    def partial_close_volume(
        self,
        *,
        original_volume_lots: Decimal,
        close_fraction: Decimal,
        snapshot: MT5V60BridgeSnapshot,
    ) -> Decimal:
        requested = original_volume_lots * close_fraction
        rounded = self._round_down_volume(requested, snapshot)
        if rounded < snapshot.symbol_spec.volume_min:
            return Decimal("0")
        return min(rounded, original_volume_lots)

    def _risk_per_lot(self, r_distance: Decimal, snapshot: MT5V60BridgeSnapshot) -> Decimal:
        ticks = r_distance / snapshot.symbol_spec.tick_size
        return ticks * snapshot.symbol_spec.tick_value

    def _minimum_broker_protection_distance(self, snapshot: MT5V60BridgeSnapshot) -> Decimal:
        spread_price = snapshot.ask - snapshot.bid
        extra_ticks = snapshot.symbol_spec.tick_size * self._broker_stop_buffer_ticks
        return snapshot.symbol_spec.min_stop_distance_price + spread_price + extra_ticks

    def _minimum_manager_stop_distance(
        self,
        *,
        ticket: MT5V60TicketRecord,
        snapshot: MT5V60BridgeSnapshot,
    ) -> Decimal:
        if ticket.stop_loss is None:
            min_r_multiple = Decimal("0.35")
        elif ticket.unrealized_r >= 1.0:
            min_r_multiple = Decimal("0.08")
        elif ticket.unrealized_r >= 0.5:
            min_r_multiple = Decimal("0.12")
        elif ticket.unrealized_r > 0:
            min_r_multiple = Decimal("0.18")
        else:
            min_r_multiple = Decimal("0.25")
        return max(self._minimum_broker_protection_distance(snapshot), ticket.r_distance_price * min_r_multiple)

    def _stop_distance_from_market(
        self,
        *,
        ticket: MT5V60TicketRecord,
        snapshot: MT5V60BridgeSnapshot,
        stop_loss: Decimal,
    ) -> Decimal:
        if ticket.side == "long":
            return snapshot.bid - stop_loss
        return stop_loss - snapshot.ask

    def _round_down_volume(self, value: Decimal, snapshot: MT5V60BridgeSnapshot) -> Decimal:
        if value <= 0:
            return Decimal("0")
        step = snapshot.symbol_spec.volume_step
        rounded = (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step
        return min(rounded, snapshot.symbol_spec.volume_max)

    def _round_down_to_tick(self, value: Decimal, tick_size: Decimal) -> Decimal:
        return (value / tick_size).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick_size

    def _round_up_to_tick(self, value: Decimal, tick_size: Decimal) -> Decimal:
        return (value / tick_size).quantize(Decimal("1"), rounding=ROUND_CEILING) * tick_size

    def _magic_number(self, *, basket_id: str, ticket_sequence: int) -> int:
        digest = blake2s(f"{basket_id}:{ticket_sequence}".encode("utf-8"), digest_size=4).digest()
        return int.from_bytes(digest, "big", signed=False)
