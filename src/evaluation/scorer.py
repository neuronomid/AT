class Scorer:
    def expectancy(self, pnl_values: list[float]) -> float:
        if not pnl_values:
            return 0.0
        return sum(pnl_values) / len(pnl_values)

    def max_drawdown(self, equity_curve: list[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            max_drawdown = max(max_drawdown, peak - value)
        return max_drawdown

    def score(
        self,
        *,
        realized_pnl_bps: float,
        trade_returns_bps: list[float],
        max_drawdown_bps: float,
        exposure_ratio: float,
    ) -> float:
        expectancy = self.expectancy(trade_returns_bps)
        exposure_penalty = exposure_ratio * 5.0
        return realized_pnl_bps + expectancy - (max_drawdown_bps * 0.5) - exposure_penalty
