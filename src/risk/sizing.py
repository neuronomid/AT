from decimal import Decimal


class PositionSizer:
    def size_for_cash(self, cash: Decimal, risk_fraction: Decimal, notional_cap: Decimal) -> Decimal:
        sized = cash * risk_fraction
        if sized <= 0:
            return Decimal("0")
        return min(sized, notional_cap)
