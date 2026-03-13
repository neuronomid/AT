import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agents.mt5_v51_entry_analyst import MT5V51EntryAnalysisResult
from app.v5_1_mt5 import (
    MT5V51PendingEntrySignal,
    _entry_analysis_budget_seconds,
    _entry_command_expires_at,
    _entry_signal_ready,
    _entry_target_open_at,
    _preflight_scalp_veto_reason,
    _run_entry_protection_cycle,
    _run_auto_scalp_cycle,
)
from brokers.mt5_v51 import MT5V51BridgeState
from data.mt5_v51_schemas import (
    MT5V51AccountSnapshot,
    MT5V51Bar,
    MT5V51BridgeHealth,
    MT5V51BridgeSnapshot,
    MT5V51EntryDecision,
    MT5V51TicketRecord,
    MT5V51SymbolSpec,
)
from execution.mt5_v51_entry_planner import MT5V51EntryPlanner
from execution.mt5_v51_ticket_registry import MT5V51TicketRegistry
from memory.journal import Journal
from risk.mt5_v51_policy import MT5V51RiskPostureEngine
from runtime.mt5_v51_context_packet import MT5V51ContextBuilder


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


def test_v5_1_entry_target_open_tracks_the_following_candle() -> None:
    last_bar_end = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    snapshot = _snapshot(server_time=last_bar_end + timedelta(seconds=1), last_bar_end=last_bar_end)

    assert _entry_target_open_at(snapshot, timeout_seconds=60) == last_bar_end + timedelta(minutes=1)
    assert _entry_analysis_budget_seconds(snapshot, timeout_seconds=60) == 59.0


def test_v5_1_entry_signal_waits_until_target_open() -> None:
    last_bar_end = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    signal = MT5V51PendingEntrySignal(
        symbol="BTCUSD",
        source_bar_end=last_bar_end,
        source_server_time=last_bar_end + timedelta(seconds=1),
        target_open_at=last_bar_end + timedelta(minutes=1),
        analysis_packet={"symbol": "BTCUSD"},
        source_risk_posture="neutral",
        result=MT5V51EntryAnalysisResult(
            decision=MT5V51EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"]),
            prompt="",
            raw_response="{}",
            latency_ms=1000,
        ),
    )

    before_open = _snapshot(server_time=last_bar_end + timedelta(seconds=59), last_bar_end=last_bar_end)
    at_open = _snapshot(server_time=last_bar_end + timedelta(minutes=1), last_bar_end=last_bar_end)

    assert _entry_signal_ready(signal, before_open) is False
    assert _entry_signal_ready(signal, at_open) is True


def test_v5_1_entry_command_expiry_uses_freshness_window() -> None:
    last_bar_end = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    snapshot = _snapshot(server_time=last_bar_end + timedelta(minutes=1), last_bar_end=last_bar_end)

    assert _entry_command_expires_at(snapshot, stale_after_seconds=5) == snapshot.server_time + timedelta(seconds=5)


def test_v5_1_preflight_blocks_when_20s_and_1m_flip_against_long() -> None:
    snapshot = _snapshot(
        server_time=datetime(2026, 3, 12, 12, 1, tzinfo=timezone.utc),
        last_bar_end=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
    ).model_copy(
        update={
            "bars_20s": _micro_bars(descending=True),
            "bars_1m": [bar.model_copy(update={"close_price": Decimal("60000") - (Decimal("10") * Decimal(str(index)))}) for index, bar in enumerate(_snapshot(
                server_time=datetime(2026, 3, 12, 12, 1, tzinfo=timezone.utc),
                last_bar_end=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
            ).bars_1m)],
        }
    )

    reason = _preflight_scalp_veto_reason(
        snapshot=snapshot,
        decision=MT5V51EntryDecision(action="enter_long", confidence=0.7, rationale="trend", thesis_tags=["trend"]),
        context_builder=MT5V51ContextBuilder(),
        minimum_micro_bars=30,
    )

    assert reason is not None
    assert "flipped" in reason


def test_v5_1_auto_scalp_cycle_queues_partial_and_breakeven(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(update={"bars_20s": _micro_bars()})
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
            shadow_mode=False,
            logger=None,
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=10))

    assert len(commands) == 2
    assert any(command.command_type == "close_ticket" and command.volume_lots == Decimal("0.10") for command in commands)
    assert any(command.command_type == "modify_ticket" and command.stop_loss == Decimal("60100") for command in commands)


def test_v5_1_auto_scalp_cycle_queues_final_close_after_partial(tmp_path) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot = _snapshot(
        server_time=base,
        last_bar_end=base - timedelta(minutes=1),
    ).model_copy(update={"bars_20s": _micro_bars()})
    registry = MT5V51TicketRegistry()
    registry.seed([_ticket(partial_stage=1, current_volume=Decimal("0.10"), current_price=Decimal("60122"), unrealized_r=1.1)])
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
            shadow_mode=False,
            logger=None,
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=10))

    assert len(commands) == 1
    assert commands[0].command_type == "close_ticket"
    assert commands[0].volume_lots == Decimal("0.10")


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
    assert commands[0].take_profit is not None
    assert commands[0].stop_loss < snapshot.bid
    assert commands[0].take_profit > snapshot.ask
