from decimal import Decimal


class PositionSizer:
    def size_for_trade(
        self,
        *,
        cash: Decimal,
        equity: Decimal,
        risk_fraction: Decimal,
        notional_cap: Decimal,
        stop_loss_bps: float | None = None,
        requested_notional_usd: float | None = None,
    ) -> Decimal:
        if cash <= 0 or risk_fraction <= 0 or notional_cap <= 0:
            return Decimal("0")

        sized = cash * risk_fraction
        if stop_loss_bps is not None and stop_loss_bps > 0:
            risk_budget = max(cash, equity) * risk_fraction
            sized = risk_budget / Decimal(str(stop_loss_bps / 10000.0))

        if requested_notional_usd is not None and requested_notional_usd > 0:
            sized = min(sized, Decimal(str(requested_notional_usd)))

        if sized <= 0:
            return Decimal("0")
        return min(sized, notional_cap, cash)
