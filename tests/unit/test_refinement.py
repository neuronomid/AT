from datetime import datetime, timezone

from control_plane.models import PolicyVersionRecord
from data.schemas import BacktestReport, PromotionDecision, ReplayMetrics
from evaluation.refinement import PolicyRefiner


def _policy(policy_name: str) -> PolicyVersionRecord:
    return PolicyVersionRecord(
        id=f"{policy_name}-id",
        policy_name=policy_name,
        version="v2.2",
        status="baseline" if policy_name == "baseline" else "candidate",
        thresholds={
            "min_regime_probability": 0.6,
            "entry_momentum_3_bps": 6.0,
            "entry_momentum_5_bps": 9.5,
            "min_trend_strength_bps": 12.0,
            "min_volume_ratio_5_30": 1.02,
            "breakout_buffer_bps": 0.4,
            "min_entry_score": 4,
            "min_confirmation_count": 3,
            "max_spread_bps": 16.0,
            "exit_regime_probability": 0.72,
            "hard_exit_momentum_3_bps": 7.0,
            "hard_exit_momentum_5_bps": 12.0,
        },
        risk_params={},
        strategy_config={
            "min_expected_edge_bps": 0.8,
            "min_atr_percentile_30": 0.18,
            "max_atr_percentile_30": 0.82,
            "time_stop_bars": 12,
            "partial_take_profit_fraction": 0.35,
            "trailing_stop_multiple": 0.55,
            "max_reward_multiple": 2.25,
            "requested_risk_fraction": 0.002,
            "max_requested_notional_fraction_cash": 0.1,
            "base_slippage_bps": 0.9,
        },
        notes="test",
    )


def test_policy_refiner_loosens_when_activity_is_too_low() -> None:
    report = BacktestReport(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        total_bars=1000,
        bars_inserted=1000,
        baseline=ReplayMetrics(policy_name="baseline@v2.2", closed_trades=40),
        candidate=ReplayMetrics(
            policy_name="walk_forward_best",
            closed_trades=4,
            realized_pnl_bps=-2.0,
            average_trade_bps=-0.5,
            win_rate=0.4,
            max_drawdown_bps=7.0,
        ),
        decision=PromotionDecision(
            status="reject",
            recommended=False,
            reason="negative",
            baseline_policy="baseline@v2.2",
            candidate_policy="walk_forward_best",
            baseline_score=-1.0,
            candidate_score=-2.0,
        ),
        windows=[],
    )

    results = PolicyRefiner(min_closed_trades_90d=18).refine(
        policies={name: _policy(name) for name in ("baseline", "conservative", "aggressive")},
        report=report,
        advisor_markdown="The system is still warm-up dependent and does too much do_nothing.",
        next_version="v2.2-r1",
    )

    baseline = next(result for result in results if result.policy_name == "baseline")
    assert baseline.version == "v2.2-r1"
    assert float(baseline.thresholds["min_regime_probability"]) < 0.6
    assert int(baseline.thresholds["min_confirmation_count"]) <= 3
