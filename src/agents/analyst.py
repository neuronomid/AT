from decimal import Decimal

from data.schemas import AccountSnapshot, MarketSnapshot, TradeDecision


class AnalystAgent:
    """Advisory decision layer. Risk and execution stay outside this component."""

    def __init__(
        self,
        *,
        policy_name: str = "baseline",
        max_spread_bps: float = 20.0,
        exit_momentum_3_bps: float = -8.0,
        exit_momentum_5_bps: float = -12.0,
        entry_momentum_3_bps: float = 8.0,
        entry_momentum_5_bps: float = 12.0,
        max_volatility_5_bps: float = 25.0,
    ) -> None:
        self.policy_name = policy_name
        self.max_spread_bps = max_spread_bps
        self.exit_momentum_3_bps = exit_momentum_3_bps
        self.exit_momentum_5_bps = exit_momentum_5_bps
        self.entry_momentum_3_bps = entry_momentum_3_bps
        self.entry_momentum_5_bps = entry_momentum_5_bps
        self.max_volatility_5_bps = max_volatility_5_bps

    def analyze(
        self,
        market_snapshot: MarketSnapshot | None,
        account_snapshot: AccountSnapshot | None,
        features: dict[str, float],
    ) -> TradeDecision:
        if market_snapshot is None or account_snapshot is None:
            return TradeDecision(
                action="do_nothing",
                confidence=0.0,
                rationale="Missing market or account context.",
            )

        sample_count = int(features.get("sample_count", 0))
        spread_bps = features.get("spread_bps", 0.0)
        momentum_3 = features.get("return_3_bps", 0.0)
        momentum_5 = features.get("return_5_bps", 0.0)
        volatility_5 = features.get("volatility_5_bps", 0.0)
        has_position = account_snapshot.open_position_qty > Decimal("0")

        if sample_count < 5:
            return TradeDecision(
                action="do_nothing",
                confidence=0.0,
                rationale="Not enough rolling samples yet to evaluate the setup.",
            )

        if spread_bps > self.max_spread_bps:
            return TradeDecision(
                action="do_nothing",
                confidence=0.0,
                rationale="Spread is too wide for a controlled entry.",
            )

        if has_position and (
            momentum_3 < self.exit_momentum_3_bps or momentum_5 < self.exit_momentum_5_bps
        ):
            confidence = min(0.95, 0.60 + abs(min(momentum_3, momentum_5)) / 50)
            return TradeDecision(
                action="exit",
                confidence=confidence,
                rationale="Open position is losing short-term momentum and the exit rule fired.",
            )

        if (
            not has_position
            and momentum_3 > self.entry_momentum_3_bps
            and momentum_5 > self.entry_momentum_5_bps
            and volatility_5 < self.max_volatility_5_bps
        ):
            confidence = min(0.95, 0.60 + (momentum_3 + momentum_5) / 100)
            return TradeDecision(
                action="buy",
                confidence=confidence,
                rationale="Short-term momentum is positive, spread is controlled, and volatility remains acceptable.",
            )

        return TradeDecision(
            action="do_nothing",
            confidence=0.25,
            rationale="No high-conviction setup is present under the current momentum rules.",
        )
