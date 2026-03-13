from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from hashlib import blake2s

from data.schemas import BridgeCommand, BridgeSnapshot, EntryDecision, MT5EntryPlan, MT5RiskDecision


class MT5EntryPlanner:
    def __init__(
        self,
        *,
        min_stop_pips: float = 6.0,
        max_stop_pips: float = 25.0,
        stop_atr_multiple: float = 1.25,
        take_profit_multiple: float = 1.25,
        volume_step: Decimal = Decimal("0.01"),
        min_volume_lots: Decimal = Decimal("0.01"),
        pip_size: Decimal = Decimal("0.0001"),
        pip_value_usd_per_lot: Decimal = Decimal("10"),
    ) -> None:
        self._min_stop_pips = min_stop_pips
        self._max_stop_pips = max_stop_pips
        self._stop_atr_multiple = stop_atr_multiple
        self._take_profit_multiple = take_profit_multiple
        self._volume_step = volume_step
        self._min_volume_lots = min_volume_lots
        self._pip_size = pip_size
        self._pip_value_usd_per_lot = pip_value_usd_per_lot

    def plan_entry(
        self,
        *,
        decision: EntryDecision,
        snapshot: BridgeSnapshot,
        risk_decision: MT5RiskDecision,
        existing_basket_id: str | None = None,
        ticket_sequence: int = 1,
    ) -> MT5EntryPlan | None:
        if not risk_decision.approved or risk_decision.risk_fraction is None:
            return None

        side = "long" if decision.action == "enter_long" else "short"
        entry_price = snapshot.ask if side == "long" else snapshot.bid
        atr_pips = self._atr_pips(snapshot)
        stop_distance_pips = self._clamp(atr_pips * self._stop_atr_multiple, self._min_stop_pips, self._max_stop_pips)
        take_profit_distance_pips = stop_distance_pips * self._take_profit_multiple
        risk_amount_usd = snapshot.account.equity * Decimal(str(risk_decision.risk_fraction))
        volume_lots = self._round_down(risk_amount_usd / (Decimal(str(stop_distance_pips)) * self._pip_value_usd_per_lot))
        if volume_lots < self._min_volume_lots:
            return None

        stop_distance_price = Decimal(str(stop_distance_pips)) * self._pip_size
        tp_distance_price = Decimal(str(take_profit_distance_pips)) * self._pip_size
        stop_loss = entry_price - stop_distance_price if side == "long" else entry_price + stop_distance_price
        take_profit = entry_price + tp_distance_price if side == "long" else entry_price - tp_distance_price
        basket_id = existing_basket_id or f"{snapshot.symbol}-{side}-{snapshot.server_time:%Y%m%d%H%M%S}"
        magic_number = self._magic_number(basket_id=basket_id, ticket_sequence=ticket_sequence)
        comment = f"v5|{basket_id}|t{ticket_sequence}|{risk_decision.risk_posture}"
        return MT5EntryPlan(
            symbol=snapshot.symbol,
            side=side,
            volume_lots=volume_lots,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_fraction=risk_decision.risk_fraction,
            risk_amount_usd=risk_amount_usd.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
            stop_distance_pips=stop_distance_pips,
            take_profit_distance_pips=take_profit_distance_pips,
            basket_id=basket_id,
            magic_number=magic_number,
            comment=comment,
        )

    def build_entry_command(
        self,
        *,
        plan: MT5EntryPlan,
        reason: str,
        created_at: datetime,
        expires_at: datetime,
        thesis_tags: list[str],
    ) -> BridgeCommand:
        return BridgeCommand(
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
                "stop_distance_pips": plan.stop_distance_pips,
                "take_profit_distance_pips": plan.take_profit_distance_pips,
                "thesis_tags": thesis_tags,
            },
        )

    def _atr_pips(self, snapshot: BridgeSnapshot, period: int = 14) -> float:
        bars = snapshot.bars_5m[-period:]
        if len(bars) < 2:
            return self._min_stop_pips
        true_ranges: list[float] = []
        previous_close = float(bars[0].close_price)
        for bar in bars[1:]:
            high = float(bar.high_price)
            low = float(bar.low_price)
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
            true_ranges.append(true_range)
            previous_close = float(bar.close_price)
        atr_price = sum(true_ranges) / len(true_ranges) if true_ranges else float(self._pip_size)
        return max(float(Decimal(str(atr_price)) / self._pip_size), self._min_stop_pips)

    def _magic_number(self, *, basket_id: str, ticket_sequence: int) -> int:
        digest = blake2s(f"{basket_id}:{ticket_sequence}".encode("utf-8"), digest_size=4).hexdigest()
        return int(digest, 16)

    def _round_down(self, value: Decimal) -> Decimal:
        if value <= 0:
            return Decimal("0")
        return (value / self._volume_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * self._volume_step

    def _clamp(self, value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))
