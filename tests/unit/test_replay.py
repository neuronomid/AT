from agents.analyst import AnalystAgent
from evaluation.replay import ReplayEngine


def _record(price: float, action_hint: str) -> dict[str, object]:
    if action_hint == "buy":
        features = {
            "sample_count": 5.0,
            "spread_bps": 5.0,
            "return_3_bps": 12.0,
            "return_5_bps": 16.0,
            "volatility_5_bps": 8.0,
            "reference_price": price,
        }
    elif action_hint == "exit":
        features = {
            "sample_count": 5.0,
            "spread_bps": 5.0,
            "return_3_bps": -12.0,
            "return_5_bps": -15.0,
            "volatility_5_bps": 10.0,
            "reference_price": price,
        }
    else:
        features = {
            "sample_count": 5.0,
            "spread_bps": 5.0,
            "return_3_bps": 1.0,
            "return_5_bps": 2.0,
            "volatility_5_bps": 8.0,
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

    metrics = ReplayEngine().run(records, AnalystAgent())

    assert metrics.opened_trades == 1
    assert metrics.closed_trades == 1
    assert metrics.realized_pnl_bps > 0
    assert metrics.score > 0
