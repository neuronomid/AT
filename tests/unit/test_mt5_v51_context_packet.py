from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v51_schemas import MT5V51AccountSnapshot, MT5V51Bar, MT5V51BridgeHealth, MT5V51BridgeSnapshot, MT5V51SymbolSpec
from data.schemas import LessonRecord, TradeReflection
from execution.mt5_v51_ticket_registry import MT5V51TicketRegistry
from runtime.mt5_v51_context_packet import MT5V51ContextBuilder


def _bars(*, timeframe: str, count: int, step_seconds: int, price_step: Decimal) -> list[MT5V51Bar]:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=step_seconds * count)
    bars: list[MT5V51Bar] = []
    price = Decimal("60000")
    for index in range(count):
        end_at = base + timedelta(seconds=step_seconds * index)
        start_at = end_at - timedelta(seconds=step_seconds)
        close = price + (price_step * Decimal(str(index)))
        if price_step >= 0:
            open_price = close - Decimal("14")
            high_price = close + Decimal("2")
            low_price = close - Decimal("16")
        else:
            open_price = close + Decimal("14")
            high_price = close + Decimal("16")
            low_price = close - Decimal("2")
        bars.append(
            MT5V51Bar(
                timeframe=timeframe,
                start_at=start_at,
                end_at=end_at,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close,
                tick_volume=100 + index,
            )
        )
    return bars


def _snapshot() -> MT5V51BridgeSnapshot:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V51BridgeSnapshot(
        server_time=now,
        received_at=now,
        symbol="BTCUSD",
        bid=Decimal("60300"),
        ask=Decimal("60302"),
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
        bars_20s=_bars(timeframe="20s", count=40, step_seconds=20, price_step=Decimal("4")),
        bars_1m=_bars(timeframe="1m", count=40, step_seconds=60, price_step=Decimal("10")),
        bars_5m=_bars(timeframe="5m", count=20, step_seconds=300, price_step=Decimal("18")),
        bars_15m=_bars(timeframe="15m", count=10, step_seconds=900, price_step=Decimal("25")),
        account=MT5V51AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V51BridgeHealth(),
    )


def _continuation_bars(*, timeframe: str, count: int, step_seconds: int) -> list[MT5V51Bar]:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=step_seconds * count)
    bars: list[MT5V51Bar] = []
    close = Decimal("60000")
    for index in range(count):
        end_at = base + timedelta(seconds=step_seconds * index)
        start_at = end_at - timedelta(seconds=step_seconds)
        if index < count - 5:
            close += Decimal("3")
            open_price = close - Decimal("2")
            high_price = close + Decimal("10")
            low_price = close - Decimal("18")
        else:
            close += Decimal("6")
            open_price = close - Decimal("4")
            high_price = close + Decimal("1")
            low_price = close - Decimal("5")
        bars.append(
            MT5V51Bar(
                timeframe=timeframe,
                start_at=start_at,
                end_at=end_at,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close,
                tick_volume=120 + index,
            )
        )
    return bars


def _bear_pause_after_impulse_bars(*, timeframe: str, count: int, step_seconds: int) -> list[MT5V51Bar]:
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=step_seconds * count)
    bars: list[MT5V51Bar] = []
    close = Decimal("60000")
    for index in range(count):
        end_at = base + timedelta(seconds=step_seconds * index)
        start_at = end_at - timedelta(seconds=step_seconds)
        if index < count - 4:
            close += Decimal("2")
            open_price = close - Decimal("3")
            high_price = close + Decimal("6")
            low_price = close - Decimal("8")
        elif index == count - 4:
            close -= Decimal("48")
            open_price = close + Decimal("34")
            high_price = close + Decimal("36")
            low_price = close - Decimal("18")
        elif index == count - 3:
            close -= Decimal("44")
            open_price = close + Decimal("30")
            high_price = close + Decimal("32")
            low_price = close - Decimal("16")
        elif index == count - 2:
            close -= Decimal("36")
            open_price = close + Decimal("24")
            high_price = close + Decimal("26")
            low_price = close - Decimal("14")
        else:
            close += Decimal("1")
            open_price = close - Decimal("1")
            high_price = close + Decimal("1")
            low_price = close - Decimal("1")
        bars.append(
            MT5V51Bar(
                timeframe=timeframe,
                start_at=start_at,
                end_at=end_at,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close,
                tick_volume=150 + index,
            )
        )
    return bars


def _reflections() -> list[TradeReflection]:
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    return [
        TradeReflection(
            reflection_id=str(index),
            symbol="BTCUSD",
            side="long",
            opened_at=base,
            closed_at=base + timedelta(minutes=1),
            bars_held=3,
            entry_price=Decimal("60000"),
            exit_price=Decimal("60010"),
            qty=Decimal("0.1"),
            realized_pnl_usd=Decimal("5"),
            realized_r=0.5,
            exit_reason="tp",
            thesis_tags=["trend"],
        )
        for index in range(5)
    ]


def _lessons() -> list[LessonRecord]:
    return [
        LessonRecord(
            lesson_id="avoid-match",
            category="v5_1_feedback",
            message="avoid-match",
            confidence=0.5,
            source="4",
            metadata={"polarity": "avoid", "context_signature": "bull|bull|bull|tight", "feedback_tags": ["respect_invalidation"]},
        )
    ] + [
        LessonRecord(
            lesson_id="reinforce-match",
            category="v5_1_feedback",
            message="reinforce-match",
            confidence=0.5,
            source="3",
            metadata={"polarity": "reinforce", "context_signature": "bull|bull|bull|tight", "feedback_tags": ["micro_confirm"]},
        )
    ] + [
        LessonRecord(
            lesson_id="avoid-old",
            category="v5_1_feedback",
            message="avoid-old",
            confidence=0.5,
            source="0",
            metadata={"polarity": "avoid", "context_signature": "bull|bull|bull|tight"},
        ),
        LessonRecord(
            lesson_id="avoid-mismatch",
            category="v5_1_feedback",
            message="avoid-mismatch",
            confidence=0.5,
            source="2",
            metadata={"polarity": "avoid", "context_signature": "bear|bear|bear|tight"},
        ),
        LessonRecord(
            lesson_id="missing-signature",
            category="v5_1_feedback",
            message="missing-signature",
            confidence=0.5,
            source="1",
            metadata={"polarity": "avoid"},
        ),
    ]


def test_mt5_v51_context_packet_uses_scalper_timeframes_only() -> None:
    builder = MT5V51ContextBuilder()
    snapshot = _snapshot()
    builder.observe_snapshot(snapshot.model_copy(update={"received_at": snapshot.received_at - timedelta(seconds=8), "server_time": snapshot.server_time - timedelta(seconds=8), "bid": Decimal("60290"), "ask": Decimal("60292"), "spread_bps": 0.28}))
    builder.observe_snapshot(snapshot.model_copy(update={"received_at": snapshot.received_at - timedelta(seconds=4), "server_time": snapshot.server_time - timedelta(seconds=4), "bid": Decimal("60296"), "ask": Decimal("60298"), "spread_bps": 0.29}))
    packet = builder.build_entry_packet(
        snapshot=snapshot,
        registry=MT5V51TicketRegistry(),
        risk_posture="neutral",
        reflections=_reflections(),
        lessons=_lessons(),
    )

    assert set(packet["timeframes"]) == {"20s", "1m", "5m"}
    assert "symbol_spec" not in packet
    assert "account" not in packet
    assert "open_exposure" not in packet
    assert packet["freshness"]["source_snapshot_age_bucket"] == "fresh"
    assert packet["microstructure"]["spread_percentile_1m"] is not None
    assert packet["microstructure"]["bid_drift_bps_10s"] > 0
    assert len(packet["recent_bars"]["20s"]) == 15
    assert len(packet["recent_bars"]["1m"]) == 12
    assert len(packet["feedback"]["recent_outcomes"]) == 4
    assert packet["feedback"]["avoid_tags"] == ["respect_invalidation"]
    assert packet["feedback"]["reinforce_tags"] == ["micro_confirm"]
    assert "distance_to_swing_high_20_bps" in packet["levels"]["1m"]
    assert "distance_to_swing_low_12_bps" in packet["levels"]["5m"]
    for summary in packet["timeframes"].values():
        assert "rsi_14" not in summary
        assert "adx_14" not in summary
        assert "ema_50" not in summary
        assert "range_expansion_ratio" not in summary
    assert packet["context_signature"] == "bull|bull|bull|tight"
    assert packet["timeframes"]["1m"]["long_trigger_ready"] is True
    assert packet["timeframes"]["1m"]["strong_bull_bars_last_3"] >= 2
    assert packet["timeframes"]["1m"]["consecutive_strong_bull_bars"] >= 2
    assert packet["trend_regime"]["tradeable"] is True
    assert packet["trend_regime"]["primary_direction"] == "bull"
    assert packet["trend_regime"]["market_state"] == "strong_bull"


def test_mt5_v51_context_packet_flags_stair_step_continuation_without_big_impulse() -> None:
    builder = MT5V51ContextBuilder()
    snapshot = _snapshot().model_copy(
        update={
            "bars_1m": _continuation_bars(timeframe="1m", count=30, step_seconds=60),
            "bars_20s": _continuation_bars(timeframe="20s", count=45, step_seconds=20),
        }
    )

    packet = builder.build_entry_packet(
        snapshot=snapshot,
        registry=MT5V51TicketRegistry(),
        risk_posture="neutral",
        reflections=[],
        lessons=[],
    )

    one_minute = packet["timeframes"]["1m"]
    assert one_minute["long_continuation_ready"] is True
    assert one_minute["long_continuation_score"] >= 7
    assert one_minute["strong_bull_bars_last_3"] == 0
    assert one_minute["long_trigger_ready"] is True
    assert packet["trend_regime"]["tradeable"] is True
    assert packet["trend_regime"]["entry_style"] == "stair_step_continuation"


def test_mt5_v51_context_packet_keeps_short_continuation_alive_through_tiny_pause() -> None:
    builder = MT5V51ContextBuilder()
    snapshot = _snapshot().model_copy(
        update={
            "bars_1m": _bear_pause_after_impulse_bars(timeframe="1m", count=30, step_seconds=60),
            "bars_20s": _bear_pause_after_impulse_bars(timeframe="20s", count=45, step_seconds=20),
        }
    )

    packet = builder.build_entry_packet(
        snapshot=snapshot,
        registry=MT5V51TicketRegistry(),
        risk_posture="neutral",
        reflections=[],
        lessons=[],
    )

    one_minute = packet["timeframes"]["1m"]
    assert one_minute["direction"] == "bull"
    assert one_minute["short_pause_after_impulse_ready"] is True
    assert one_minute["short_continuation_ready"] is True
    assert one_minute["short_trigger_ready"] is True
    assert packet["trend_regime"]["tradeable"] is True
    assert packet["trend_regime"]["primary_direction"] == "bear"
    assert packet["trend_regime"]["entry_style"] == "pause_after_impulse"
