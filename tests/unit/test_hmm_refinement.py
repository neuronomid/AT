from datetime import datetime, timezone

from control_plane.policies import build_hmm_v3_policy
from data.schemas import (
    BacktestReport,
    BacktestRegimeSummary,
    BacktestTradeSummary,
    PromotionDecision,
    ReplayMetrics,
)
from evaluation.hmm_refinement import HMMStrategyRefiner


def _report() -> BacktestReport:
    return BacktestReport(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        start_at=datetime(2025, 12, 11, tzinfo=timezone.utc),
        end_at=datetime(2026, 3, 11, tzinfo=timezone.utc),
        total_bars=1000,
        bars_inserted=0,
        baseline=ReplayMetrics(policy_name="baseline@v2.2", closed_trades=100, score=-10.0),
        candidate=ReplayMetrics(
            policy_name="baseline@v3.0",
            closed_trades=40,
            realized_pnl_bps=-25.0,
            average_trade_bps=-8.0,
            win_rate=0.34,
            max_drawdown_bps=80.0,
            score=-50.0,
        ),
        decision=PromotionDecision(
            status="reject",
            recommended=False,
            reason="weak",
            baseline_policy="baseline@v2.2",
            candidate_policy="baseline@v3.0",
            baseline_score=-10.0,
            candidate_score=-50.0,
        ),
        trade_summary=BacktestTradeSummary(total_trades=40, winning_trades=12, losing_trades=28),
        regime_summary=BacktestRegimeSummary(regime_occupancy={"bull_trend": 100}),
    )


def test_hmm_refiner_keeps_architecture_and_tunes_thresholds() -> None:
    base_policy = build_hmm_v3_policy(version="v3.0", notes="test")
    refiner = HMMStrategyRefiner()

    result = refiner.refine(
        base_policy=base_policy,
        report=_report(),
        advisor_markdown="Need better exits and tighter risk handling under stress.",
        next_version="v3.1",
    )

    assert result.policy.version == "v3.1"
    assert result.policy.strategy_config["strategy_family"] == "hmm_regime_v3"
    assert float(result.policy.strategy_config["hmm_bear_exit_probability"]) < float(
        base_policy.strategy_config["hmm_bear_exit_probability"]
    )
    assert float(result.policy.strategy_config["requested_risk_fraction"]) < float(
        base_policy.strategy_config["requested_risk_fraction"]
    )
