from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.feature_engine import FeatureEngine
from data.schemas import MarketSnapshot


def test_feature_engine_builds_rolling_momentum_and_volatility() -> None:
    engine = FeatureEngine()
    features = {}
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for index, price in enumerate([2000, 2001, 2002, 2004, 2006], start=1):
        features = engine.build_features(
            MarketSnapshot(
                symbol="ETH/USD",
                timestamp=base_time + timedelta(seconds=index),
                bid_price=Decimal(str(price - 0.2)),
                ask_price=Decimal(str(price + 0.2)),
                last_trade_price=Decimal(str(price)),
            )
        )

    assert features["sample_count"] == 5.0
    assert features["return_3_bps"] > 0
    assert features["return_5_bps"] > 0
    assert features["spread_bps"] > 0
