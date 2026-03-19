from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v60_schemas import (
    MT5V60AccountSnapshot,
    MT5V60Bar,
    MT5V60BridgeHealth,
    MT5V60BridgeSnapshot,
    MT5V60ChartScreenshot,
    MT5V60ScreenshotState,
    MT5V60SymbolSpec,
)
from execution.mt5_v60_ticket_registry import MT5V60TicketRegistry
from runtime.mt5_v60_context_packet import MT5V60ContextBuilder


def _bars(*, timeframe: str, count: int, step_seconds: int, price_step: Decimal) -> list[MT5V60Bar]:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=step_seconds * count)
    bars: list[MT5V60Bar] = []
    price = Decimal("70000")
    for index in range(count):
        end_at = base + timedelta(seconds=step_seconds * index)
        start_at = end_at - timedelta(seconds=step_seconds)
        close = price + (price_step * Decimal(str(index)))
        bars.append(
            MT5V60Bar(
                timeframe=timeframe,
                start_at=start_at,
                end_at=end_at,
                open_price=close - Decimal("4"),
                high_price=close + Decimal("6"),
                low_price=close - Decimal("8"),
                close_price=close,
                tick_volume=100 + index,
            )
        )
    return bars


def _snapshot() -> MT5V60BridgeSnapshot:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V60BridgeSnapshot(
        server_time=now,
        received_at=now,
        symbol="BTCUSD@",
        bid=Decimal("70100"),
        ask=Decimal("70102"),
        spread_bps=0.3,
        symbol_spec=MT5V60SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.50"),
            tick_value=Decimal("1.00"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5.00"),
            stops_level_points=10,
        ),
        bars_1m=_bars(timeframe="1m", count=20, step_seconds=60, price_step=Decimal("4")),
        bars_2m=_bars(timeframe="2m", count=20, step_seconds=120, price_step=Decimal("7")),
        bars_3m=_bars(timeframe="3m", count=24, step_seconds=180, price_step=Decimal("10")),
        bars_5m=_bars(timeframe="5m", count=14, step_seconds=300, price_step=Decimal("12")),
        chart_screenshot=MT5V60ChartScreenshot(
            relative_path="AT_V60/screenshots/latest.png",
            fingerprint="abc123",
            captured_at=now - timedelta(seconds=20),
            capture_ok=True,
            message="ok",
        ),
        account=MT5V60AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V60BridgeHealth(),
    )


def test_mt5_v60_context_packet_uses_v6_timeframes() -> None:
    builder = MT5V60ContextBuilder()
    snapshot = _snapshot()
    registry = MT5V60TicketRegistry()
    state = MT5V60ScreenshotState(
        absolute_path="/tmp/latest.png",
        latest_screenshot_capture_ts=snapshot.chart_screenshot.captured_at,
        latest_screenshot_fingerprint="abc123",
    )

    packet = builder.build_entry_packet(snapshot=snapshot, registry=registry, risk_posture="neutral", screenshot_state=state)

    assert set(packet["timeframes"]) == {"1m", "2m", "3m", "5m"}
    assert len(packet["recent_bars"]["3m"]) == 20
    assert len(packet["recent_bars"]["1m"]) == 10
    assert len(packet["recent_bars"]["2m"]) == 10
    assert "20s" not in str(packet)


def test_mt5_v60_manager_packet_includes_cached_visual_context() -> None:
    builder = MT5V60ContextBuilder()
    snapshot = _snapshot()
    registry = MT5V60TicketRegistry()
    state = MT5V60ScreenshotState(
        absolute_path="/tmp/latest.png",
        latest_screenshot_capture_ts=snapshot.chart_screenshot.captured_at,
        latest_screenshot_fingerprint="abc123",
        last_manager_image_sent_fingerprint="abc123",
        cached_visual_context={"bias": "bullish"},
        cached_visual_context_capture_ts=snapshot.chart_screenshot.captured_at,
    )

    packet = builder.build_manager_packet(
        snapshot=snapshot,
        registry=registry,
        allowed_actions={},
        risk_posture="neutral",
        reflections=[],
        lessons=[],
        screenshot_state=state,
        include_raw_screenshot=False,
    )

    assert packet["manager_context"]["image_attached"] is False
    assert packet["manager_context"]["screenshot"]["cached_visual_context"] == {"bias": "bullish"}
