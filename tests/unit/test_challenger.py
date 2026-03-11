from data.schemas import ReplayMetrics
from evaluation.challenger import Challenger


def test_challenger_returns_insufficient_data_when_trade_count_is_too_low() -> None:
    baseline = ReplayMetrics(policy_name="baseline", closed_trades=0, score=0.0)
    candidate = ReplayMetrics(policy_name="challenger", closed_trades=0, score=10.0)

    decision = Challenger().compare(baseline, candidate)

    assert decision.status == "insufficient_data"


def test_challenger_promotes_better_candidate() -> None:
    baseline = ReplayMetrics(
        policy_name="baseline",
        closed_trades=2,
        score=100.0,
        realized_pnl_bps=120.0,
        max_drawdown_bps=30.0,
    )
    candidate = ReplayMetrics(
        policy_name="challenger",
        closed_trades=2,
        score=110.0,
        realized_pnl_bps=140.0,
        max_drawdown_bps=40.0,
    )

    decision = Challenger(min_score_improvement=5.0, max_additional_drawdown_bps=20.0).compare(
        baseline, candidate
    )

    assert decision.status == "promote"
    assert decision.recommended is True
