from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agents.analyst import AnalystAgent
from data.feature_engine import FeatureEngine
from data.schemas import AccountSnapshot, MarketSnapshot


def test_analyst_emits_buy_when_momentum_is_positive() -> None:
    agent = AnalystAgent()
    engine = FeatureEngine()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    features = {}

    for index, price in enumerate([2000, 2002, 2004, 2007, 2010], start=1):
        features = engine.build_features(
            MarketSnapshot(
                symbol="ETH/USD",
                timestamp=base_time + timedelta(seconds=index),
                bid_price=Decimal(str(price - 0.1)),
                ask_price=Decimal(str(price + 0.1)),
                last_trade_price=Decimal(str(price)),
            )
        )

    decision = agent.analyze(
        market_snapshot=MarketSnapshot(
            symbol="ETH/USD",
            timestamp=base_time + timedelta(seconds=6),
            bid_price=Decimal("2009.9"),
            ask_price=Decimal("2010.1"),
            last_trade_price=Decimal("2010"),
        ),
        account_snapshot=AccountSnapshot(open_position_qty=Decimal("0")),
        features=features,
    )

    assert decision.action == "buy"
    assert decision.confidence >= 0.6


def test_analyst_emits_exit_when_position_loses_momentum() -> None:
    agent = AnalystAgent()
    decision = agent.analyze(
        market_snapshot=MarketSnapshot(
            symbol="ETH/USD",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            bid_price=Decimal("2000"),
            ask_price=Decimal("2000.2"),
            last_trade_price=Decimal("2000.1"),
        ),
        account_snapshot=AccountSnapshot(open_position_qty=Decimal("0.25")),
        features={
            "sample_count": 5.0,
            "spread_bps": 1.0,
            "return_3_bps": -10.0,
            "return_5_bps": -15.0,
            "volatility_5_bps": 10.0,
        },
    )

    assert decision.action == "exit"
