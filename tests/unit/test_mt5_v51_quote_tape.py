from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v51_schemas import MT5V51AccountSnapshot, MT5V51BridgeSnapshot, MT5V51SymbolSpec
from runtime.mt5_v51_quote_tape import MT5V51QuoteTape


def _snapshot(*, when: datetime, bid: str, ask: str, spread_bps: float) -> MT5V51BridgeSnapshot:
    return MT5V51BridgeSnapshot(
        server_time=when,
        received_at=when,
        symbol="BTCUSD",
        bid=Decimal(bid),
        ask=Decimal(ask),
        spread_bps=spread_bps,
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
    )


def test_mt5_v51_quote_tape_reports_recent_cost_and_drift() -> None:
    tape = MT5V51QuoteTape()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshots = [
        _snapshot(when=now - timedelta(seconds=8), bid="60290", ask="60292", spread_bps=0.28),
        _snapshot(when=now - timedelta(seconds=4), bid="60296", ask="60298", spread_bps=0.29),
        _snapshot(when=now, bid="60300", ask="60302", spread_bps=0.30),
    ]
    for snapshot in snapshots:
        tape.ingest(snapshot)

    payload = tape.build_payload(snapshot=snapshots[-1], one_minute_atr_bps=5.0, now=now)

    assert payload["spread_percentile_1m"] == 100.0
    assert payload["spread_to_1m_atr_ratio"] == 0.06
    assert payload["bid_drift_bps_10s"] > 0
    assert payload["ask_drift_bps_10s"] > 0
    assert payload["source_snapshot_age_bucket"] == "fresh"
