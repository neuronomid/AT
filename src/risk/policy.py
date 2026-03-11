from decimal import Decimal

from data.schemas import AccountSnapshot, MarketSnapshot, RiskDecision, TradeDecision
from execution.order_manager import OrderManager
from risk.guardrails import Guardrails
from risk.sizing import PositionSizer


class RiskPolicy:
    def __init__(
        self,
        *,
        min_confidence: float,
        max_risk_fraction: Decimal,
        max_position_notional_usd: Decimal,
        max_spread_bps: Decimal,
        max_trades_per_hour: int,
        cooldown_seconds: int,
        position_sizer: PositionSizer | None = None,
        guardrails: Guardrails | None = None,
    ) -> None:
        self._min_confidence = min_confidence
        self._max_risk_fraction = max_risk_fraction
        self._max_position_notional_usd = max_position_notional_usd
        self._max_spread_bps = max_spread_bps
        self._max_trades_per_hour = max_trades_per_hour
        self._cooldown_seconds = cooldown_seconds
        self._position_sizer = position_sizer or PositionSizer()
        self._guardrails = guardrails or Guardrails()

    def evaluate(
        self,
        *,
        decision: TradeDecision,
        account_snapshot: AccountSnapshot,
        market_snapshot: MarketSnapshot | None,
        order_manager: OrderManager,
        trades_this_hour: int,
    ) -> RiskDecision:
        if decision.action not in {"buy", "sell", "exit"}:
            return RiskDecision(approved=False, reason="Decision is not an executable trade action.")

        if decision.confidence < self._min_confidence:
            return RiskDecision(approved=False, reason="Decision confidence is below the configured minimum.")

        if account_snapshot.trading_blocked:
            return RiskDecision(approved=False, reason="Account is marked as trading blocked.")

        if account_snapshot.crypto_status and account_snapshot.crypto_status.upper() != "ACTIVE":
            return RiskDecision(approved=False, reason="Crypto trading is not active on the account.")

        symbol = market_snapshot.symbol if market_snapshot is not None else ""
        if order_manager.has_pending_order(symbol):
            return RiskDecision(approved=False, reason="There is already a pending order for this symbol.")

        if order_manager.in_cooldown(self._cooldown_seconds):
            return RiskDecision(approved=False, reason="The cooldown window after the last trade is still active.")

        if not self._guardrails.check_trade_frequency(trades_this_hour, self._max_trades_per_hour):
            return RiskDecision(approved=False, reason="Trade frequency limit reached for the current hour.")

        if market_snapshot is not None:
            spread_bps = self._guardrails.calculate_spread_bps(market_snapshot)
            if spread_bps is not None and spread_bps > self._max_spread_bps:
                return RiskDecision(approved=False, reason="Current spread is wider than the configured limit.")

        allowed_notional = self._position_sizer.size_for_cash(
            cash=account_snapshot.cash,
            risk_fraction=self._max_risk_fraction,
            notional_cap=self._max_position_notional_usd,
        )
        if allowed_notional <= 0:
            return RiskDecision(approved=False, reason="No buying power available for a new trade.")

        return RiskDecision(
            approved=True,
            reason="Trade passes deterministic risk checks.",
            allowed_notional_usd=allowed_notional,
        )
