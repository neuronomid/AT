from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agents.hmm_analyst import HMMRegimeAnalystAgent
from data.schemas import AccountSnapshot, HistoricalBar
from regime.hmm import HMMFeatureBuilder, MinuteBarAggregator, RegimeInference, RollingHMMRegimeEngine


def _minute_bar(index: int, price: float, volume: float = 1000.0) -> HistoricalBar:
    return HistoricalBar(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index),
        open_price=Decimal(str(price)),
        high_price=Decimal(str(price + 0.3)),
        low_price=Decimal(str(price - 0.3)),
        close_price=Decimal(str(price)),
        volume=Decimal(str(volume)),
    )


def _fifteen_minute_bar(index: int, price: float, volume: float = 5000.0) -> HistoricalBar:
    return HistoricalBar(
        symbol="ETH/USD",
        timeframe="15Min",
        location="derived",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index * 15),
        open_price=Decimal(str(price)),
        high_price=Decimal(str(price + 1.5)),
        low_price=Decimal(str(price - 1.0)),
        close_price=Decimal(str(price + 0.5)),
        volume=Decimal(str(volume)),
    )


class StubRegimeEngine:
    def __init__(self, inference: RegimeInference) -> None:
        self._inference = inference

    def update(self, _snapshot) -> RegimeInference:
        return self._inference


def test_minute_aggregator_emits_completed_bar_without_lookahead() -> None:
    aggregator = MinuteBarAggregator(symbol="ETH/USD", resample_minutes=15)
    completed = []
    for index in range(15):
        completed.extend(aggregator.update(_minute_bar(index, 100 + index).to_market_snapshot()))

    assert completed == []

    completed.extend(aggregator.update(_minute_bar(15, 200.0).to_market_snapshot()))

    assert len(completed) == 1
    assert completed[0].timestamp == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert completed[0].close_price == Decimal("114.0")


def test_hmm_feature_builder_returns_observation_after_warmup() -> None:
    builder = HMMFeatureBuilder()
    observation = None
    for index in range(50):
        observation = builder.update(_fifteen_minute_bar(index, 100 + index * 0.8, volume=5000 + index * 50))

    assert observation is not None
    assert "log_return_1" in observation.features
    assert "atr_percentile_30" in observation.features
    assert "ema_gap_8_21_bps" in observation.features


def test_label_state_mapping_is_deterministic() -> None:
    engine = RollingHMMRegimeEngine(symbol="ETH/USD")
    mapping = engine._label_states(
        {
            0: {
                "mean_return": 0.004,
                "mean_abs_return": 0.004,
                "mean_volatility": 0.0008,
                "mean_atr_percentile": 0.45,
            },
            1: {
                "mean_return": -0.003,
                "mean_abs_return": 0.003,
                "mean_volatility": 0.0009,
                "mean_atr_percentile": 0.5,
            },
            2: {
                "mean_return": 0.0002,
                "mean_abs_return": 0.0002,
                "mean_volatility": 0.0001,
                "mean_atr_percentile": 0.1,
            },
            3: {
                "mean_return": 0.0005,
                "mean_abs_return": 0.0005,
                "mean_volatility": 0.0035,
                "mean_atr_percentile": 0.95,
            },
        }
    )

    assert mapping[3] == "stress"
    assert mapping[2] == "quiet_range"
    assert mapping[0] == "bull_trend"
    assert mapping[1] == "bear_trend"


def test_hmm_analyst_blocks_when_model_not_ready() -> None:
    agent = HMMRegimeAnalystAgent(
        regime_engine=StubRegimeEngine(RegimeInference(model_ready=False)),
    )

    decision = agent.analyze(
        _minute_bar(0, 100.0).to_market_snapshot(),
        AccountSnapshot(cash=Decimal("1000"), equity=Decimal("1000")),
        {"spread_bps": 1.0},
    )

    assert decision.action == "do_nothing"
    assert "insufficient_completed_15m_history_for_hmm" in decision.entry_blockers


def test_hmm_analyst_enters_long_when_bull_regime_and_confirmations_align() -> None:
    inference = RegimeInference(
        regime="bull_trend",
        regime_probability=0.7,
        regime_probabilities={
            "bull_trend": 0.7,
            "bear_trend": 0.08,
            "quiet_range": 0.1,
            "stress": 0.12,
        },
        continuation_probabilities={
            "bull_trend": 0.66,
            "bear_trend": 0.08,
            "quiet_range": 0.1,
            "stress": 0.16,
        },
        atr_14_bps=22.0,
        atr_percentile=0.45,
        htf_bullish=True,
        model_ready=True,
    )
    agent = HMMRegimeAnalystAgent(
        regime_engine=StubRegimeEngine(inference),
        min_confirmation_count=6,
        min_entry_score=6,
        min_expected_edge_bps=0.1,
    )

    decision = agent.analyze(
        _minute_bar(0, 100.0).to_market_snapshot(),
        AccountSnapshot(cash=Decimal("1000"), equity=Decimal("1000")),
        {
            "spread_bps": 1.0,
            "return_3_bps": 6.0,
            "return_5_bps": 9.0,
            "trend_strength_bps": 12.0,
            "volume_ratio_5_30": 1.2,
            "breakout_up_20_bps": 2.0,
            "zscore_30": 1.0,
            "atr_14_bps": 20.0,
        },
    )

    assert decision.action == "buy"
    assert decision.regime == "bull_trend"
    assert decision.execution_plan is not None
    assert decision.execution_plan.planned_risk_usd is not None


def test_hmm_analyst_exits_long_on_stress_regime() -> None:
    inference = RegimeInference(
        regime="stress",
        regime_probability=0.62,
        regime_probabilities={
            "bull_trend": 0.15,
            "bear_trend": 0.12,
            "quiet_range": 0.11,
            "stress": 0.62,
        },
        continuation_probabilities={
            "bull_trend": 0.1,
            "bear_trend": 0.1,
            "quiet_range": 0.1,
            "stress": 0.7,
        },
        atr_14_bps=45.0,
        atr_percentile=0.9,
        htf_bullish=False,
        model_ready=True,
    )
    agent = HMMRegimeAnalystAgent(regime_engine=StubRegimeEngine(inference))

    decision = agent.analyze(
        _minute_bar(0, 100.0).to_market_snapshot(),
        AccountSnapshot(cash=Decimal("1000"), equity=Decimal("1000"), open_position_qty=Decimal("0.5")),
        {"spread_bps": 1.0, "return_3_bps": -4.0, "return_5_bps": -7.0},
    )

    assert decision.action == "exit"
    assert decision.regime == "stress"


def test_inverse_hmm_analyst_enters_short_when_bear_regime_and_confirmations_align() -> None:
    inference = RegimeInference(
        regime="bear_trend",
        regime_probability=0.72,
        regime_probabilities={
            "bull_trend": 0.08,
            "bear_trend": 0.72,
            "quiet_range": 0.08,
            "stress": 0.12,
        },
        continuation_probabilities={
            "bull_trend": 0.08,
            "bear_trend": 0.67,
            "quiet_range": 0.09,
            "stress": 0.16,
        },
        atr_14_bps=24.0,
        atr_percentile=0.5,
        htf_bearish=True,
        model_ready=True,
    )
    agent = HMMRegimeAnalystAgent(
        regime_engine=StubRegimeEngine(inference),
        trade_direction="short",
        strategy_family="inverse_hmm_regime_v3",
        min_confirmation_count=6,
        min_entry_score=6,
        min_expected_edge_bps=0.1,
    )

    decision = agent.analyze(
        _minute_bar(0, 100.0).to_market_snapshot(),
        AccountSnapshot(cash=Decimal("1000"), equity=Decimal("1000")),
        {
            "spread_bps": 1.0,
            "return_3_bps": -6.0,
            "return_5_bps": -9.0,
            "trend_strength_bps": 12.0,
            "volume_ratio_5_30": 1.2,
            "breakdown_20_bps": 2.0,
            "zscore_30": -1.0,
            "atr_14_bps": 22.0,
        },
    )

    assert decision.action == "sell"
    assert decision.regime == "bear_trend"
    assert decision.execution_plan is not None
    assert decision.execution_plan.planned_risk_usd is not None


def test_inverse_hmm_analyst_exits_short_on_bull_regime() -> None:
    inference = RegimeInference(
        regime="bull_trend",
        regime_probability=0.64,
        regime_probabilities={
            "bull_trend": 0.64,
            "bear_trend": 0.12,
            "quiet_range": 0.11,
            "stress": 0.13,
        },
        continuation_probabilities={
            "bull_trend": 0.7,
            "bear_trend": 0.08,
            "quiet_range": 0.08,
            "stress": 0.14,
        },
        atr_14_bps=28.0,
        atr_percentile=0.45,
        htf_bearish=False,
        model_ready=True,
    )
    agent = HMMRegimeAnalystAgent(
        regime_engine=StubRegimeEngine(inference),
        trade_direction="short",
        strategy_family="inverse_hmm_regime_v3",
    )

    decision = agent.analyze(
        _minute_bar(0, 100.0).to_market_snapshot(),
        AccountSnapshot(cash=Decimal("1000"), equity=Decimal("1000"), open_position_qty=Decimal("-0.5")),
        {"spread_bps": 1.0, "return_3_bps": 4.0, "return_5_bps": 7.0},
    )

    assert decision.action == "exit"
    assert decision.regime == "bull_trend"
