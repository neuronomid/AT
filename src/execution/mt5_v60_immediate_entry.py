from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR
from hashlib import blake2s

from data.mt5_v60_schemas import (
    MT5V60BridgeCommand,
    MT5V60BridgeSnapshot,
    MT5V60EntryDecision,
    MT5V60RiskDecision,
)


@dataclass
class MT5V60ImmediateEntryBuildOutcome:
    command: MT5V60BridgeCommand | None
    plan_payload: dict[str, object] | None
    rejection_reason: str | None = None


class MT5V60ImmediateEntryBuilder:
    def __init__(self, *, broker_stop_buffer_ticks: Decimal = Decimal("1")) -> None:
        self._broker_stop_buffer_ticks = broker_stop_buffer_ticks

    def build(
        self,
        *,
        decision: MT5V60EntryDecision,
        snapshot: MT5V60BridgeSnapshot,
        risk_decision: MT5V60RiskDecision,
        analysis_mode: str = "standard_entry",
        ticket_sequence: int = 1,
    ) -> MT5V60ImmediateEntryBuildOutcome:
        if not risk_decision.approved or risk_decision.risk_fraction is None:
            return MT5V60ImmediateEntryBuildOutcome(
                command=None,
                plan_payload=None,
                rejection_reason="Risk decision did not approve the entry.",
            )
        if decision.stop_loss_price is None or decision.take_profit_price is None:
            return MT5V60ImmediateEntryBuildOutcome(
                command=None,
                plan_payload=None,
                rejection_reason="Entry decision is missing the internal stop loss or take profit planning anchors.",
            )

        side = "long" if decision.action == "enter_long" else "short"
        tick_size = snapshot.symbol_spec.tick_size
        entry_price = snapshot.ask if side == "long" else snapshot.bid
        stop_loss = self._round_stop_loss(price=decision.stop_loss_price, tick_size=tick_size, side=side)
        take_profit = self._round_take_profit(price=decision.take_profit_price, tick_size=tick_size, side=side)
        normalized_levels = self._normalize_reference_levels(
            side=side,
            snapshot=snapshot,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        if normalized_levels is None:
            return MT5V60ImmediateEntryBuildOutcome(
                command=None,
                plan_payload=None,
                rejection_reason="Stop loss or take profit is invalid for the current MT5 quote.",
            )
        stop_loss, take_profit = normalized_levels
        r_distance = abs(entry_price - stop_loss)
        if r_distance <= 0:
            return MT5V60ImmediateEntryBuildOutcome(
                command=None,
                plan_payload=None,
                rejection_reason="Stop loss is not far enough from the market price to size the trade.",
            )

        account_base = snapshot.account.balance if snapshot.account.balance > 0 else snapshot.account.equity
        risk_amount_usd = account_base * Decimal(str(risk_decision.risk_fraction))
        risk_per_lot = self._risk_per_lot(r_distance=r_distance, snapshot=snapshot)
        if risk_per_lot <= 0:
            return MT5V60ImmediateEntryBuildOutcome(
                command=None,
                plan_payload=None,
                rejection_reason="Could not compute a positive per-lot risk amount.",
            )

        volume_lots = self._round_down_volume(risk_amount_usd / risk_per_lot, snapshot=snapshot)
        if volume_lots < snapshot.symbol_spec.volume_min:
            return MT5V60ImmediateEntryBuildOutcome(
                command=None,
                plan_payload=None,
                rejection_reason="Computed volume is below the broker minimum lot size.",
            )

        basket_id = f"{snapshot.symbol}-{side}-{snapshot.server_time:%Y%m%d%H%M%S}"
        magic_number = self._magic_number(basket_id=basket_id, ticket_sequence=ticket_sequence)
        normalized_mode = "stop_loss_reversal" if analysis_mode == "stop_loss_reversal" else "standard_entry"
        comment = f"v60|{basket_id}|{risk_decision.risk_posture}|{normalized_mode}"

        command = MT5V60BridgeCommand(
            command_id=f"{basket_id}-{magic_number}",
            command_type="place_entry",
            symbol=snapshot.symbol,
            created_at=snapshot.server_time,
            expires_at=None,
            basket_id=basket_id,
            side=side,
            volume_lots=volume_lots,
            stop_loss=None,
            take_profit=None,
            comment=comment,
            magic_number=magic_number,
            reason=decision.rationale,
            metadata={
                "risk_fraction": risk_decision.risk_fraction,
                "risk_amount_usd": float(risk_amount_usd.quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
                "entry_price": float(entry_price),
                "initial_stop_loss": float(stop_loss),
                "hard_take_profit": float(take_profit),
                "r_distance_price": float(r_distance),
                "analysis_mode": normalized_mode,
                "thesis_tags": decision.thesis_tags,
                "context_signature": decision.context_signature,
                "followed_lessons": [],
                "entry_submitted_without_broker_protection": True,
            },
        )
        plan_payload: dict[str, object] = {
            "symbol": snapshot.symbol,
            "side": side,
            "volume_lots": volume_lots,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk_fraction": risk_decision.risk_fraction,
            "risk_amount_usd": risk_amount_usd.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
            "r_distance_price": r_distance,
            "analysis_mode": normalized_mode,
            "basket_id": basket_id,
            "magic_number": magic_number,
            "comment": comment,
            "entry_submitted_without_broker_protection": True,
        }
        return MT5V60ImmediateEntryBuildOutcome(command=command, plan_payload=plan_payload)

    def _risk_per_lot(self, *, r_distance: Decimal, snapshot: MT5V60BridgeSnapshot) -> Decimal:
        ticks = r_distance / snapshot.symbol_spec.tick_size
        return ticks * snapshot.symbol_spec.tick_value

    def _round_down_volume(self, value: Decimal, *, snapshot: MT5V60BridgeSnapshot) -> Decimal:
        if value <= 0:
            return Decimal("0")
        step = snapshot.symbol_spec.volume_step
        rounded = (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step
        return min(rounded, snapshot.symbol_spec.volume_max)

    def _round_stop_loss(self, *, price: Decimal, tick_size: Decimal, side: str) -> Decimal:
        if side == "long":
            return self._round_down_to_tick(price, tick_size)
        return self._round_up_to_tick(price, tick_size)

    def _round_take_profit(self, *, price: Decimal, tick_size: Decimal, side: str) -> Decimal:
        if side == "long":
            return self._round_up_to_tick(price, tick_size)
        return self._round_down_to_tick(price, tick_size)

    def _round_down_to_tick(self, value: Decimal, tick_size: Decimal) -> Decimal:
        return (value / tick_size).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick_size

    def _round_up_to_tick(self, value: Decimal, tick_size: Decimal) -> Decimal:
        return (value / tick_size).quantize(Decimal("1"), rounding=ROUND_CEILING) * tick_size

    def _magic_number(self, *, basket_id: str, ticket_sequence: int) -> int:
        digest = blake2s(f"{basket_id}:{ticket_sequence}".encode("utf-8"), digest_size=4).digest()
        return int.from_bytes(digest, "big", signed=False)

    def _normalize_reference_levels(
        self,
        *,
        side: str,
        snapshot: MT5V60BridgeSnapshot,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
    ) -> tuple[Decimal, Decimal] | None:
        tick_size = snapshot.symbol_spec.tick_size
        min_distance = snapshot.symbol_spec.min_stop_distance_price + (tick_size * self._broker_stop_buffer_ticks)
        if side == "long":
            max_valid_stop = self._round_down_to_tick(snapshot.bid - min_distance, tick_size)
            min_valid_take_profit = self._round_up_to_tick(snapshot.bid + min_distance, tick_size)
            resolved_stop = min(stop_loss, max_valid_stop)
            resolved_take_profit = max(take_profit, min_valid_take_profit)
            if resolved_stop <= 0 or resolved_stop >= entry_price:
                return None
            if resolved_take_profit <= entry_price:
                return None
            return resolved_stop, resolved_take_profit

        min_valid_stop = self._round_up_to_tick(snapshot.ask + min_distance, tick_size)
        max_valid_take_profit = self._round_down_to_tick(snapshot.ask - min_distance, tick_size)
        resolved_stop = max(stop_loss, min_valid_stop)
        resolved_take_profit = min(take_profit, max_valid_take_profit)
        if resolved_stop <= entry_price:
            return None
        if resolved_take_profit <= 0 or resolved_take_profit >= entry_price:
            return None
        return resolved_stop, resolved_take_profit
