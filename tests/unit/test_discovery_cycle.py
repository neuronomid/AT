from datetime import datetime, timezone

from app.discovery_cycle import passes_three_month_gate
from data.schemas import BacktestReport, PromotionDecision, ReplayMetrics


def _report(*, pnl: float, avg_trade: float, trades: int) -> BacktestReport:
    return BacktestReport(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        start_at=datetime(2025, 12, 11, tzinfo=timezone.utc),
        end_at=datetime(2026, 3, 11, tzinfo=timezone.utc),
        total_bars=1000,
        bars_inserted=0,
        baseline=ReplayMetrics(policy_name="baseline@v2.2", closed_trades=50, score=1.0),
        candidate=ReplayMetrics(
            policy_name="baseline@discovery",
            closed_trades=trades,
            realized_pnl_bps=pnl,
            average_trade_bps=avg_trade,
            score=2.0,
        ),
        decision=PromotionDecision(
            status="promote",
            recommended=True,
            reason="test",
            baseline_policy="baseline@v2.2",
            candidate_policy="baseline@discovery",
            baseline_score=1.0,
            candidate_score=2.0,
        ),
    )


def test_three_month_gate_requires_positive_pnl_positive_trade_expectancy_and_trade_count() -> None:
    assert passes_three_month_gate(_report(pnl=8.0, avg_trade=1.2, trades=18), min_closed_trades=18) == (
        True,
        "six_month_validation_ready",
    )
    assert passes_three_month_gate(_report(pnl=-1.0, avg_trade=1.2, trades=18), min_closed_trades=18) == (
        False,
        "three_month_failed_gate",
    )
    assert passes_three_month_gate(_report(pnl=8.0, avg_trade=-0.1, trades=18), min_closed_trades=18) == (
        False,
        "three_month_failed_gate",
    )
    assert passes_three_month_gate(_report(pnl=8.0, avg_trade=1.2, trades=12), min_closed_trades=18) == (
        False,
        "three_month_failed_gate",
    )
