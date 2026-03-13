from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v51_schemas import MT5V51AccountSnapshot, MT5V51BridgeHealth, MT5V51BridgeSnapshot, MT5V51SymbolSpec
from runtime.mt5_v51_microbars import MT5V51Synthetic20sBuilder


def _snapshot(*, elapsed_seconds: int, midpoint: Decimal) -> MT5V51BridgeSnapshot:
    server_time = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc).replace(microsecond=0)
    server_time = server_time + timedelta(seconds=elapsed_seconds)
    return MT5V51BridgeSnapshot(
        server_time=server_time,
        received_at=server_time,
        symbol="BTCUSD",
        bid=midpoint - Decimal("1"),
        ask=midpoint + Decimal("1"),
        spread_bps=0.3,
        symbol_spec=MT5V51SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.50"),
            tick_value=Decimal("1.00"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5.00"),
            stops_level_points=10,
        ),
        account=MT5V51AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V51BridgeHealth(),
    )


def test_mt5_v51_microbars_close_on_20_second_boundaries() -> None:
    builder = MT5V51Synthetic20sBuilder("BTCUSD", warmup_bars=2)
    builder.enrich_snapshot(_snapshot(elapsed_seconds=1, midpoint=Decimal("60000")))
    builder.enrich_snapshot(_snapshot(elapsed_seconds=10, midpoint=Decimal("60012")))
    enriched = builder.enrich_snapshot(_snapshot(elapsed_seconds=21, midpoint=Decimal("60020")))

    assert len(enriched.bars_20s) == 1
    bar = enriched.bars_20s[0]
    assert bar.start_at == datetime(2026, 3, 12, 12, 0, 0, tzinfo=timezone.utc)
    assert bar.end_at == datetime(2026, 3, 12, 12, 0, 20, tzinfo=timezone.utc)
    assert bar.open_price == Decimal("60000")
    assert bar.high_price == Decimal("60012")
    assert bar.low_price == Decimal("60000")
    assert bar.close_price == Decimal("60012")
    assert bar.tick_volume == 2
    assert bar.volume == Decimal("2")


def test_mt5_v51_microbars_do_not_fabricate_ticks_across_snapshot_gaps() -> None:
    builder = MT5V51Synthetic20sBuilder("BTCUSD", warmup_bars=2)
    builder.enrich_snapshot(_snapshot(elapsed_seconds=1, midpoint=Decimal("60000")))
    builder.enrich_snapshot(_snapshot(elapsed_seconds=3, midpoint=Decimal("60002")))
    builder.enrich_snapshot(_snapshot(elapsed_seconds=19, midpoint=Decimal("60006")))
    enriched = builder.enrich_snapshot(_snapshot(elapsed_seconds=21, midpoint=Decimal("60008")))

    bar = enriched.bars_20s[0]
    assert bar.tick_volume == 3
    assert bar.volume == Decimal("3")
    assert bar.close_price == Decimal("60006")


def test_mt5_v51_microbars_trim_lookback_and_track_warmup() -> None:
    builder = MT5V51Synthetic20sBuilder("BTCUSD", max_bars=3, warmup_bars=2)
    for elapsed_seconds, midpoint in [(1, 60000), (21, 60010), (41, 60020), (61, 60030), (81, 60040)]:
        builder.enrich_snapshot(_snapshot(elapsed_seconds=elapsed_seconds, midpoint=Decimal(str(midpoint))))

    assert builder.warmup_complete() is True
    assert builder.closed_bar_count() == 3
