from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.schemas import AccountSnapshot, LiveCandle
from runtime.context_packet import ContextPacketBuilder


def test_context_packet_adds_long_setup_summary() -> None:
    builder = ContextPacketBuilder(candle_lookback=20)
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    candles: list[LiveCandle] = []
    for idx in range(20):
        open_price = Decimal("2000") + Decimal(str(idx))
        close_price = open_price + Decimal("1.5")
        candles.append(
            LiveCandle(
                symbol="ETH/USD",
                start_at=base + timedelta(minutes=idx),
                end_at=base + timedelta(minutes=idx, seconds=59),
                open_price=open_price,
                high_price=close_price + Decimal("0.5"),
                low_price=open_price - Decimal("0.5"),
                close_price=close_price,
                volume=Decimal("2"),
                trade_count=3,
                spread_bps=2.0,
                vwap=open_price + Decimal("0.75"),
            )
        )

    packet = builder.build(
        candles=candles,
        account_snapshot=AccountSnapshot(
            equity=Decimal("10000"),
            cash=Decimal("10000"),
            buying_power=Decimal("20000"),
        ),
        open_trade=None,
        trades_this_hour=0,
        stale_age_seconds=3.0,
        latest_reflection=None,
        lessons=[],
    )

    decision_support = packet["decision_support"]
    assert decision_support["long_setup_score"] >= 4
    assert "bullish_ema_stack" in decision_support["long_setup_flags"]
    assert "price_above_vwap" in decision_support["long_setup_flags"]
    assert decision_support["warning_flags"] == []
