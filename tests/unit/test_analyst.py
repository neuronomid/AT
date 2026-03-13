from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agents.analyst import AnalystAgent
from data.feature_engine import FeatureEngine
from data.schemas import AccountSnapshot, MarketSnapshot


def _features_from_prices(prices: list[float]) -> dict[str, float]:
    engine = FeatureEngine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    features: dict[str, float] = {}
    for index, price in enumerate(prices, start=1):
        features = engine.build_features(
            MarketSnapshot(
                symbol="ETH/USD",
                timestamp=base_time + timedelta(minutes=index),
                bid_price=Decimal(str(price - 0.05)),
                ask_price=Decimal(str(price + 0.05)),
                last_trade_price=Decimal(str(price)),
                last_trade_size=Decimal(str(100 + (index * 4))),
            )
        )
    return features


def test_analyst_emits_buy_when_uptrend_regime_and_edge_align() -> None:
    agent = AnalystAgent()
    features = _features_from_prices([2000 + (index * 3) for index in range(45)])

    decision = agent.analyze(
        market_snapshot=MarketSnapshot(
            symbol="ETH/USD",
            timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
            bid_price=Decimal("2086.95"),
            ask_price=Decimal("2087.05"),
            last_trade_price=Decimal("2087"),
        ),
        account_snapshot=AccountSnapshot(open_position_qty=Decimal("0")),
        features=features,
    )

    assert decision.action == "buy"
    assert decision.regime == "uptrend"
    assert decision.trade_plan is not None
    assert decision.expected_edge_bps is not None and decision.expected_edge_bps > 0


def test_analyst_emits_sell_when_downtrend_regime_and_edge_align() -> None:
    agent = AnalystAgent(allow_short_entries=True)
    features = _features_from_prices([2200 - (index * 3) for index in range(45)])

    decision = agent.analyze(
        market_snapshot=MarketSnapshot(
            symbol="ETH/USD",
            timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
            bid_price=Decimal("2112.95"),
            ask_price=Decimal("2113.05"),
            last_trade_price=Decimal("2113"),
        ),
        account_snapshot=AccountSnapshot(open_position_qty=Decimal("0")),
        features=features,
    )

    assert decision.action == "sell"
    assert decision.regime == "downtrend"
    assert decision.trade_plan is not None


def test_analyst_emits_exit_for_long_when_regime_flips_down() -> None:
    agent = AnalystAgent()
    decision = agent.analyze(
        market_snapshot=MarketSnapshot(
            symbol="ETH/USD",
            timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
            bid_price=Decimal("2000"),
            ask_price=Decimal("2000.1"),
            last_trade_price=Decimal("2000.05"),
        ),
        account_snapshot=AccountSnapshot(open_position_qty=Decimal("0.25")),
        features={
            "sample_count": 45.0,
            "spread_bps": 0.5,
            "return_3_bps": -10.0,
            "return_5_bps": -16.0,
            "return_15_bps": -26.0,
            "return_30_bps": -48.0,
            "volatility_5_bps": 10.0,
            "volatility_30_bps": 12.0,
            "zscore_30": -0.8,
            "trend_strength_bps": 28.0,
            "volume_ratio_5_30": 1.25,
        },
    )

    assert decision.action == "exit"
    assert decision.regime in {"downtrend", "chaotic"}
