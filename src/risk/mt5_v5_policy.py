from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from data.schemas import BridgeSnapshot, EntryDecision, MT5RiskDecision, TradeReflection
from execution.mt5_ticket_book import MT5TicketBook


class MT5RiskPostureEngine:
    _multipliers = {
        "reduced": 0.75,
        "neutral": 1.0,
        "mildly_aggressive": 1.10,
    }

    def derive(self, reflections: Sequence[TradeReflection]) -> tuple[str, float]:
        recent = list(reflections)[-5:]
        if not recent:
            return "neutral", self._multipliers["neutral"]

        trailing_three = recent[-3:]
        avg_recent_r = sum(reflection.realized_r for reflection in recent) / len(recent)
        losses_last_three = sum(1 for reflection in trailing_three if reflection.realized_r < 0)
        wins_last_five = sum(1 for reflection in recent if reflection.realized_r > 0)

        if losses_last_three >= 2 or avg_recent_r <= -0.25:
            return "reduced", self._multipliers["reduced"]
        if len(recent) >= 3 and wins_last_five >= 3 and avg_recent_r >= 0.35:
            return "mildly_aggressive", self._multipliers["mildly_aggressive"]
        return "neutral", self._multipliers["neutral"]


class MT5V5RiskArbiter:
    def __init__(
        self,
        *,
        symbol: str = "EURUSD",
        account_mode: str = "hedging",
        min_confidence: float = 0.60,
        max_spread_bps: float = 20.0,
        decision_window_seconds: int = 60,
        min_risk_fraction: float = 0.0025,
        max_risk_fraction: float = 0.0075,
        basket_risk_cap: float = 0.01,
        max_tickets_per_symbol: int = 2,
        max_new_entries_per_bar: int = 1,
        daily_loss_pct: float = 0.02,
    ) -> None:
        self._symbol = symbol
        self._account_mode = account_mode
        self._min_confidence = min_confidence
        self._max_spread_bps = max_spread_bps
        self._decision_window_seconds = decision_window_seconds
        self._min_risk_fraction = min_risk_fraction
        self._max_risk_fraction = max_risk_fraction
        self._basket_risk_cap = basket_risk_cap
        self._max_tickets_per_symbol = max_tickets_per_symbol
        self._max_new_entries_per_bar = max_new_entries_per_bar
        self._daily_loss_pct = daily_loss_pct

    def decision_window_open(self, snapshot: BridgeSnapshot) -> bool:
        if not snapshot.bars_5m:
            return False
        latest_closed = snapshot.bars_5m[-1].end_at
        deadline = latest_closed + timedelta(seconds=self._decision_window_seconds)
        return snapshot.server_time <= deadline

    def evaluate_entry(
        self,
        *,
        decision: EntryDecision,
        snapshot: BridgeSnapshot,
        ticket_book: MT5TicketBook,
        risk_posture: str,
        risk_multiplier: float,
        pending_symbol_command: bool,
        new_entries_this_bar: int,
    ) -> MT5RiskDecision:
        if decision.action == "hold":
            return MT5RiskDecision(approved=False, reason="Entry decision is hold.", risk_posture=risk_posture)
        if snapshot.symbol.strip().upper() != self._symbol.strip().upper():
            return MT5RiskDecision(approved=False, reason="Snapshot symbol does not match runtime symbol.", risk_posture=risk_posture)
        if snapshot.account.account_mode != self._account_mode:
            return MT5RiskDecision(approved=False, reason="MT5 account mode does not match runtime expectation.", risk_posture=risk_posture)
        if not snapshot.account.trade_allowed:
            return MT5RiskDecision(approved=False, reason="MT5 account is not trade enabled.", risk_posture=risk_posture)
        if decision.confidence < self._min_confidence:
            return MT5RiskDecision(approved=False, reason="Decision confidence is below minimum.", risk_posture=risk_posture)
        if not self.decision_window_open(snapshot):
            return MT5RiskDecision(approved=False, reason="Entry decision missed the 60-second bar window.", risk_posture=risk_posture)
        if snapshot.spread_bps is not None and snapshot.spread_bps > self._max_spread_bps:
            return MT5RiskDecision(approved=False, reason="Spread is wider than the configured limit.", risk_posture=risk_posture)
        if pending_symbol_command:
            return MT5RiskDecision(approved=False, reason="A pending command already exists for the symbol.", risk_posture=risk_posture)
        if self._daily_loss_triggered(snapshot):
            return MT5RiskDecision(approved=False, reason="Daily loss kill switch is active.", risk_posture=risk_posture)
        if new_entries_this_bar >= self._max_new_entries_per_bar:
            return MT5RiskDecision(approved=False, reason="New-entry limit for the current 5m bar has been reached.", risk_posture=risk_posture)

        side = "long" if decision.action == "enter_long" else "short"
        if ticket_book.has_opposite_exposure(snapshot.symbol, side):
            return MT5RiskDecision(approved=False, reason="Opposite-direction exposure is already open.", risk_posture=risk_posture)

        side_count = ticket_book.ticket_count(snapshot.symbol, side)
        if side_count >= self._max_tickets_per_symbol:
            return MT5RiskDecision(approved=False, reason="Maximum same-direction ticket count has been reached.", risk_posture=risk_posture)
        if side_count == 1 and not ticket_book.can_add_second_ticket(
            snapshot.symbol,
            side,
            current_bar_end=(snapshot.bars_5m[-1].end_at if snapshot.bars_5m else None),
        ):
            return MT5RiskDecision(approved=False, reason="Second-ticket stacking gate is not satisfied.", risk_posture=risk_posture)

        adjusted_min = self._min_risk_fraction * risk_multiplier
        adjusted_max = self._max_risk_fraction * risk_multiplier
        requested = decision.requested_risk_fraction if decision.requested_risk_fraction is not None else (adjusted_min + adjusted_max) / 2.0
        risk_fraction = max(adjusted_min, min(adjusted_max, requested))
        proposed_risk_usd = snapshot.account.equity * Decimal(str(risk_fraction))
        basket_cap_usd = snapshot.account.equity * Decimal(str(self._basket_risk_cap))
        current_open_risk = ticket_book.total_open_risk_usd(snapshot.symbol, side=side)
        if current_open_risk + proposed_risk_usd > basket_cap_usd:
            return MT5RiskDecision(approved=False, reason="Basket risk cap would be exceeded.", risk_posture=risk_posture)

        return MT5RiskDecision(
            approved=True,
            reason="Entry passed MT5 V5 deterministic checks.",
            risk_fraction=risk_fraction,
            risk_posture=risk_posture,
        )

    def _daily_loss_triggered(self, snapshot: BridgeSnapshot) -> bool:
        balance = snapshot.account.balance
        equity = snapshot.account.equity
        if balance <= 0:
            return False
        loss_ratio = float((balance - equity) / balance)
        return loss_ratio >= self._daily_loss_pct
