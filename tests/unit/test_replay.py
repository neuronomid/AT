from agents.analyst import AnalystAgent
from evaluation.replay import ReplayEngine


def _record(price: float, action_hint: str) -> dict[str, object]:
    if action_hint == "buy":
        features = {
            "sample_count": 45.0,
            "spread_bps": 5.0,
            "return_3_bps": 12.0,
            "return_5_bps": 16.0,
            "return_15_bps": 24.0,
            "return_30_bps": 40.0,
            "return_60_bps": 52.0,
            "return_240_bps": 80.0,
            "volatility_5_bps": 8.0,
            "volatility_30_bps": 10.0,
            "zscore_30": 0.6,
            "trend_strength_bps": 22.0,
            "volume_ratio_5_30": 1.3,
            "breakout_up_20_bps": 1.2,
            "atr_30_percentile": 0.4,
            "ema_slope_20_bps": 6.0,
            "ema_slope_60_bps": 4.0,
            "ema_slope_240_bps": 2.0,
            "ema_gap_60_240_bps": 10.0,
            "reference_price": price,
        }
    elif action_hint == "exit":
        features = {
            "sample_count": 45.0,
            "spread_bps": 5.0,
            "return_3_bps": -12.0,
            "return_5_bps": -15.0,
            "return_15_bps": -22.0,
            "return_30_bps": -36.0,
            "return_60_bps": -48.0,
            "return_240_bps": -72.0,
            "volatility_5_bps": 10.0,
            "volatility_30_bps": 12.0,
            "zscore_30": -0.7,
            "trend_strength_bps": 24.0,
            "volume_ratio_5_30": 1.25,
            "breakdown_20_bps": 1.4,
            "atr_30_percentile": 0.45,
            "ema_slope_20_bps": -6.0,
            "ema_slope_60_bps": -4.0,
            "ema_slope_240_bps": -2.0,
            "ema_gap_60_240_bps": -10.0,
            "reference_price": price,
        }
    else:
        features = {
            "sample_count": 45.0,
            "spread_bps": 5.0,
            "return_3_bps": 1.0,
            "return_5_bps": 2.0,
            "return_15_bps": 3.0,
            "return_30_bps": 4.0,
            "volatility_5_bps": 8.0,
            "volatility_30_bps": 10.0,
            "zscore_30": 0.1,
            "trend_strength_bps": 4.0,
            "volume_ratio_5_30": 0.9,
            "reference_price": price,
        }

    return {
        "record_type": "decision",
        "market_snapshot": {
            "symbol": "ETH/USD",
            "timestamp": "2026-01-01T00:00:00Z",
            "bid_price": str(price - 0.1),
            "ask_price": str(price + 0.1),
            "last_trade_price": str(price),
        },
        "features": features,
        "decision": {"action": "do_nothing"},
        "risk_decision": {"approved": False, "reason": "n/a", "allowed_notional_usd": "0"},
    }


def test_replay_engine_generates_positive_trade_metrics() -> None:
    records = [
        _record(100.0, "buy"),
        _record(102.0, "hold"),
        _record(104.0, "exit"),
    ]

    metrics = ReplayEngine().run(
        records,
        AnalystAgent(
            min_sample_count=45,
            min_regime_probability=0.52,
            regime_trend_15_bps=10.0,
            regime_trend_30_bps=20.0,
            entry_momentum_3_bps=6.0,
            entry_momentum_5_bps=8.0,
            max_volatility_5_bps=40.0,
            chaos_volatility_5_bps=100.0,
            min_trend_strength_bps=10.0,
            min_volume_ratio_5_30=1.05,
            min_entry_score=5,
            min_expected_edge_bps=0.0,
        ),
    )

    assert metrics.opened_trades == 1
    assert metrics.closed_trades == 1
    assert metrics.realized_pnl_bps > 0
    assert metrics.score > 0
