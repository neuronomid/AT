from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agents.analyst import AnalystAgent
from data.schemas import HistoricalBar
from evaluation.backtest import HistoricalBacktester
from risk.policy import RiskPolicy


def _bar(
    index: int,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float = 1000.0,
) -> HistoricalBar:
    return HistoricalBar(
        symbol="ETH/USD",
        timeframe="1Min",
        location="us",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index),
        open_price=Decimal(str(open_price)),
        high_price=Decimal(str(high_price)),
        low_price=Decimal(str(low_price)),
        close_price=Decimal(str(close_price)),
        volume=Decimal(str(volume)),
    )


def _policy() -> AnalystAgent:
    return AnalystAgent(
        allow_short_entries=True,
        min_sample_count=45,
        min_regime_probability=0.52,
        regime_trend_15_bps=8.0,
        regime_trend_30_bps=16.0,
        entry_momentum_3_bps=2.0,
        entry_momentum_5_bps=4.0,
        exit_momentum_3_bps=-2.0,
        exit_momentum_5_bps=-4.0,
        max_volatility_5_bps=50.0,
        chaos_volatility_5_bps=120.0,
        min_trend_strength_bps=6.0,
        min_volume_ratio_5_30=0.8,
        min_entry_score=4,
        exit_regime_probability=0.7,
        hard_exit_momentum_3_bps=6.0,
        hard_exit_momentum_5_bps=10.0,
        min_stop_loss_bps=8.0,
        max_stop_loss_bps=12.0,
        stop_loss_vol_multiplier=1.0,
        trailing_stop_multiple=0.5,
        time_stop_bars=8,
        min_expected_edge_bps=0.1,
    )


def _risk_policy() -> RiskPolicy:
    return RiskPolicy(
        min_confidence=0.6,
        max_risk_fraction=Decimal("0.5"),
        max_position_notional_usd=Decimal("1000"),
        max_spread_bps=Decimal("20"),
        max_trades_per_hour=20,
        cooldown_seconds=0,
    )


def test_historical_backtester_closes_profitable_long_trade() -> None:
    warmup = [_bar(index, 100 + index, 100 + index + 0.2, 100 + index - 0.2, 100 + index, volume=1000 + index * 5) for index in range(45)]
    trend = [
        _bar(45, 145.0, 145.6, 144.9, 145.5, volume=1800),
        _bar(46, 145.5, 146.5, 145.4, 146.2, volume=1900),
        _bar(47, 146.2, 147.6, 146.0, 147.4, volume=2100),
        _bar(48, 147.4, 148.8, 147.3, 148.5, volume=2200),
        _bar(49, 148.5, 149.4, 148.2, 149.0, volume=2300),
    ]
    bars = warmup + trend
    backtester = HistoricalBacktester(
        symbol="ETH/USD",
        starting_cash_usd=Decimal("1000"),
        risk_policy=_risk_policy(),
    )

    result = backtester.simulate(bars=bars, policy=_policy())

    assert result.metrics.closed_trades >= 1
    assert any(trade.side == "buy" for trade in result.trades)
    assert result.metrics.realized_pnl_bps != 0


def test_historical_backtester_supports_short_entries() -> None:
    warmup = [_bar(index, 200 - index, 200 - index + 0.2, 200 - index - 0.2, 200 - index, volume=1000 + index * 5) for index in range(45)]
    trend = [
        _bar(45, 155.0, 155.1, 154.2, 154.4, volume=1800),
        _bar(46, 154.4, 154.5, 153.5, 153.7, volume=1900),
        _bar(47, 153.7, 153.8, 152.4, 152.8, volume=2100),
        _bar(48, 152.8, 152.9, 151.6, 151.9, volume=2200),
        _bar(49, 151.9, 152.0, 150.8, 151.1, volume=2300),
    ]
    bars = warmup + trend
    backtester = HistoricalBacktester(
        symbol="ETH/USD",
        starting_cash_usd=Decimal("1000"),
        risk_policy=_risk_policy(),
    )

    result = backtester.simulate(bars=bars, policy=_policy())

    assert result.metrics.closed_trades >= 1
    assert any(trade.side == "sell" for trade in result.trades)
