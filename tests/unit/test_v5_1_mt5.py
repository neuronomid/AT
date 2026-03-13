import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.v5_1_mt5 import (
    _continuation_override_decision,
    _entry_analysis_budget_seconds,
    _entry_command_expires_at,
    _fast_quote_entry_decision,
    _run_entry_protection_cycle,
    _run_fast_entry_cycle,
    _run_auto_scalp_cycle,
    _shutdown_flatten_open_tickets,
)
from brokers.mt5_v51 import MT5V51BridgeState
from data.mt5_v51_schemas import (
    MT5V51AccountSnapshot,
    MT5V51Bar,
    MT5V51BridgeHealth,
    MT5V51ExecutionAck,
    MT5V51LiveTicket,
    MT5V51BridgeSnapshot,
    MT5V51TicketRecord,
    MT5V51SymbolSpec,
)
from execution.mt5_v51_entry_planner import MT5V51EntryPlanner
from execution.mt5_v51_ticket_registry import MT5V51TicketRegistry
from memory.journal import Journal
from risk.mt5_v51_policy import MT5V51RiskArbiter, MT5V51RiskPostureEngine
from runtime.mt5_v51_context_packet import MT5V51ContextBuilder
from runtime.mt5_v51_microbars import MT5V51Synthetic20sBuilder


class _StaticEntryContextBuilder:
    def __init__(self, packet: dict[str, object]) -> None:
        self._packet = packet

    def build_entry_packet(self, **kwargs) -> dict[str, object]:
        return self._packet


def _snapshot(*, server_time: datetime, last_bar_end: datetime) -> MT5V51BridgeSnapshot:
    bars = []
    price = Decimal("60000")
    for index in range(20):
        end_at = last_bar_end - timedelta(minutes=(19 - index))
        start_at = end_at - timedelta(minutes=1)
        close = price + (Decimal("10") * Decimal(str(index)))
        bars.append(
            MT5V51Bar(
                timeframe="1m",
                start_at=start_at,
                end_at=end_at,
                open_price=close - Decimal("4"),
                high_price=close + Decimal("7"),
                low_price=close - Decimal("9"),
                close_price=close,
                tick_volume=100 + index,
            )
        )
    return MT5V51BridgeSnapshot(
        server_time=server_time,
        received_at=server_time,
        symbol="BTCUSD",
        bid=Decimal("60100"),
        ask=Decimal("60102"),
        spread_bps=0.4,
        symbol_spec=MT5V51SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.50"),
            tick_value=Decimal("1"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5"),
            stops_level_points=10,
        ),
        bars_1m=bars,
        bars_5m=[],
        bars_15m=[],
        account=MT5V51AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9500")),
        health=MT5V51BridgeHealth(),
    )


def _micro_bars(*, descending: bool = False) -> list[MT5V51Bar]:
    base = datetime(2026, 3, 12, 11, 50, tzinfo=timezone.utc)
    bars = []
    price = Decimal("60000")
    step = Decimal("-6") if descending else Decimal("6")
    for index in range(40):
        end_at = base + timedelta(seconds=20 * index)
        start_at = end_at - timedelta(seconds=20)
        close = price + (step * Decimal(str(index)))
        bars.append(
            MT5V51Bar(
                timeframe="20s",
                start_at=start_at,
                end_at=end_at,
                open_price=close - Decimal("2"),
                high_price=close + Decimal("3"),
                low_price=close - Decimal("4"),
                close_price=close,
                tick_volume=50 + index,
            )
        )
    return bars


def _ticket(*, partial_stage: int, current_volume: Decimal, current_price: Decimal, unrealized_r: float) -> MT5V51TicketRecord:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V51TicketRecord(
        ticket_id="1001",
        symbol="BTCUSD",
        side="long",
        basket_id="BTCUSD-long-1",
        original_volume_lots=Decimal("0.20"),
        current_volume_lots=current_volume,
        open_price=Decimal("60100"),
        current_price=current_price,
        stop_loss=Decimal("60080"),
        take_profit=Decimal("60120"),
        initial_stop_loss=Decimal("60080"),
        hard_take_profit=Decimal("60120"),
        soft_take_profit_1=Decimal("60110"),
        soft_take_profit_2=Decimal("60120"),
        r_distance_price=Decimal("20"),
        risk_amount_usd=Decimal("40"),
        partial_stage=partial_stage,
        highest_favorable_close=current_price,
        lowest_favorable_close=Decimal("60100"),
        thesis_tags=["trend"],
        opened_at=base - timedelta(minutes=1),
        last_seen_at=base,
        unrealized_pnl_usd=Decimal("20"),
        unrealized_r=unrealized_r,
    )


def _unprotected_ticket() -> MT5V51TicketRecord:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V51TicketRecord(
        ticket_id="2001",
        symbol="BTCUSD",
        side="long",
        basket_id="BTCUSD-long-2",
        original_volume_lots=Decimal("0.20"),
        current_volume_lots=Decimal("0.20"),
        open_price=Decimal("60102"),
        current_price=Decimal("60102"),
        stop_loss=None,
        take_profit=None,
        initial_stop_loss=Decimal("60080"),
        hard_take_profit=Decimal("60120"),
        soft_take_profit_1=Decimal("60111"),
        soft_take_profit_2=Decimal("60120"),
        r_distance_price=Decimal("22"),
        risk_amount_usd=Decimal("40"),
        partial_stage=0,
        highest_favorable_close=Decimal("60102"),
        lowest_favorable_close=Decimal("60102"),
        thesis_tags=["trend"],
        opened_at=base - timedelta(minutes=1),
        last_seen_at=base,
        unrealized_pnl_usd=Decimal("0"),
        unrealized_r=0.0,
    )


def test_v5_1_entry_analysis_budget_uses_full_timeout_window() -> None:
    assert _entry_analysis_budget_seconds(timeout_seconds=60) == 60.0


def test_v5_1_entry_command_expiry_uses_freshness_window() -> None:
    last_bar_end = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    snapshot = _snapshot(server_time=last_bar_end + timedelta(minutes=1), last_bar_end=last_bar_end)

    assert _entry_command_expires_at(snapshot, stale_after_seconds=5) == snapshot.server_time + timedelta(seconds=5)


def test_v5_1_continuation_override_promotes_clean_bull_run() -> None:
    packet = {
        "position_state": "flat",
        "freshness": {"source_snapshot_age_bucket": "fresh"},
        "quote": {"spread_bps": 3.8},
        "microstructure": {"spread_to_1m_atr_ratio": 0.22},
        "risk_posture": "neutral",
        "context_signature": "bull|bull|bear|tight",
        "timeframes": {
            "1m": {
                "long_trigger_ready": False,
                "long_continuation_ready": True,
                "ema_gap_bps": 3.4,
                "return_3_bps": 8.2,
                "return_5_bps": 11.5,
                "close_range_position": 0.81,
                "body_pct": 0.72,
                "latest_range_vs_atr": 0.41,
            },
            "20s": {
                "direction": "bull",
                "consecutive_bear_closes": 0,
                "consecutive_strong_bear_bars": 0,
                "short_trigger_ready": False,
            },
        },
        "recent_bars": {
            "1m": [
                {"open": 100.0, "close": 101.0},
                {"open": 101.0, "close": 102.0},
                {"open": 102.0, "close": 103.0},
                {"open": 103.0, "close": 104.0},
            ]
        },
    }

    decision = _continuation_override_decision(packet)

    assert decision is not None
    assert decision.action == "enter_long"
    assert decision.requested_risk_fraction == 0.0035
    assert "override" in decision.thesis_tags


def test_v5_1_continuation_override_accepts_aging_pause_after_impulse() -> None:
    packet = {
        "position_state": "flat",
        "freshness": {"source_snapshot_age_bucket": "aging"},
        "quote": {"spread_bps": 3.2},
        "microstructure": {"spread_to_1m_atr_ratio": 0.4821, "spread_percentile_1m": 20.75},
        "risk_posture": "neutral",
        "context_signature": "bull|bull|bull|tight",
        "timeframes": {
            "1m": {
                "direction": "flat",
                "long_trigger_ready": True,
                "long_continuation_ready": True,
                "long_pause_after_impulse_ready": True,
                "ema_gap_bps": 2.9,
                "return_3_bps": 12.4,
                "return_5_bps": 9.0,
                "close_range_position": 0.5,
                "body_pct": 0.0,
                "latest_range_vs_atr": 0.0,
            },
            "20s": {
                "direction": "bull",
                "long_trigger_ready": True,
                "long_continuation_ready": False,
                "consecutive_bear_closes": 0,
                "consecutive_strong_bear_bars": 0,
                "short_trigger_ready": False,
            },
        },
        "recent_bars": {
            "1m": [
                {"open": 71488.0, "close": 71535.5},
                {"open": 71532.5, "close": 71560.5},
                {"open": 71558.5, "close": 71566.5},
                {"open": 71537.0, "close": 71578.5},
                {"open": 71579.0, "close": 71627.0},
                {"open": 71625.5, "close": 71625.5},
            ]
        },
    }

    decision = _continuation_override_decision(packet)

    assert decision is not None
    assert decision.action == "enter_long"
    assert decision.requested_risk_fraction == 0.0035
    assert "override" in decision.thesis_tags


def test_v5_1_continuation_override_promotes_bear_pause_after_impulse() -> None:
    packet = {
        "position_state": "flat",
        "freshness": {"source_snapshot_age_bucket": "fresh"},
        "quote": {"spread_bps": 3.8},
        "microstructure": {"spread_to_1m_atr_ratio": 0.22},
        "risk_posture": "neutral",
        "context_signature": "bear|bear|bull|tight",
        "timeframes": {
            "1m": {
                "direction": "bull",
                "short_trigger_ready": True,
                "short_continuation_ready": True,
                "short_pause_after_impulse_ready": True,
                "ema_gap_bps": -3.1,
                "return_3_bps": -11.3,
                "return_5_bps": -6.3,
                "close_range_position": 1.0,
                "body_pct": 1.0,
                "latest_range_vs_atr": 0.04,
            },
            "20s": {
                "direction": "bear",
                "consecutive_bull_closes": 0,
                "consecutive_strong_bull_bars": 0,
                "long_trigger_ready": False,
            },
        },
        "recent_bars": {
            "1m": [
                {"open": 105.0, "close": 104.0},
                {"open": 104.0, "close": 100.0},
                {"open": 100.0, "close": 96.0},
                {"open": 95.0, "close": 96.1},
            ]
        },
    }

    decision = _continuation_override_decision(packet)

    assert decision is not None
    assert decision.action == "enter_short"
    assert decision.requested_risk_fraction == 0.0035
    assert "override" in decision.thesis_tags


def test_v5_1_fast_quote_entry_decision_detects_live_bull_acceleration() -> None:
    packet = {
        "position_state": "flat",
        "freshness": {"source_snapshot_age_bucket": "fresh"},
        "quote": {"bid": 100.0, "ask": 100.08, "spread_bps": 4.0},
        "microstructure": {
            "spread_to_1m_atr_ratio": 0.20,
            "bid_drift_bps_10s": 1.6,
            "ask_drift_bps_10s": 1.9,
            "mid_drift_bps_10s": 1.8,
        },
        "risk_posture": "neutral",
        "context_signature": "bull|bull|bull|tight",
        "timeframes": {
            "1m": {
                "long_trigger_ready": True,
                "long_continuation_ready": True,
                "ema_gap_bps": 4.2,
                "return_3_bps": 10.1,
                "return_5_bps": 14.4,
            },
            "20s": {
                "direction": "bull",
                "long_trigger_ready": True,
                "long_continuation_ready": True,
                "consecutive_bear_closes": 0,
                "consecutive_strong_bear_bars": 0,
                "short_trigger_ready": False,
            },
        },
        "recent_bars": {
            "1m": [{"close": 99.96}],
            "20s": [{"close": 99.99}],
        },
    }

    decision = _fast_quote_entry_decision(packet)

    assert decision is not None
    assert decision.action == "enter_long"
    assert decision.confidence == 0.72
    assert "fast_override" in decision.thesis_tags


def test_v5_1_fast_quote_entry_decision_accepts_aging_quotes() -> None:
    packet = {
        "position_state": "flat",
        "freshness": {"source_snapshot_age_bucket": "aging"},
        "quote": {"bid": 100.0, "ask": 100.08, "spread_bps": 4.0},
        "microstructure": {
            "spread_to_1m_atr_ratio": 0.20,
            "bid_drift_bps_10s": 1.6,
            "ask_drift_bps_10s": 1.9,
            "mid_drift_bps_10s": 1.8,
            "sample_count_10s": 7,
        },
        "risk_posture": "neutral",
        "context_signature": "bull|flat|bull|tight",
        "timeframes": {
            "1m": {
                "long_trigger_ready": True,
                "long_continuation_ready": True,
                "ema_gap_bps": 4.2,
                "return_3_bps": 10.1,
                "return_5_bps": 14.4,
            },
            "20s": {
                "direction": "flat",
                "long_trigger_ready": False,
                "long_continuation_ready": False,
                "consecutive_bear_closes": 0,
                "consecutive_strong_bear_bars": 0,
                "short_trigger_ready": False,
            },
        },
        "recent_bars": {
            "1m": [{"close": 99.96}],
            "20s": [],
        },
    }

    decision = _fast_quote_entry_decision(packet)

    assert decision is not None
    assert decision.action == "enter_long"
    assert decision.confidence == 0.72
    assert "fast_override" in decision.thesis_tags


def test_v5_1_fast_quote_entry_decision_detects_live_bull_acceleration_without_20s_history() -> None:
    packet = {
        "position_state": "flat",
        "freshness": {"source_snapshot_age_bucket": "fresh"},
        "quote": {"bid": 100.0, "ask": 100.08, "spread_bps": 4.0},
        "microstructure": {
            "spread_to_1m_atr_ratio": 0.20,
            "bid_drift_bps_10s": 1.6,
            "ask_drift_bps_10s": 1.9,
            "mid_drift_bps_10s": 1.8,
            "sample_count_10s": 7,
        },
        "risk_posture": "neutral",
        "context_signature": "bull|flat|bull|tight",
        "timeframes": {
            "1m": {
                "long_trigger_ready": True,
                "long_continuation_ready": True,
                "ema_gap_bps": 4.2,
                "return_3_bps": 10.1,
                "return_5_bps": 14.4,
            },
            "20s": {
                "direction": "flat",
                "long_trigger_ready": False,
                "long_continuation_ready": False,
                "consecutive_bear_closes": 0,
                "consecutive_strong_bear_bars": 0,
                "short_trigger_ready": False,
            },
        },
        "recent_bars": {
            "1m": [{"close": 99.96}],
            "20s": [],
        },
    }

    decision = _fast_quote_entry_decision(packet)

    assert decision is not None
    assert decision.action == "enter_long"
    assert decision.confidence == 0.72
    assert "fast_override" in decision.thesis_tags


def test_v5_1_fast_entry_cycle_queues_intrabar_command(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(
        update={
            "bars_20s": _micro_bars(),
            "bid": Decimal("60118"),
            "ask": Decimal("60120"),
            "spread_bps": 0.33,
        }
    )
    packet = {
        "position_state": "flat",
        "freshness": {"source_snapshot_age_bucket": "fresh"},
        "quote": {"bid": 60118.0, "ask": 60120.0, "spread_bps": 0.33},
        "microstructure": {
            "spread_to_1m_atr_ratio": 0.08,
            "bid_drift_bps_10s": 2.4,
            "ask_drift_bps_10s": 2.7,
            "mid_drift_bps_10s": 2.5,
        },
        "risk_posture": "neutral",
        "context_signature": "bull|bull|bull|tight",
        "timeframes": {
            "1m": {
                "long_trigger_ready": True,
                "long_continuation_ready": True,
                "ema_gap_bps": 5.1,
                "return_3_bps": 12.5,
                "return_5_bps": 18.0,
            },
            "20s": {
                "direction": "bull",
                "long_trigger_ready": True,
                "long_continuation_ready": True,
                "consecutive_bear_closes": 0,
                "consecutive_strong_bear_bars": 0,
                "short_trigger_ready": False,
            },
        },
        "recent_bars": {
            "1m": [{"close": 60098.0}],
            "20s": [{"close": 60111.0}],
        },
    }
    registry = MT5V51TicketRegistry()
    bridge_state = MT5V51BridgeState()
    journal = Journal(str(Path(tmp_path) / "events.jsonl"))
    settings = SimpleNamespace(
        v51_micro_min_warmup_bars=6,
        v51_stale_after_seconds=5,
        v51_bridge_id="bridge-test",
    )

    executed, signal_key = asyncio.run(
        _run_fast_entry_cycle(
            snapshot=snapshot,
            settings=settings,
            agent_name="mt5_v51_primary",
            event_journal=journal,
            store=None,
            registry=registry,
            planner=MT5V51EntryPlanner(),
            risk_arbiter=MT5V51RiskArbiter(),
            context_builder=_StaticEntryContextBuilder(packet),
            posture_engine=MT5V51RiskPostureEngine(),
            bridge_state=bridge_state,
            reflections=[],
            lessons=[],
            shadow_mode=False,
            logger=None,
            last_signal_key=None,
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=10))

    assert executed is True
    assert signal_key is not None
    assert len(commands) == 1
    assert commands[0].command_type == "place_entry"
    assert commands[0].side == "long"

    executed_again, signal_key_again = asyncio.run(
        _run_fast_entry_cycle(
            snapshot=snapshot,
            settings=settings,
            agent_name="mt5_v51_primary",
            event_journal=journal,
            store=None,
            registry=MT5V51TicketRegistry(),
            planner=MT5V51EntryPlanner(),
            risk_arbiter=MT5V51RiskArbiter(),
            context_builder=_StaticEntryContextBuilder(packet),
            posture_engine=MT5V51RiskPostureEngine(),
            bridge_state=MT5V51BridgeState(),
            reflections=[],
            lessons=[],
            shadow_mode=False,
            logger=None,
            last_signal_key=signal_key,
        )
    )

    assert executed_again is False
    assert signal_key_again == signal_key


def test_v5_1_fast_entry_cycle_queues_intrabar_command_during_microbar_warmup(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(
        update={
            "bid": Decimal("60118"),
            "ask": Decimal("60120"),
            "spread_bps": 0.33,
        }
    )
    packet = {
        "position_state": "flat",
        "freshness": {"source_snapshot_age_bucket": "fresh"},
        "quote": {"bid": 60118.0, "ask": 60120.0, "spread_bps": 0.33},
        "microstructure": {
            "spread_to_1m_atr_ratio": 0.08,
            "bid_drift_bps_10s": 2.4,
            "ask_drift_bps_10s": 2.7,
            "mid_drift_bps_10s": 2.5,
            "sample_count_10s": 8,
        },
        "risk_posture": "neutral",
        "context_signature": "bull|flat|bull|tight",
        "timeframes": {
            "1m": {
                "long_trigger_ready": True,
                "long_continuation_ready": True,
                "ema_gap_bps": 5.1,
                "return_3_bps": 12.5,
                "return_5_bps": 18.0,
            },
            "20s": {
                "direction": "flat",
                "long_trigger_ready": False,
                "long_continuation_ready": False,
                "consecutive_bear_closes": 0,
                "consecutive_strong_bear_bars": 0,
                "short_trigger_ready": False,
            },
        },
        "recent_bars": {
            "1m": [{"close": 60098.0}],
            "20s": [],
        },
    }
    registry = MT5V51TicketRegistry()
    bridge_state = MT5V51BridgeState()
    journal = Journal(str(Path(tmp_path) / "events.jsonl"))
    settings = SimpleNamespace(
        v51_micro_min_warmup_bars=6,
        v51_stale_after_seconds=5,
        v51_bridge_id="bridge-test",
    )

    executed, signal_key = asyncio.run(
        _run_fast_entry_cycle(
            snapshot=snapshot,
            settings=settings,
            agent_name="mt5_v51_primary",
            event_journal=journal,
            store=None,
            registry=registry,
            planner=MT5V51EntryPlanner(),
            risk_arbiter=MT5V51RiskArbiter(),
            context_builder=_StaticEntryContextBuilder(packet),
            posture_engine=MT5V51RiskPostureEngine(),
            bridge_state=bridge_state,
            reflections=[],
            lessons=[],
            shadow_mode=False,
            logger=None,
            last_signal_key=None,
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=10))

    assert executed is True
    assert signal_key is not None
    assert len(commands) == 1
    assert commands[0].command_type == "place_entry"
    assert commands[0].side == "long"


def test_v5_1_auto_scalp_cycle_queues_partial_and_breakeven(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(update={"bars_20s": _micro_bars(), "bid": Decimal("60109"), "ask": Decimal("60111")})
    registry = MT5V51TicketRegistry()
    ticket = _ticket(
        partial_stage=0,
        current_volume=Decimal("0.20"),
        current_price=Decimal("60112"),
        unrealized_r=0.6,
    ).model_copy(update={"opened_at": base - timedelta(minutes=3)})
    registry.seed([ticket])
    bridge_state = MT5V51BridgeState()
    journal = Journal(str(Path(tmp_path) / "events.jsonl"))

    asyncio.run(
        _run_auto_scalp_cycle(
            snapshot=snapshot,
            agent_name="mt5_v51_primary",
            event_journal=journal,
            store=None,
            registry=registry,
            planner=MT5V51EntryPlanner(),
            context_builder=MT5V51ContextBuilder(),
            posture_engine=MT5V51RiskPostureEngine(),
            bridge_state=bridge_state,
            reflections=[],
            lessons=[],
            min_hold_bars=2,
            shadow_mode=False,
            logger=None,
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=10))

    assert len(commands) == 2
    assert any(command.command_type == "close_ticket" and command.volume_lots == Decimal("0.10") for command in commands)
    assert any(
        command.command_type == "modify_ticket"
        and command.stop_loss == Decimal("60100")
        and command.take_profit == Decimal("60120")
        for command in commands
    )


def test_v5_1_auto_scalp_cycle_waits_for_minimum_hold_bars(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(update={"bars_20s": _micro_bars(), "bid": Decimal("60109"), "ask": Decimal("60111")})
    registry = MT5V51TicketRegistry()
    registry.seed([_ticket(partial_stage=0, current_volume=Decimal("0.20"), current_price=Decimal("60112"), unrealized_r=0.6)])
    bridge_state = MT5V51BridgeState()
    journal = Journal(str(Path(tmp_path) / "events.jsonl"))

    asyncio.run(
        _run_auto_scalp_cycle(
            snapshot=snapshot,
            agent_name="mt5_v51_primary",
            event_journal=journal,
            store=None,
            registry=registry,
            planner=MT5V51EntryPlanner(),
            context_builder=MT5V51ContextBuilder(),
            posture_engine=MT5V51RiskPostureEngine(),
            bridge_state=bridge_state,
            reflections=[],
            lessons=[],
            min_hold_bars=2,
            shadow_mode=False,
            logger=None,
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=10))

    assert commands == []


def test_v5_1_entry_protection_cycle_queues_broker_safe_modify(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(update={"bars_20s": _micro_bars()})
    registry = MT5V51TicketRegistry()
    registry.seed([_unprotected_ticket()])
    bridge_state = MT5V51BridgeState()
    journal = Journal(str(Path(tmp_path) / "events.jsonl"))

    queued = asyncio.run(
        _run_entry_protection_cycle(
            snapshot=snapshot,
            agent_name="mt5_v51_primary",
            event_journal=journal,
            store=None,
            registry=registry,
            planner=MT5V51EntryPlanner(),
            bridge_state=bridge_state,
            shadow_mode=False,
            logger=None,
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=10))

    assert queued is True
    assert len(commands) == 1
    assert commands[0].command_type == "modify_ticket"
    assert commands[0].ticket_id == "2001"
    assert commands[0].stop_loss is not None
    assert commands[0].take_profit is None
    assert commands[0].stop_loss < snapshot.bid


def test_v5_1_entry_protection_cycle_restores_tp_after_partial(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(update={"bars_20s": _micro_bars(), "bid": Decimal("60109"), "ask": Decimal("60111")})
    partial_ticket = _ticket(
        partial_stage=1,
        current_volume=Decimal("0.10"),
        current_price=Decimal("60112"),
        unrealized_r=0.6,
    ).model_copy(update={"take_profit": None, "opened_at": base - timedelta(minutes=3)})
    registry = MT5V51TicketRegistry()
    registry.seed([partial_ticket])
    bridge_state = MT5V51BridgeState()
    journal = Journal(str(Path(tmp_path) / "events.jsonl"))

    queued = asyncio.run(
        _run_entry_protection_cycle(
            snapshot=snapshot,
            agent_name="mt5_v51_primary",
            event_journal=journal,
            store=None,
            registry=registry,
            planner=MT5V51EntryPlanner(),
            bridge_state=bridge_state,
            shadow_mode=False,
            logger=None,
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=10))

    assert queued is True
    assert len(commands) == 1
    assert commands[0].command_type == "modify_ticket"
    assert commands[0].stop_loss == Decimal("60100")
    assert commands[0].take_profit == Decimal("60120")


def test_v5_1_shutdown_flatten_closes_open_ticket_and_records_reflection(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    ticket = _ticket(
        partial_stage=0,
        current_volume=Decimal("0.20"),
        current_price=Decimal("60108"),
        unrealized_r=0.4,
    ).model_copy(update={"ticket_id": "3001", "opened_at": base - timedelta(minutes=2), "last_seen_at": base})
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(
        update={
            "bars_20s": _micro_bars(),
            "open_tickets": [
                MT5V51LiveTicket(
                    ticket_id="3001",
                    symbol="BTCUSD",
                    side="long",
                    volume_lots=Decimal("0.20"),
                    open_price=Decimal("60100"),
                    current_price=Decimal("60108"),
                    stop_loss=Decimal("60080"),
                    take_profit=Decimal("60120"),
                    unrealized_pnl_usd=Decimal("16"),
                    opened_at=base - timedelta(minutes=2),
                    basket_id="BTCUSD-long-1",
                )
            ],
        }
    )
    registry = MT5V51TicketRegistry()
    registry.seed([ticket])
    bridge_state = MT5V51BridgeState()
    event_journal = Journal(str(Path(tmp_path) / "events.jsonl"))
    reflection_journal = Journal(str(Path(tmp_path) / "trade_reflections.jsonl"))
    settings = SimpleNamespace(v51_mt5_symbol="BTCUSD")

    async def _exercise() -> None:
        await bridge_state.publish_snapshot(snapshot)

        async def _simulate_broker() -> None:
            while True:
                commands = await bridge_state.poll_commands(limit=10)
                if commands:
                    command = commands[0]
                    await bridge_state.ack_command(
                        MT5V51ExecutionAck(
                            command_id=command.command_id,
                            status="applied",
                            broker_time=base + timedelta(seconds=2),
                            ticket_id=command.ticket_id,
                        )
                    )
                    await bridge_state.publish_snapshot(
                        snapshot.model_copy(
                            update={
                                "server_time": base + timedelta(seconds=2),
                                "open_tickets": [],
                            }
                        )
                    )
                    return
                await asyncio.sleep(0.01)

        broker_task = asyncio.create_task(_simulate_broker())
        await _shutdown_flatten_open_tickets(
            settings=settings,
            agent_name="mt5_v51_primary",
            event_journal=event_journal,
            reflection_journal=reflection_journal,
            store=None,
            registry=registry,
            bridge_state=bridge_state,
            context_builder=MT5V51ContextBuilder(),
            micro_bar_builder=MT5V51Synthetic20sBuilder("BTCUSD", warmup_bars=6),
            reflections=[],
            lessons=[],
            shadow_mode=False,
            logger=None,
        )
        await broker_task

    asyncio.run(_exercise())

    assert registry.all("BTCUSD") == []
    lines = Path(tmp_path, "trade_reflections.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert "snapshot_flat" in lines[0]
