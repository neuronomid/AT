from datetime import datetime, timezone

from agents.strategy_advisor import StrategyAdvisor
from data.schemas import BacktestReport, PromotionDecision, ReplayMetrics, ReviewSummary


def test_strategy_advisor_builds_prompt_with_review_backtest_and_lessons() -> None:
    advisor = StrategyAdvisor(
        api_key="test-key",
        model="gpt-5-mini",
        base_url="https://api.openai.com/v1",
    )
    review_summary = ReviewSummary(
        decision_records=12,
        trade_reviews=4,
        executable_decisions=5,
        risk_rejections=3,
        action_counts={"buy": 2, "sell": 1, "do_nothing": 9},
        rejection_reasons={"Expected edge is below the configured minimum.": 2},
        review_outcomes={"entry_opened": 2, "position_reduced": 2},
    )
    backtest_report = BacktestReport(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        start_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
        total_bars=1000,
        bars_inserted=1000,
        baseline=ReplayMetrics(policy_name="baseline@v2", win_rate=0.4, average_trade_bps=-0.2, max_drawdown_bps=6.0, exposure_ratio=0.2),
        candidate=ReplayMetrics(policy_name="walk_forward_best", win_rate=0.42, average_trade_bps=0.1, max_drawdown_bps=4.0, exposure_ratio=0.18),
        decision=PromotionDecision(
            status="reject",
            recommended=False,
            reason="Candidate score did not improve enough over baseline.",
            baseline_policy="baseline@v2",
            candidate_policy="walk_forward_best",
            baseline_score=-2.0,
            candidate_score=-1.0,
        ),
        windows=[],
    )

    prompt = advisor.build_prompt(
        review_summary=review_summary,
        backtest_report=backtest_report,
        lessons=[{"message": "Avoid low-edge entries during range conditions."}],
    )

    assert "ETH/USD" in prompt
    assert "Avoid low-edge entries during range conditions." in prompt
    assert "candidate score" in prompt
