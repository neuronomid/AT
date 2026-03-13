from data.schemas import PromotionDecision, ReplayMetrics


class Challenger:
    """Compares candidate policies against the current baseline."""

    def __init__(
        self,
        *,
        min_closed_trades: int = 1,
        min_score_improvement: float = 5.0,
        max_additional_drawdown_bps: float = 50.0,
    ) -> None:
        self._min_closed_trades = min_closed_trades
        self._min_score_improvement = min_score_improvement
        self._max_additional_drawdown_bps = max_additional_drawdown_bps

    def compare(self, baseline: ReplayMetrics, candidate: ReplayMetrics) -> PromotionDecision:
        if baseline.closed_trades < self._min_closed_trades or candidate.closed_trades < self._min_closed_trades:
            return PromotionDecision(
                status="insufficient_data",
                recommended=False,
                reason="Not enough closed trades to make a promotion decision.",
                baseline_policy=baseline.policy_name,
                candidate_policy=candidate.policy_name,
                baseline_score=baseline.score,
                candidate_score=candidate.score,
            )

        if candidate.score < baseline.score + self._min_score_improvement:
            return PromotionDecision(
                status="reject",
                recommended=False,
                reason="Candidate score did not improve enough over baseline.",
                baseline_policy=baseline.policy_name,
                candidate_policy=candidate.policy_name,
                baseline_score=baseline.score,
                candidate_score=candidate.score,
            )

        if candidate.realized_pnl_bps <= baseline.realized_pnl_bps:
            return PromotionDecision(
                status="reject",
                recommended=False,
                reason="Candidate did not outperform baseline on realized PnL.",
                baseline_policy=baseline.policy_name,
                candidate_policy=candidate.policy_name,
                baseline_score=baseline.score,
                candidate_score=candidate.score,
            )

        if candidate.max_drawdown_bps > baseline.max_drawdown_bps + self._max_additional_drawdown_bps:
            return PromotionDecision(
                status="reject",
                recommended=False,
                reason="Candidate increased drawdown beyond the allowed tolerance.",
                baseline_policy=baseline.policy_name,
                candidate_policy=candidate.policy_name,
                baseline_score=baseline.score,
                candidate_score=candidate.score,
            )

        return PromotionDecision(
            status="promote",
            recommended=True,
            reason="Candidate improved score and realized PnL without violating drawdown tolerance.",
            baseline_policy=baseline.policy_name,
            candidate_policy=candidate.policy_name,
            baseline_score=baseline.score,
            candidate_score=candidate.score,
        )
