from datetime import datetime, timezone

from agents.research_reviewer import ResearchReviewAdvisor
from data.schemas import (
    BacktestReport,
    DiscoveryDatasetSummary,
    DiscoveryRegimeSummary,
    DiscoveryReport,
    DiscoveredStrategySpec,
    IndicatorBucketTable,
    InverseAppendixSummary,
    PatternFinding,
    PromotionDecision,
    ReplayMetrics,
)
from research.reporting import (
    render_discovered_strategy_markdown,
    render_discovery_report_markdown,
    render_inverse_appendix_markdown,
)


def _pattern() -> PatternFinding:
    return PatternFinding(
        direction="long",
        regime="bull_trend",
        support_count=120,
        score_bps=8.5,
        estimated_round_trip_cost_bps=4.3,
        forward_15m_mean_bps=4.0,
        forward_30m_mean_bps=6.0,
        forward_60m_mean_bps=10.0,
        mean_favorable_excursion_bps=18.0,
        mean_adverse_excursion_bps=7.0,
        percentile_60_favorable_excursion_bps=20.0,
        percentile_60_adverse_excursion_bps=9.0,
        percentile_85_favorable_excursion_bps=28.0,
        median_bars_to_peak_favorable=40,
        thresholds={
            "regime_probability_min": 0.74,
            "continuation_probability_min": 0.71,
            "momentum_5_bps_min": 9.5,
            "volume_ratio_min": 1.2,
            "breakout_bps_min": 3.8,
            "abs_zscore_max": 1.4,
        },
        atr_band=[0.2, 0.7],
    )


def _discovery_report() -> DiscoveryReport:
    pattern = _pattern()
    strategy = DiscoveredStrategySpec(
        policy_name="baseline",
        version="discovery-test",
        policy_label="baseline@discovery-test",
        direction="long_flat",
        source_regime="bull_trend",
        thresholds={"entry_momentum_5_bps": 9.5},
        strategy_config={"take_profit_multiple": 1.2, "time_stop_bars": 40},
        notes="Synthesized from test discovery data.",
        selected_pattern=pattern,
    )
    return DiscoveryReport(
        dataset=DiscoveryDatasetSummary(
            symbol="ETH/USD",
            timeframe="1Min",
            start_at=datetime(2025, 12, 11, tzinfo=timezone.utc),
            end_at=datetime(2026, 3, 11, tzinfo=timezone.utc),
            warmup_start_at=datetime(2025, 11, 21, tzinfo=timezone.utc),
            total_bars=1000,
            evaluation_bars=900,
            evaluable_bars=700,
            estimated_round_trip_cost_bps=4.3,
        ),
        regime_summary=DiscoveryRegimeSummary(
            regime_occupancy={"bull_trend": 300, "quiet_range": 200},
            regime_transitions={"bull_trend->quiet_range": 4},
            average_forward_60m_bps={"bull_trend": 6.2},
            average_probability={"bull_trend": 0.81},
        ),
        indicator_bucket_tables=[
            IndicatorBucketTable(
                indicator="regime_probability",
                direction="long",
                buckets={"Q1": -2.0, "Q4": 8.0},
            )
        ],
        headline_findings=["Bull regime carried the strongest forward expectancy."],
        long_patterns=[pattern],
        selected_pattern=pattern,
        candidate_strategy=strategy,
        inverse_appendix=InverseAppendixSummary(
            enabled=True,
            headline="Short appendix remained research only.",
        ),
    )


def _backtest() -> BacktestReport:
    return BacktestReport(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        start_at=datetime(2025, 12, 11, tzinfo=timezone.utc),
        end_at=datetime(2026, 3, 11, tzinfo=timezone.utc),
        total_bars=1000,
        bars_inserted=0,
        baseline=ReplayMetrics(policy_name="baseline@v2.2", closed_trades=60, score=1.0),
        candidate=ReplayMetrics(
            policy_name="baseline@discovery-test",
            closed_trades=24,
            realized_pnl_bps=12.0,
            average_trade_bps=1.5,
            win_rate=0.52,
            max_drawdown_bps=6.0,
            exposure_ratio=0.08,
            score=14.0,
        ),
        decision=PromotionDecision(
            status="promote",
            recommended=True,
            reason="test",
            baseline_policy="baseline@v2.2",
            candidate_policy="baseline@discovery-test",
            baseline_score=1.0,
            candidate_score=14.0,
        ),
    )


def test_research_reviewer_prompt_and_renderers_include_new_sections() -> None:
    discovery_report = _discovery_report()
    backtest = _backtest()
    advisor = ResearchReviewAdvisor(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    prompt = advisor.build_prompt(
        discovery_report=discovery_report,
        backtest_3m=backtest,
        inverse_appendix=discovery_report.inverse_appendix,
    )
    discovery_md = render_discovery_report_markdown(discovery_report)
    strategy_md = render_discovered_strategy_markdown(discovery_report.candidate_strategy)
    inverse_md = render_inverse_appendix_markdown(discovery_report.inverse_appendix)

    assert "Dataset summary" in prompt
    assert "Selected primary pattern" in prompt
    assert "Three-month backtest" in prompt
    assert "Inverse appendix" in prompt
    assert "## Selected Pattern" in discovery_md
    assert "## Strategy Config" in strategy_md
    assert "Inverse Research Appendix" in inverse_md
