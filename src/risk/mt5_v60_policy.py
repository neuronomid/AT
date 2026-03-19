from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from data.mt5_v60_schemas import MT5V60BridgeSnapshot, MT5V60EntryDecision, MT5V60RiskDecision
from data.schemas import TradeReflection
from execution.mt5_v60_ticket_registry import MT5V60TicketRegistry
from runtime.mt5_v60_symbols import mt5_v60_symbols_match, normalize_mt5_v60_symbol


class MT5V60RiskPostureEngine:
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


class MT5V60RiskArbiter:
    def __init__(
        self,
        *,
        symbol: str = "BTCUSD@",
        account_mode: str = "hedging",
        min_confidence: float = 0.50,
        max_spread_bps: float = 15.0,
        stale_after_seconds: int = 5,
        min_risk_fraction: float = 0.001,
        max_risk_fraction: float = 0.005,
        daily_loss_pct: float = 0.015,
        max_trades_per_hour: int = 15,
        seeded_entry_times: Sequence[datetime] | None = None,
    ) -> None:
        self._symbol = normalize_mt5_v60_symbol(symbol)
        self._account_mode = account_mode
        self._min_confidence = min_confidence
        self._max_spread_bps = max_spread_bps
        self._stale_after_seconds = stale_after_seconds
        self._min_risk_fraction = min_risk_fraction
        self._max_risk_fraction = max_risk_fraction
        self._daily_loss_pct = daily_loss_pct
        self._max_trades_per_hour = max_trades_per_hour
        normalized_entry_times = sorted(self._normalize_datetime(value) for value in (seeded_entry_times or []))
        self._recent_entry_times: deque[datetime] = deque(normalized_entry_times)

    def record_approved_entry(self, entry_time: datetime) -> None:
        normalized_entry_time = self._normalize_datetime(entry_time)
        self._recent_entry_times.append(normalized_entry_time)
        self._trim(normalized_entry_time)

    def recent_trade_count(self, now: datetime) -> int:
        self._trim(self._normalize_datetime(now))
        return len(self._recent_entry_times)

    def evaluate_entry(
        self,
        *,
        decision: MT5V60EntryDecision,
        snapshot: MT5V60BridgeSnapshot,
        registry: MT5V60TicketRegistry,
        risk_posture: str,
        risk_multiplier: float,
        pending_symbol_command: bool,
        allow_stale_snapshot: bool = False,
    ) -> MT5V60RiskDecision:
        del risk_multiplier
        if decision.action == "hold":
            return MT5V60RiskDecision(approved=False, reason="Entry decision is hold.", risk_posture=risk_posture)
        if not mt5_v60_symbols_match(snapshot.symbol, self._symbol):
            return MT5V60RiskDecision(
                approved=False,
                reason="Snapshot symbol does not match runtime symbol.",
                risk_posture=risk_posture,
            )
        if snapshot.account.account_mode != self._account_mode:
            return MT5V60RiskDecision(
                approved=False,
                reason="MT5 account mode does not match runtime expectation.",
                risk_posture=risk_posture,
            )
        if not snapshot.account.trade_allowed:
            return MT5V60RiskDecision(approved=False, reason="MT5 account is not trade enabled.", risk_posture=risk_posture)
        if decision.confidence < self._min_confidence:
            return MT5V60RiskDecision(approved=False, reason="Decision confidence is below minimum.", risk_posture=risk_posture)
        if not allow_stale_snapshot and self.snapshot_is_stale(snapshot):
            return MT5V60RiskDecision(approved=False, reason="Snapshot is stale.", risk_posture=risk_posture)
        if snapshot.spread_bps is not None and snapshot.spread_bps > self._max_spread_bps:
            return MT5V60RiskDecision(approved=False, reason="Spread is wider than the configured limit.", risk_posture=risk_posture)
        if pending_symbol_command:
            return MT5V60RiskDecision(approved=False, reason="A pending command already exists for the symbol.", risk_posture=risk_posture)
        if self._daily_loss_triggered(snapshot):
            return MT5V60RiskDecision(approved=False, reason="Daily loss kill switch is active.", risk_posture=risk_posture)
        if registry.has_open_position(snapshot.symbol):
            return MT5V60RiskDecision(approved=False, reason="A BTCUSD ticket is already open.", risk_posture=risk_posture)
        if self.recent_trade_count(snapshot.server_time) >= self._max_trades_per_hour:
            return MT5V60RiskDecision(
                approved=False,
                reason="The rolling 60-minute entry cap has been reached.",
                risk_posture=risk_posture,
            )
        requested = (
            decision.requested_risk_fraction
            if decision.requested_risk_fraction is not None
            else (self._min_risk_fraction + self._max_risk_fraction) / 2.0
        )
        risk_fraction = max(self._min_risk_fraction, min(self._max_risk_fraction, requested))
        return MT5V60RiskDecision(
            approved=True,
            reason="Entry passed MT5 V6.0 deterministic checks.",
            risk_fraction=risk_fraction,
            risk_posture=risk_posture,
        )

    def snapshot_is_stale(self, snapshot: MT5V60BridgeSnapshot) -> bool:
        if snapshot.received_at is None:
            return True
        received_at = snapshot.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        else:
            received_at = received_at.astimezone(timezone.utc)
        age = (datetime.now(timezone.utc) - received_at).total_seconds()
        return age > self._stale_after_seconds

    def _daily_loss_triggered(self, snapshot: MT5V60BridgeSnapshot) -> bool:
        balance = snapshot.account.balance
        equity = snapshot.account.equity
        if balance <= 0:
            return False
        loss_ratio = float((balance - equity) / balance)
        return loss_ratio >= self._daily_loss_pct

    def _trim(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=1)
        while self._recent_entry_times and self._recent_entry_times[0] < cutoff:
            self._recent_entry_times.popleft()

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
