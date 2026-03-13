from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd

from data.schemas import HistoricalBar, PatternFinding
from research.discovery import DiscoveryResearcher


def _bar(index: int, *, price: float, volume: float = 1000.0) -> HistoricalBar:
    open_price = price - 0.2
    close_price = price
    high_price = price + 0.4
    low_price = price - 0.5
    return HistoricalBar(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        timestamp=datetime(2025, 12, 11, tzinfo=timezone.utc) + timedelta(minutes=index),
        open_price=Decimal(str(open_price)),
        high_price=Decimal(str(high_price)),
        low_price=Decimal(str(low_price)),
        close_price=Decimal(str(close_price)),
        volume=Decimal(str(volume)),
    )


def test_build_research_frame_populates_indicator_and_hmm_columns() -> None:
    bars = [
        _bar(index, price=1800 + (index * 0.7) + ((index % 9) * 0.3), volume=1200 + ((index % 13) * 25))
        for index in range(420)
    ]
    researcher = DiscoveryResearcher(
        symbol="ETH/USD",
        timeframe="1Min",
        hmm_resample_minutes=5,
        hmm_train_window_bars=8,
        hmm_retrain_interval_bars=2,
    )

    frame, dataset = researcher.build_research_frame(
        bars=bars,
        start_at=bars[220].timestamp,
        end_at=bars[-2].timestamp,
    )

    assert dataset.total_bars == len(bars)
    assert dataset.evaluation_bars == len(frame)
    assert dataset.evaluable_bars > 0
    assert "ema_20" in frame.columns
    assert "rsi_14" in frame.columns
    assert "macd_histogram" in frame.columns
    assert "regime" in frame.columns
    assert "hmm_atr_percentile_30" in frame.columns
    assert frame["ema_20"].notna().any()
    assert frame["rsi_14"].notna().any()
    assert frame["macd_histogram"].notna().any()
    assert frame["model_ready"].any()


def test_pattern_miner_prefers_profitable_bull_bucket() -> None:
    researcher = DiscoveryResearcher()
    profitable_rows = 100
    unprofitable_rows = 100
    frame = pd.DataFrame(
        {
            "evaluable": [True] * (profitable_rows + unprofitable_rows),
            "regime": ["bull_trend"] * (profitable_rows + unprofitable_rows),
            "bull_probability": [0.85] * profitable_rows + [0.55] * unprofitable_rows,
            "bear_probability": [0.1] * (profitable_rows + unprofitable_rows),
            "bull_continuation": [0.82] * profitable_rows + [0.52] * unprofitable_rows,
            "bear_continuation": [0.1] * (profitable_rows + unprofitable_rows),
            "return_5_bps": [12.0] * profitable_rows + [1.5] * unprofitable_rows,
            "volume_ratio_5_30": [1.45] * profitable_rows + [0.95] * unprofitable_rows,
            "breakout_up_20_bps": [6.0] * profitable_rows + [0.1] * unprofitable_rows,
            "breakdown_20_bps": [0.0] * (profitable_rows + unprofitable_rows),
            "zscore_30": [0.4] * profitable_rows + [2.2] * unprofitable_rows,
            "hmm_atr_percentile_30": [0.4] * profitable_rows + [0.9] * unprofitable_rows,
            "forward_15m_bps": [8.0] * profitable_rows + [-2.0] * unprofitable_rows,
            "forward_30m_bps": [11.0] * profitable_rows + [-3.5] * unprofitable_rows,
            "forward_60m_bps": [16.0] * profitable_rows + [-6.0] * unprofitable_rows,
            "long_mfe_60m_bps": [24.0] * profitable_rows + [4.0] * unprofitable_rows,
            "long_mae_60m_bps": [8.0] * profitable_rows + [15.0] * unprofitable_rows,
            "short_mfe_60m_bps": [0.0] * (profitable_rows + unprofitable_rows),
            "short_mae_60m_bps": [0.0] * (profitable_rows + unprofitable_rows),
            "bars_to_peak_long": [35.0] * profitable_rows + [58.0] * unprofitable_rows,
            "bars_to_peak_short": [0.0] * (profitable_rows + unprofitable_rows),
        }
    )

    findings = researcher._mine_direction_patterns(frame, direction="long")

    assert findings
    top = findings[0]
    assert top.score_bps > 0
    assert top.support_count >= 80
    assert top.thresholds["regime_probability_min"] >= 0.70
    assert top.thresholds["breakout_bps_min"] >= 3.0


def test_strategy_synthesis_keeps_primary_long_flat_and_inverse_separate() -> None:
    researcher = DiscoveryResearcher()
    pattern = PatternFinding(
        direction="long",
        regime="bull_trend",
        support_count=120,
        score_bps=9.5,
        estimated_round_trip_cost_bps=4.3,
        forward_15m_mean_bps=5.0,
        forward_30m_mean_bps=7.0,
        forward_60m_mean_bps=11.0,
        mean_favorable_excursion_bps=18.0,
        mean_adverse_excursion_bps=7.0,
        percentile_60_favorable_excursion_bps=20.0,
        percentile_60_adverse_excursion_bps=9.0,
        percentile_85_favorable_excursion_bps=28.0,
        median_bars_to_peak_favorable=42,
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

    primary_policy, primary_strategy = researcher.synthesize_strategy(
        selected_pattern=pattern,
        version="discovery-test",
        direction="long",
    )
    inverse_policy, inverse_strategy = researcher.synthesize_strategy(
        selected_pattern=pattern.model_copy(update={"direction": "short", "regime": "bear_trend"}),
        version="discovery-test-inverse",
        direction="short",
    )

    assert primary_strategy.direction == "long_flat"
    assert inverse_strategy.direction == "inverse_research"
    assert primary_policy.policy_name == "baseline"
    assert inverse_policy.policy_name == "inverse"
    assert primary_strategy.strategy_config["trailing_stop_multiple"] == 0.75
    assert primary_strategy.strategy_config["take_profit_multiple"] >= 1.0
    assert primary_strategy.strategy_config["time_stop_bars"] == 42
