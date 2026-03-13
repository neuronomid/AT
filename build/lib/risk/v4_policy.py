from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal

from data.schemas import AccountSnapshot, LiveCandle, LLMRuntimeDecision, RiskDecision, TradeDecision, TradePlan, ExecutionPlan
from execution.order_manager import OrderManager
from execution.position_tracker import PositionTracker


class V4RiskPolicy:
    def __init__(
        self,
        *,
        min_confidence: float = 0.60,
        max_trades_per_hour: int = 10,
        risk_fraction_min: float = 0.0025,
        risk_fraction_max: float = 0.015,
        take_profit_r_min: float = 0.5,
        take_profit_r_max: float = 2.0,
        max_spread_bps: float = 20.0,
        stale_after_seconds: int = 90,
        max_bars_in_trade: int = 20,
        daily_loss_pct: float = 0.02,
    ) -> None:
        self._min_confidence = min_confidence
        self._max_trades_per_hour = max_trades_per_hour
        self._risk_fraction_min = risk_fraction_min
        self._risk_fraction_max = risk_fraction_max
        self._take_profit_r_min = take_profit_r_min
        self._take_profit_r_max = take_profit_r_max
        self._max_spread_bps = max_spread_bps
        self._stale_after_seconds = stale_after_seconds
        self._max_bars_in_trade = max_bars_in_trade
        self._daily_loss_pct = daily_loss_pct

    @property
    def max_bars_in_trade(self) -> int:
        return self._max_bars_in_trade

    def normalize_decision(
        self,
        *,
        runtime_decision: LLMRuntimeDecision,
        candles: Sequence[LiveCandle],
        account_snapshot: AccountSnapshot,
        context_signature: str,
    ) -> TradeDecision:
        if not candles:
            return TradeDecision(action="do_nothing", confidence=0.0, rationale="No closed candles available.")

        latest_close = candles[-1].close_price
        risk_fraction = self._clamp(
            runtime_decision.risk_fraction_equity if runtime_decision.risk_fraction_equity is not None else 0.005,
            self._risk_fraction_min,
            self._risk_fraction_max,
        )
        take_profit_r = self._clamp(
            runtime_decision.take_profit_r if runtime_decision.take_profit_r is not None else 1.0,
            self._take_profit_r_min,
            self._take_profit_r_max,
        )
        reduce_fraction = self._normalize_reduce_fraction(runtime_decision.reduce_fraction)

        if runtime_decision.action != "buy":
            return TradeDecision(
                action=runtime_decision.action,
                confidence=runtime_decision.confidence,
                rationale=runtime_decision.rationale,
                risk_fraction_equity=risk_fraction if runtime_decision.action == "buy" else None,
                take_profit_r=take_profit_r if runtime_decision.action == "buy" else None,
                reduce_fraction=reduce_fraction,
                thesis_tags=list(runtime_decision.thesis_tags),
                context_signature=context_signature,
                execution_plan=ExecutionPlan(order_type="market", time_in_force="gtc"),
            )

        stop_distance = self._stop_distance(candles, latest_close)
        stop_loss_bps = self._price_distance_bps(latest_close, stop_distance)
        take_profit_bps = stop_loss_bps * take_profit_r
        risk_budget = account_snapshot.equity * Decimal(str(risk_fraction))
        distance_ratio = stop_distance / latest_close if latest_close > 0 else Decimal("0")
        requested_notional = Decimal("0")
        if distance_ratio > 0:
            requested_notional = risk_budget / distance_ratio

        stop_price = latest_close - stop_distance
        take_profit_price = latest_close + (stop_distance * Decimal(str(take_profit_r)))
        return TradeDecision(
            action="buy",
            confidence=runtime_decision.confidence,
            rationale=runtime_decision.rationale,
            trade_plan=TradePlan(
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                max_take_profit_bps=take_profit_bps,
                trailing_stop_bps=stop_loss_bps * 0.75,
                time_stop_bars=self._max_bars_in_trade,
                partial_take_profit_fraction=0.5,
            ),
            execution_plan=ExecutionPlan(
                requested_notional_usd=float(requested_notional),
                order_type="market",
                time_in_force="gtc",
                entry_reference_price=float(latest_close),
                stop_price=float(stop_price),
                take_profit_price=float(take_profit_price),
                max_take_profit_price=float(take_profit_price),
                planned_risk_usd=float(risk_budget),
            ),
            planned_risk_usd=float(risk_budget),
            risk_fraction_equity=risk_fraction,
            take_profit_r=take_profit_r,
            thesis_tags=list(runtime_decision.thesis_tags),
            context_signature=context_signature,
        )

    def evaluate(
        self,
        *,
        decision: TradeDecision,
        account_snapshot: AccountSnapshot,
        order_manager: OrderManager,
        position_tracker: PositionTracker,
        trades_this_hour: int,
        spread_bps: float | None,
        stale_age_seconds: float | None,
        recent_context_signatures: Sequence[str],
        last_losing_signature: str | None,
    ) -> RiskDecision:
        if decision.action not in {"buy", "reduce", "exit"}:
            return RiskDecision(approved=False, reason="Decision is not an executable trade action.")

        if stale_age_seconds is None or stale_age_seconds > self._stale_after_seconds:
            return RiskDecision(approved=False, reason="Live market data is stale.")

        if account_snapshot.trading_blocked:
            return RiskDecision(approved=False, reason="Account is marked as trading blocked.")

        if account_snapshot.crypto_status and account_snapshot.crypto_status.upper() != "ACTIVE":
            return RiskDecision(approved=False, reason="Crypto trading is not active on the account.")

        if order_manager.has_pending_order("ETH/USD"):
            return RiskDecision(approved=False, reason="There is already a pending order for this symbol.")

        if decision.action == "buy":
            if decision.confidence < self._min_confidence:
                return RiskDecision(approved=False, reason="Decision confidence is below the configured minimum.")
            if trades_this_hour >= self._max_trades_per_hour:
                return RiskDecision(approved=False, reason="Entry frequency limit reached for the current hour.")
            if position_tracker.has_position() or account_snapshot.open_position_qty > 0:
                return RiskDecision(approved=False, reason="A long position is already open.")
            if spread_bps is not None and spread_bps > self._max_spread_bps:
                return RiskDecision(approved=False, reason="Current spread is wider than the configured limit.")
            if (
                last_losing_signature is not None
                and decision.context_signature == last_losing_signature
                and len(recent_context_signatures) >= 3
                and all(signature == decision.context_signature for signature in recent_context_signatures[-3:])
            ):
                return RiskDecision(approved=False, reason="Context-sensitive anti-churn block is active.")

            requested_notional = Decimal(
                str(decision.execution_plan.requested_notional_usd if decision.execution_plan else 0.0)
            )
            allowed_notional = min(requested_notional, account_snapshot.buying_power, account_snapshot.cash)
            if allowed_notional <= 0:
                return RiskDecision(approved=False, reason="No buying power available for a new trade.")
            return RiskDecision(
                approved=True,
                reason="Entry passes v4 deterministic checks.",
                allowed_notional_usd=allowed_notional,
            )

        if account_snapshot.open_position_qty <= 0 or not position_tracker.has_position():
            return RiskDecision(approved=False, reason="No open position is available to reduce or exit.")

        if decision.action == "reduce" and (decision.reduce_fraction is None or decision.reduce_fraction <= 0):
            return RiskDecision(approved=False, reason="Reduce decisions require a valid reduce fraction.")

        return RiskDecision(
            approved=True,
            reason="Position-management action passes v4 deterministic checks.",
            allowed_notional_usd=Decimal("0"),
        )

    def should_kill_for_daily_loss(self, *, session_start_equity: Decimal, current_equity: Decimal) -> bool:
        if session_start_equity <= 0:
            return False
        loss_ratio = float((session_start_equity - current_equity) / session_start_equity)
        return loss_ratio >= self._daily_loss_pct

    def clamp_take_profit_r(self, take_profit_r: float | None) -> float:
        return self._clamp(take_profit_r if take_profit_r is not None else 1.0, self._take_profit_r_min, self._take_profit_r_max)

    def _stop_distance(self, candles: Sequence[LiveCandle], latest_close: Decimal) -> Decimal:
        swing_window = list(candles)[-5:]
        swing_low = min(candle.low_price for candle in swing_window)
        structural_distance = latest_close - swing_low
        atr = Decimal(str(self._atr(candles, 14)))
        atr_min = atr * Decimal("0.75")
        atr_max = atr * Decimal("1.75")
        raw_distance = structural_distance if structural_distance > 0 else atr
        if atr_min > 0:
            raw_distance = max(raw_distance, atr_min)
        if atr_max > 0:
            raw_distance = min(raw_distance, atr_max)
        return max(raw_distance, Decimal("0.01"))

    def _atr(self, candles: Sequence[LiveCandle], period: int) -> float:
        if len(candles) < 2:
            return 0.0
        window = list(candles)[-period:]
        true_ranges: list[float] = []
        previous_close = float(window[0].close_price)
        for candle in window[1:]:
            high = float(candle.high_price)
            low = float(candle.low_price)
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
            true_ranges.append(true_range)
            previous_close = float(candle.close_price)
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    def _price_distance_bps(self, reference_price: Decimal, distance: Decimal) -> float:
        if reference_price <= 0:
            return 0.0
        return float((distance / reference_price) * Decimal("10000"))

    def _clamp(self, value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))

    def _normalize_reduce_fraction(self, value: float | None) -> float | None:
        if value is None:
            return None
        allowed = [0.25, 0.5, 1.0]
        return min(allowed, key=lambda candidate: abs(candidate - value))
