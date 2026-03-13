from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from data.schemas import TradeDecision


@dataclass
class OpenTradeState:
    symbol: str
    opened_at: datetime
    entry_price: Decimal
    initial_qty: Decimal
    remaining_qty: Decimal
    risk_fraction_equity: float
    risk_amount_usd: Decimal
    initial_r_distance: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    take_profit_r: float
    trailing_r: float
    entry_spread_bps: float | None = None
    context_signature: str | None = None
    thesis_tags: list[str] = field(default_factory=list)
    entry_packet_summary: dict[str, object] = field(default_factory=dict)
    followed_lessons: list[str] = field(default_factory=list)
    partial_taken: bool = False
    bars_held: int = 0
    highest_price: Decimal = Decimal("0")
    lowest_price: Decimal = Decimal("0")
    realized_pnl_usd: Decimal = Decimal("0")
    realized_r: float = 0.0
    max_favorable_r: float = 0.0
    max_adverse_r: float = 0.0

    def __post_init__(self) -> None:
        if self.highest_price <= 0:
            self.highest_price = self.entry_price
        if self.lowest_price <= 0:
            self.lowest_price = self.entry_price

    @property
    def trailing_stop_price(self) -> Decimal | None:
        if not self.partial_taken:
            return None
        trailing_distance = self.initial_r_distance * Decimal(str(self.trailing_r))
        return max(self.entry_price, self.highest_price - trailing_distance)

    def to_prompt_payload(self, current_price: float) -> dict[str, object]:
        unrealized_pnl = (Decimal(str(current_price)) - self.entry_price) * self.remaining_qty
        unrealized_r = float(unrealized_pnl / self.risk_amount_usd) if self.risk_amount_usd > 0 else 0.0
        return {
            "opened_at": self.opened_at.isoformat(),
            "entry_price": float(self.entry_price),
            "initial_qty": float(self.initial_qty),
            "remaining_qty": float(self.remaining_qty),
            "stop_loss_price": float(self.stop_loss_price),
            "take_profit_price": float(self.take_profit_price),
            "take_profit_r": self.take_profit_r,
            "partial_taken": self.partial_taken,
            "bars_held": self.bars_held,
            "realized_pnl_usd": float(self.realized_pnl_usd),
            "realized_r": self.realized_r,
            "unrealized_r": unrealized_r,
            "context_signature": self.context_signature,
            "thesis_tags": self.thesis_tags,
        }


class PositionTracker:
    """Tracks v4 open-position state and deterministic trade management levels."""

    _min_position_qty = Decimal("0.000001")

    def __init__(self) -> None:
        self._open_trade: OpenTradeState | None = None

    @property
    def open_trade(self) -> OpenTradeState | None:
        return self._open_trade

    def has_position(self) -> bool:
        return self._open_trade is not None and self._open_trade.remaining_qty >= self._min_position_qty

    def clear(self) -> None:
        self._open_trade = None

    def record_candle(self, close_price: Decimal) -> None:
        trade = self._open_trade
        if trade is None:
            return
        trade.bars_held += 1
        trade.highest_price = max(trade.highest_price, close_price)
        trade.lowest_price = min(trade.lowest_price, close_price)
        if trade.initial_r_distance > 0:
            favorable_r = float((trade.highest_price - trade.entry_price) / trade.initial_r_distance)
            adverse_r = float((trade.entry_price - trade.lowest_price) / trade.initial_r_distance)
            trade.max_favorable_r = max(trade.max_favorable_r, favorable_r)
            trade.max_adverse_r = max(trade.max_adverse_r, adverse_r)

    def open_from_fill(
        self,
        *,
        opened_at: datetime,
        symbol: str,
        fill_price: Decimal,
        filled_qty: Decimal,
        decision: TradeDecision,
        risk_amount_usd: Decimal,
        stop_loss_price: Decimal,
        take_profit_price: Decimal,
        initial_r_distance: Decimal,
        entry_spread_bps: float | None,
        entry_packet_summary: dict[str, object],
        followed_lessons: list[str],
    ) -> None:
        self._open_trade = OpenTradeState(
            symbol=symbol,
            opened_at=opened_at,
            entry_price=fill_price,
            initial_qty=filled_qty,
            remaining_qty=filled_qty,
            risk_fraction_equity=decision.risk_fraction_equity or 0.0,
            risk_amount_usd=risk_amount_usd,
            initial_r_distance=initial_r_distance,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            take_profit_r=decision.take_profit_r or 1.0,
            trailing_r=0.75,
            entry_spread_bps=entry_spread_bps,
            context_signature=decision.context_signature,
            thesis_tags=list(decision.thesis_tags),
            entry_packet_summary=entry_packet_summary,
            followed_lessons=followed_lessons,
        )

    def bootstrap_from_account(
        self,
        *,
        opened_at: datetime,
        symbol: str,
        entry_price: Decimal,
        qty: Decimal,
        stop_loss_price: Decimal,
        take_profit_price: Decimal,
        initial_r_distance: Decimal,
        context_signature: str = "bootstrap",
        thesis_tags: list[str] | None = None,
    ) -> None:
        risk_amount_usd = max(initial_r_distance * qty, Decimal("0.01"))
        self._open_trade = OpenTradeState(
            symbol=symbol,
            opened_at=opened_at,
            entry_price=entry_price,
            initial_qty=qty,
            remaining_qty=qty,
            risk_fraction_equity=0.0,
            risk_amount_usd=risk_amount_usd,
            initial_r_distance=initial_r_distance,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            take_profit_r=1.0,
            trailing_r=0.75,
            entry_spread_bps=None,
            context_signature=context_signature,
            thesis_tags=list(thesis_tags or ["bootstrap"]),
            entry_packet_summary={},
            followed_lessons=[],
        )

    def apply_sell_fill(
        self,
        *,
        fill_price: Decimal,
        filled_qty: Decimal,
        decision: TradeDecision,
    ) -> OpenTradeState | None:
        trade = self._open_trade
        if trade is None or filled_qty <= 0:
            return None

        realized_qty = min(filled_qty, trade.remaining_qty)
        pnl = (fill_price - trade.entry_price) * realized_qty
        trade.realized_pnl_usd += pnl
        if trade.risk_amount_usd > 0:
            trade.realized_r += float(pnl / trade.risk_amount_usd)
        trade.remaining_qty -= realized_qty
        if abs(trade.remaining_qty) < self._min_position_qty:
            trade.remaining_qty = Decimal("0")

        if decision.action == "reduce":
            trade.partial_taken = True
            trade.stop_loss_price = max(trade.stop_loss_price, trade.entry_price)

        if trade.remaining_qty > 0:
            return None

        completed = replace(trade)
        completed.remaining_qty = Decimal("0")
        self._open_trade = None
        return completed

    def sync_with_account(
        self,
        *,
        qty: Decimal,
        avg_entry_price: Decimal | None = None,
    ) -> bool:
        trade = self._open_trade
        if trade is None:
            return False

        normalized_qty = qty if abs(qty) >= self._min_position_qty else Decimal("0")
        if normalized_qty <= 0:
            self._open_trade = None
            return True

        changed = False
        if avg_entry_price is not None and avg_entry_price > 0 and trade.entry_price != avg_entry_price:
            trade.entry_price = avg_entry_price
            trade.highest_price = max(trade.highest_price, avg_entry_price)
            trade.lowest_price = min(trade.lowest_price, avg_entry_price)
            changed = True

        if trade.remaining_qty != normalized_qty:
            if trade.remaining_qty == trade.initial_qty and not trade.partial_taken and trade.realized_pnl_usd == 0:
                trade.initial_qty = normalized_qty
            trade.remaining_qty = normalized_qty
            changed = True

        if trade.initial_qty < trade.remaining_qty:
            trade.initial_qty = trade.remaining_qty
            changed = True

        return changed

    def should_take_partial(self, current_price: Decimal) -> bool:
        trade = self._open_trade
        if trade is None or trade.partial_taken:
            return False
        return current_price >= trade.take_profit_price

    def should_hard_stop(self, current_price: Decimal) -> bool:
        trade = self._open_trade
        if trade is None:
            return False
        return current_price <= trade.stop_loss_price

    def should_trailing_stop(self, current_price: Decimal) -> bool:
        trade = self._open_trade
        if trade is None:
            return False
        trailing_stop = trade.trailing_stop_price
        return trailing_stop is not None and current_price <= trailing_stop

    def should_time_exit(self, max_bars: int) -> bool:
        trade = self._open_trade
        if trade is None:
            return False
        return trade.bars_held >= max_bars

    def suggested_reduce_fraction(self) -> float:
        trade = self._open_trade
        if trade is None or trade.partial_taken:
            return 0.0
        return 0.5
