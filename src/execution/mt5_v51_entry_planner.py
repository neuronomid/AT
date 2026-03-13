from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR
from hashlib import blake2s

from data.mt5_v51_schemas import (
    MT5V51BridgeCommand,
    MT5V51BridgeSnapshot,
    MT5V51EntryDecision,
    MT5V51EntryPlan,
    MT5V51RiskDecision,
    MT5V51TicketRecord,
)


class MT5V51EntryPlanner:
    def __init__(
        self,
        *,
        partial_target_r: Decimal = Decimal("0.5"),
        final_target_r: Decimal = Decimal("1.0"),
        broker_stop_buffer_ticks: Decimal = Decimal("10"),
    ) -> None:
        self._partial_target_r = partial_target_r
        self._final_target_r = final_target_r
        self._broker_stop_buffer_ticks = broker_stop_buffer_ticks

    def plan_entry(
        self,
        *,
        decision: MT5V51EntryDecision,
        snapshot: MT5V51BridgeSnapshot,
        risk_decision: MT5V51RiskDecision,
        ticket_sequence: int = 1,
    ) -> MT5V51EntryPlan | None:
        if not risk_decision.approved or risk_decision.risk_fraction is None:
            return None

        side = "long" if decision.action == "enter_long" else "short"
        entry_price = snapshot.ask if side == "long" else snapshot.bid
        bars = self._closed_bars(snapshot)
        if len(bars) < 20:
            return None
        atr = self._atr_price(bars[-14:])
        if atr <= 0:
            return None

        lows = [bar.low_price for bar in bars[-20:]]
        highs = [bar.high_price for bar in bars[-20:]]
        atr_offset = atr * Decimal("0.25")
        if side == "long":
            structure_stop = min(lows) - atr_offset
            vol_stop = entry_price - atr
            raw_stop = max(structure_stop, vol_stop)
            raw_r_distance = entry_price - raw_stop
        else:
            structure_stop = max(highs) + atr_offset
            vol_stop = entry_price + atr
            raw_stop = min(structure_stop, vol_stop)
            raw_r_distance = raw_stop - entry_price

        min_r_distance = max(
            atr * Decimal("0.80"),
            self._minimum_broker_protection_distance(snapshot),
            snapshot.symbol_spec.tick_size,
        )
        max_r_distance = atr * Decimal("2.20")
        r_distance = min(max(raw_r_distance, min_r_distance), max_r_distance)
        if r_distance <= 0:
            return None

        if side == "long":
            stop_loss = self._round_down_to_tick(entry_price - r_distance, snapshot.symbol_spec.tick_size)
            soft_take_profit_1 = self._round_up_to_tick(
                entry_price + (r_distance * self._partial_target_r),
                snapshot.symbol_spec.tick_size,
            )
            soft_take_profit_2 = self._round_up_to_tick(
                entry_price + (r_distance * self._final_target_r),
                snapshot.symbol_spec.tick_size,
            )
            take_profit = soft_take_profit_2
        else:
            stop_loss = self._round_up_to_tick(entry_price + r_distance, snapshot.symbol_spec.tick_size)
            soft_take_profit_1 = self._round_down_to_tick(
                entry_price - (r_distance * self._partial_target_r),
                snapshot.symbol_spec.tick_size,
            )
            soft_take_profit_2 = self._round_down_to_tick(
                entry_price - (r_distance * self._final_target_r),
                snapshot.symbol_spec.tick_size,
            )
            take_profit = soft_take_profit_2
        r_distance = abs(entry_price - stop_loss)

        risk_amount_usd = snapshot.account.equity * Decimal(str(risk_decision.risk_fraction))
        risk_per_lot = self._risk_per_lot(r_distance, snapshot)
        if risk_per_lot <= 0:
            return None
        volume_lots = self._round_down_volume(risk_amount_usd / risk_per_lot, snapshot)
        if volume_lots < snapshot.symbol_spec.volume_min:
            return None

        basket_id = f"{snapshot.symbol}-{side}-{snapshot.server_time:%Y%m%d%H%M%S}"
        magic_number = self._magic_number(basket_id=basket_id, ticket_sequence=ticket_sequence)
        comment = f"v51|{basket_id}|{risk_decision.risk_posture}"
        return MT5V51EntryPlan(
            symbol=snapshot.symbol,
            side=side,
            volume_lots=volume_lots,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            soft_take_profit_1=soft_take_profit_1,
            soft_take_profit_2=soft_take_profit_2,
            risk_fraction=risk_decision.risk_fraction,
            risk_amount_usd=risk_amount_usd.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
            r_distance_price=r_distance,
            basket_id=basket_id,
            magic_number=magic_number,
            comment=comment,
        )

    def build_entry_command(
        self,
        *,
        plan: MT5V51EntryPlan,
        reason: str,
        created_at: datetime,
        expires_at: datetime,
        thesis_tags: list[str],
        context_signature: str | None,
        followed_lessons: list[str],
    ) -> MT5V51BridgeCommand:
        return MT5V51BridgeCommand(
            command_id=f"{plan.basket_id}-{plan.magic_number}",
            command_type="place_entry",
            symbol=plan.symbol,
            created_at=created_at,
            expires_at=expires_at,
            basket_id=plan.basket_id,
            side=plan.side,
            volume_lots=plan.volume_lots,
            stop_loss=None,
            take_profit=None,
            comment=plan.comment,
            magic_number=plan.magic_number,
            reason=reason,
            metadata={
                "risk_fraction": plan.risk_fraction,
                "risk_amount_usd": float(plan.risk_amount_usd),
                "entry_price": float(plan.entry_price),
                "initial_stop_loss": float(plan.stop_loss),
                "r_distance_price": float(plan.r_distance_price),
                "soft_take_profit_1": float(plan.soft_take_profit_1),
                "soft_take_profit_2": float(plan.soft_take_profit_2),
                "hard_take_profit": float(plan.take_profit),
                "attach_protection_after_fill": True,
                "thesis_tags": thesis_tags,
                "context_signature": context_signature,
                "followed_lessons": followed_lessons,
            },
        )

    def build_protection_command(
        self,
        *,
        ticket: MT5V51TicketRecord,
        snapshot: MT5V51BridgeSnapshot,
        reason: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> MT5V51BridgeCommand | None:
        levels = self.protection_levels(ticket=ticket, snapshot=snapshot)
        if levels is None:
            return None
        stop_loss, take_profit = levels
        return MT5V51BridgeCommand(
            command_id=f"protect-{ticket.ticket_id}-{int(created_at.timestamp())}",
            command_type="modify_ticket",
            symbol=ticket.symbol,
            created_at=created_at,
            expires_at=expires_at,
            ticket_id=ticket.ticket_id,
            basket_id=ticket.basket_id,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=reason,
            metadata={"action": "attach_protection"},
        )

    def protection_levels(
        self,
        *,
        ticket: MT5V51TicketRecord,
        snapshot: MT5V51BridgeSnapshot,
    ) -> tuple[Decimal, Decimal] | None:
        min_distance = self._minimum_broker_protection_distance(snapshot)
        tick_size = snapshot.symbol_spec.tick_size
        if ticket.side == "long":
            max_valid_stop = self._round_down_to_tick(snapshot.bid - min_distance, tick_size)
            min_valid_take_profit = self._round_up_to_tick(snapshot.ask + min_distance, tick_size)
            stop_loss = min(self._round_down_to_tick(ticket.initial_stop_loss, tick_size), max_valid_stop)
            take_profit = max(self._round_up_to_tick(ticket.hard_take_profit, tick_size), min_valid_take_profit)
            if stop_loss <= 0 or stop_loss >= snapshot.bid or take_profit <= snapshot.ask:
                return None
            return stop_loss, take_profit

        min_valid_stop = self._round_up_to_tick(snapshot.ask + min_distance, tick_size)
        max_valid_take_profit = self._round_down_to_tick(snapshot.bid - min_distance, tick_size)
        stop_loss = max(self._round_up_to_tick(ticket.initial_stop_loss, tick_size), min_valid_stop)
        take_profit = min(self._round_down_to_tick(ticket.hard_take_profit, tick_size), max_valid_take_profit)
        if stop_loss <= snapshot.ask or take_profit <= 0 or take_profit >= snapshot.bid:
            return None
        return stop_loss, take_profit

    def partial_close_volume(
        self,
        *,
        original_volume_lots: Decimal,
        close_fraction: Decimal,
        snapshot: MT5V51BridgeSnapshot,
    ) -> Decimal:
        requested = original_volume_lots * close_fraction
        rounded = self._round_down_volume(requested, snapshot)
        if rounded < snapshot.symbol_spec.volume_min:
            return Decimal("0")
        return min(rounded, original_volume_lots)

    def _risk_per_lot(self, r_distance: Decimal, snapshot: MT5V51BridgeSnapshot) -> Decimal:
        ticks = r_distance / snapshot.symbol_spec.tick_size
        return ticks * snapshot.symbol_spec.tick_value

    def _minimum_broker_protection_distance(self, snapshot: MT5V51BridgeSnapshot) -> Decimal:
        spread_price = snapshot.ask - snapshot.bid
        safety_buffer = snapshot.symbol_spec.tick_size * self._broker_stop_buffer_ticks
        return snapshot.symbol_spec.min_stop_distance_price + spread_price + safety_buffer

    def _closed_bars(self, snapshot: MT5V51BridgeSnapshot) -> list:
        return [bar for bar in snapshot.bars_1m if bar.complete]

    def _atr_price(self, bars) -> Decimal:
        if len(bars) < 2:
            return Decimal("0")
        true_ranges: list[Decimal] = []
        previous_close = bars[0].close_price
        for bar in bars[1:]:
            true_range = max(
                bar.high_price - bar.low_price,
                abs(bar.high_price - previous_close),
                abs(bar.low_price - previous_close),
            )
            true_ranges.append(true_range)
            previous_close = bar.close_price
        if not true_ranges:
            return Decimal("0")
        return sum(true_ranges) / Decimal(len(true_ranges))

    def _round_down_volume(self, value: Decimal, snapshot: MT5V51BridgeSnapshot) -> Decimal:
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
        digest = blake2s(f"{basket_id}:{ticket_sequence}".encode("utf-8"), digest_size=4).hexdigest()
        return int(digest, 16)
