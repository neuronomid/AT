import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.v6_0_mt5 import _run_manager_cycle
from app.v6_0_mt5 import (
    _entry_command_expires_at,
    _advance_manager_screenshot_state,
    _execution_snapshot,
    _manager_should_attach_raw_image,
    _run_entry_protection_cycle,
    _should_trigger_stop_loss_reversal,
)
from app.v6_0_config import V60Settings
from brokers.mt5_v60 import MT5V60BridgeState
from data.mt5_v60_schemas import (
    MT5V60AccountSnapshot,
    MT5V60BridgeHealth,
    MT5V60BridgeSnapshot,
    MT5V60EntryDecision,
    MT5V60ManagementDecisionBatch,
    MT5V60LiveTicket,
    MT5V60RiskDecision,
    MT5V60ScreenshotState,
    MT5V60SymbolSpec,
    MT5V60TicketRecord,
)
from execution.mt5_v60_entry_planner import MT5V60EntryPlanner
from execution.mt5_v60_immediate_entry import MT5V60ImmediateEntryBuilder
from execution.mt5_v60_ticket_registry import MT5V60TicketRegistry
from memory.journal import Journal
from risk.mt5_v60_policy import MT5V60RiskPostureEngine
from runtime.mt5_v60_context_packet import MT5V60ContextBuilder


def _ticket(*, analysis_mode: str, close_reason: str) -> MT5V60TicketRecord:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V60TicketRecord(
        ticket_id="1001",
        symbol="EURUSD@",
        side="long",
        basket_id="EURUSD-long-1",
        original_volume_lots=Decimal("0.10"),
        current_volume_lots=Decimal("0.10"),
        open_price=Decimal("70100"),
        current_price=Decimal("70080"),
        stop_loss=Decimal("70080"),
        take_profit=Decimal("70120"),
        initial_stop_loss=Decimal("70080"),
        hard_take_profit=Decimal("70120"),
        r_distance_price=Decimal("20"),
        risk_amount_usd=Decimal("50"),
        analysis_mode=analysis_mode,
        highest_favorable_close=Decimal("70100"),
        lowest_favorable_close=Decimal("70080"),
        opened_at=now,
        last_seen_at=now,
        last_close_reason=close_reason,
        unrealized_pnl_usd=Decimal("-50"),
        unrealized_r=-1.0,
    )


def _snapshot(*, bid: str = "70627", ask: str = "70653") -> MT5V60BridgeSnapshot:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return MT5V60BridgeSnapshot(
        server_time=now,
        received_at=now,
        symbol="EURUSD@",
        bid=Decimal(bid),
        ask=Decimal(ask),
        spread_bps=3.6,
        symbol_spec=MT5V60SymbolSpec(
            digits=2,
            point=Decimal("0.01"),
            tick_size=Decimal("0.01"),
            tick_value=Decimal("0.01"),
            volume_min=Decimal("0.01"),
            volume_step=Decimal("0.01"),
            volume_max=Decimal("5.00"),
            stops_level_points=2500,
        ),
        account=MT5V60AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10000"), free_margin=Decimal("9000")),
        health=MT5V60BridgeHealth(),
    )


def test_v6_0_manager_image_policy_attaches_only_new_fingerprint(tmp_path: Path) -> None:
    image_path = tmp_path / "latest.png"
    image_path.write_bytes(b"fake")
    state = MT5V60ScreenshotState(
        absolute_path=str(image_path),
        latest_screenshot_capture_ts=datetime.now(timezone.utc),
        latest_screenshot_fingerprint="new",
        last_manager_image_sent_fingerprint="old",
    )

    assert _manager_should_attach_raw_image(screenshot_state=state) is True

    same_state = state.model_copy(update={"last_manager_image_sent_fingerprint": "new"})
    assert _manager_should_attach_raw_image(screenshot_state=same_state) is False


def test_v6_0_manager_image_policy_only_advances_after_successful_delivery() -> None:
    captured_at = datetime.now(timezone.utc)
    state = MT5V60ScreenshotState(
        absolute_path="/tmp/latest.png",
        latest_screenshot_capture_ts=captured_at,
        latest_screenshot_fingerprint="new",
        last_manager_image_sent_fingerprint="old",
        cached_visual_context={"bias": "neutral"},
    )

    failed = _advance_manager_screenshot_state(
        screenshot_state=state,
        delivery_succeeded=False,
        visual_context_update={"bias": "bearish"},
    )
    assert failed.last_manager_image_sent_fingerprint == "old"
    assert failed.cached_visual_context == {"bias": "neutral"}

    delivered = _advance_manager_screenshot_state(
        screenshot_state=state,
        delivery_succeeded=True,
        visual_context_update={"bias": "bearish"},
    )
    assert delivered.last_manager_image_sent_fingerprint == "new"
    assert delivered.cached_visual_context == {"bias": "bearish"}
    assert delivered.cached_visual_context_capture_ts == captured_at


def test_v6_0_stop_loss_reversal_only_for_standard_entry_stopouts() -> None:
    assert _should_trigger_stop_loss_reversal(_ticket(analysis_mode="standard_entry", close_reason="stop_loss")) is True
    assert _should_trigger_stop_loss_reversal(_ticket(analysis_mode="stop_loss_reversal", close_reason="stop_loss")) is False
    assert _should_trigger_stop_loss_reversal(_ticket(analysis_mode="standard_entry", close_reason="take_profit")) is False


def test_v6_0_execution_snapshot_prefers_newer_matching_snapshot() -> None:
    source = _snapshot(bid="69840.5", ask="69866.5")
    latest = source.model_copy(
        update={
            "bid": Decimal("69847.5"),
            "ask": Decimal("69873.5"),
            "server_time": source.server_time + timedelta(seconds=52),
            "received_at": source.received_at + timedelta(seconds=52),
        }
    )

    resolved = _execution_snapshot(source, latest)

    assert resolved is latest


def test_v6_0_immediate_entry_builder_accepts_trade_even_when_take_profit_exceeds_one_r() -> None:
    builder = MT5V60ImmediateEntryBuilder()
    snapshot = _snapshot()
    decision = MT5V60EntryDecision(
        action="enter_short",
        confidence=0.66,
        rationale="Immediate short.",
        thesis_tags=["bearish_continuation"],
        requested_risk_fraction=0.003,
        stop_loss_price=Decimal("70950"),
        take_profit_price=Decimal("70200"),
        context_signature="bear|bear|bear|tight",
    )
    risk = MT5V60RiskDecision(approved=True, reason="ok", risk_fraction=0.003, risk_posture="neutral")

    outcome = builder.build(decision=decision, snapshot=snapshot, risk_decision=risk)

    assert outcome.command is not None
    assert outcome.plan_payload is not None
    assert outcome.command.command_type == "place_entry"
    assert outcome.command.side == "short"
    assert outcome.command.take_profit is None
    assert outcome.command.stop_loss is None
    assert outcome.command.metadata["hard_take_profit"] == 70200.0
    assert outcome.command.metadata["initial_stop_loss"] == 70950.0


def test_v6_0_immediate_entry_builder_raises_short_stop_to_current_mt5_boundary() -> None:
    builder = MT5V60ImmediateEntryBuilder()
    snapshot = _snapshot(bid="69847.5", ask="69873.5")
    decision = MT5V60EntryDecision(
        action="enter_short",
        confidence=0.72,
        rationale="Immediate short.",
        thesis_tags=["bearish_continuation"],
        requested_risk_fraction=0.004,
        stop_loss_price=Decimal("69898.5"),
        take_profit_price=Decimal("69782.5"),
        context_signature="bear|bear|bear|tight",
    )
    risk = MT5V60RiskDecision(approved=True, reason="ok", risk_fraction=0.004, risk_posture="neutral")

    outcome = builder.build(decision=decision, snapshot=snapshot, risk_decision=risk)

    assert outcome.command is not None
    assert outcome.command.stop_loss is None
    assert outcome.command.take_profit is None
    assert outcome.command.metadata["initial_stop_loss"] == 69898.51
    assert outcome.command.metadata["hard_take_profit"] == 69782.5


def test_v6_0_immediate_entry_builder_uses_balance_for_risk_amount() -> None:
    builder = MT5V60ImmediateEntryBuilder()
    snapshot = _snapshot()
    snapshot.account.balance = Decimal("10000")
    snapshot.account.equity = Decimal("8500")
    decision = MT5V60EntryDecision(
        action="enter_short",
        confidence=0.70,
        rationale="Immediate short.",
        thesis_tags=["bearish_continuation"],
        requested_risk_fraction=0.005,
        stop_loss_price=Decimal("70950"),
        take_profit_price=Decimal("70200"),
        context_signature="bear|bear|bear|tight",
    )
    risk = MT5V60RiskDecision(approved=True, reason="ok", risk_fraction=0.005, risk_posture="neutral")

    outcome = builder.build(decision=decision, snapshot=snapshot, risk_decision=risk)

    assert outcome.command is not None
    assert outcome.command.metadata["risk_amount_usd"] == 50.0


def test_v6_0_entry_command_expiry_is_based_on_queue_time_not_old_snapshot_time() -> None:
    snapshot = _snapshot()
    stale_snapshot = snapshot.model_copy(update={"server_time": datetime.now(timezone.utc) - timedelta(seconds=90)})

    expires_at = _entry_command_expires_at(stale_snapshot, stale_after_seconds=5)

    assert expires_at > datetime.now(timezone.utc)


def test_v6_0_entry_protection_cycle_queues_first_protection(tmp_path: Path) -> None:
    snapshot = _snapshot(bid="70060.00", ask="70065.00")
    registry = MT5V60TicketRegistry()
    now = snapshot.server_time
    ticket = MT5V60TicketRecord(
        ticket_id="1001",
        symbol="EURUSD@",
        side="short",
        basket_id="basket-1",
        original_volume_lots=Decimal("0.10"),
        current_volume_lots=Decimal("0.10"),
        open_price=Decimal("70089.50"),
        current_price=Decimal("70065.25"),
        stop_loss=None,
        take_profit=None,
        initial_stop_loss=Decimal("70138.56"),
        hard_take_profit=Decimal("70029.44"),
        r_distance_price=Decimal("49.06"),
        risk_amount_usd=Decimal("50"),
        analysis_mode="standard_entry",
        highest_favorable_close=Decimal("70065.25"),
        lowest_favorable_close=Decimal("70065.25"),
        metadata={"entry_submitted_without_broker_protection": True},
        opened_at=now,
        last_seen_at=now,
    )
    registry.seed([ticket])
    bridge_state = MT5V60BridgeState("mt5-v60-local")
    event_journal = Journal(str(tmp_path / "events.jsonl"))

    queued = asyncio.run(
        _run_entry_protection_cycle(
            snapshot=snapshot,
            settings=V60Settings(),
            agent_name="test",
            event_journal=event_journal,
            store=None,
            registry=registry,
            planner=MT5V60EntryPlanner(),
            bridge_state=bridge_state,
            shadow_mode=False,
            logger=logging.getLogger(__name__),
        )
    )

    assert queued is True
    commands = asyncio.run(bridge_state.poll_commands(limit=5))
    assert len(commands) == 1
    assert commands[0].command_type == "modify_ticket"
    assert commands[0].metadata["action"] == "attach_first_protection_auto"
    assert commands[0].stop_loss == Decimal("70138.56")
    assert commands[0].take_profit == Decimal("70029.44")


def test_v6_0_ticket_registry_marks_auto_first_protection_for_naked_fill() -> None:
    snapshot = _snapshot().model_copy(
        update={
            "open_tickets": [
                MT5V60LiveTicket(
                    ticket_id="1001",
                    symbol="EURUSD@",
                    side="long",
                    volume_lots=Decimal("0.10"),
                    open_price=Decimal("70100"),
                    current_price=Decimal("70120"),
                    stop_loss=Decimal("70080"),
                    take_profit=Decimal("70140"),
                    unrealized_pnl_usd=Decimal("20"),
                )
            ]
        }
    )
    registry = MT5V60TicketRegistry()
    now = snapshot.server_time
    registry.seed(
        [
            MT5V60TicketRecord(
                ticket_id="1001",
                symbol="EURUSD@",
                side="long",
                basket_id="basket-1",
                original_volume_lots=Decimal("0.10"),
                current_volume_lots=Decimal("0.10"),
                open_price=Decimal("70100"),
                current_price=Decimal("70100"),
                stop_loss=None,
                take_profit=None,
                initial_stop_loss=Decimal("70080"),
                hard_take_profit=Decimal("70140"),
                r_distance_price=Decimal("20"),
                risk_amount_usd=Decimal("50"),
                analysis_mode="standard_entry",
                highest_favorable_close=Decimal("70100"),
                lowest_favorable_close=Decimal("70100"),
                metadata={"entry_submitted_without_broker_protection": True},
                opened_at=now,
                last_seen_at=now,
            )
        ]
    )

    registry.sync(snapshot)
    updated = registry.by_ticket_id("1001")

    assert updated is not None
    assert updated.first_protection_attached is True
    assert updated.first_protection_review_pending is True


def test_v6_0_manager_cycle_enqueues_modify_ticket_for_hold_command_with_tp(tmp_path: Path) -> None:
    snapshot = _snapshot(bid="69883.0", ask="69909.0")
    now = snapshot.server_time
    registry = MT5V60TicketRegistry()
    registry.seed(
        [
            MT5V60TicketRecord(
                ticket_id="61690195",
                symbol="EURUSD@",
                side="long",
                basket_id="basket-1",
                original_volume_lots=Decimal("0.38"),
                current_volume_lots=Decimal("0.38"),
                open_price=Decimal("69905.5"),
                current_price=Decimal("69898.5"),
                stop_loss=Decimal("69750.0"),
                take_profit=None,
                initial_stop_loss=Decimal("69750.0"),
                hard_take_profit=Decimal("70109.0"),
                r_distance_price=Decimal("155.5"),
                risk_amount_usd=Decimal("57.99"),
                analysis_mode="standard_entry",
                highest_favorable_close=Decimal("69905.5"),
                lowest_favorable_close=Decimal("69898.5"),
                opened_at=now,
                last_seen_at=now,
            )
        ]
    )
    bridge_state = MT5V60BridgeState("mt5-v60-local")
    event_journal = Journal(str(tmp_path / "events.jsonl"))

    class _FakeManagerAgent:
        prompt_version = "test"

        async def analyze(self, packet, *, image_path=None):
            del packet, image_path
            return SimpleNamespace(
                decision_batch=MT5V60ManagementDecisionBatch.model_validate(
                    {
                        "decisions": [
                            {
                                "ticket_id": "61690195",
                                "confidence": 0.55,
                                "rationale": "Hold the trade but set the missing TP.",
                                "commands": [
                                    {
                                        "action": "hold",
                                        "stop_loss_price": "69750.0",
                                        "take_profit_price": "70109.0",
                                        "close_fraction": None,
                                    }
                                ],
                                "visual_context_update": None,
                            }
                        ]
                    }
                ),
                raw_response="{}",
                latency_ms=12,
            )

    asyncio.run(
        _run_manager_cycle(
            snapshot=snapshot,
            settings=V60Settings(),
            agent_name="test",
            event_journal=event_journal,
            store=None,
            registry=registry,
            planner=MT5V60EntryPlanner(),
            context_builder=MT5V60ContextBuilder(),
            posture_engine=MT5V60RiskPostureEngine(),
            bridge_state=bridge_state,
            reflections=[],
            lessons=[],
            screenshot_state=MT5V60ScreenshotState(),
            manager_agent=_FakeManagerAgent(),
            shadow_mode=False,
            logger=logging.getLogger(__name__),
        )
    )

    commands = asyncio.run(bridge_state.poll_commands(limit=5))
    assert len(commands) == 1
    assert commands[0].command_type == "modify_ticket"
    assert commands[0].ticket_id == "61690195"
    assert commands[0].take_profit == Decimal("70109.0")
    assert commands[0].stop_loss == Decimal("69750.0")
    assert commands[0].metadata["action"] == "modify_ticket"
    assert commands[0].metadata["source_action"] == "hold"
