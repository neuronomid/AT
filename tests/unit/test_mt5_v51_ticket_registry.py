from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.mt5_v51_schemas import (
    MT5V51AccountSnapshot,
    MT5V51Bar,
    MT5V51BridgeCommand,
    MT5V51BridgeHealth,
    MT5V51BridgeSnapshot,
    MT5V51ExecutionAck,
    MT5V51LiveTicket,
    MT5V51SymbolSpec,
)
from execution.mt5_v51_ticket_registry import MT5V51TicketRegistry


def _snapshot(volume: Decimal) -> MT5V51BridgeSnapshot:
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    bars = []
    price = Decimal("60000")
    for index in range(20):
        end_at = base - timedelta(minutes=(19 - index))
        start_at = end_at - timedelta(minutes=1)
        close = price + (Decimal("12") * Decimal(str(index)))
        bars.append(
            MT5V51Bar(
                timeframe="1m",
                start_at=start_at,
                end_at=end_at,
                open_price=close - Decimal("5"),
                high_price=close + Decimal("8"),
                low_price=close - Decimal("9"),
                close_price=close,
                tick_volume=100 + index,
            )
        )
    return MT5V51BridgeSnapshot(
        server_time=base,
        received_at=base,
        symbol="BTCUSD",
        bid=Decimal("60200"),
        ask=Decimal("60202"),
        spread_bps=0.3,
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
        account=MT5V51AccountSnapshot(balance=Decimal("10000"), equity=Decimal("10010"), free_margin=Decimal("9500")),
        open_tickets=[
            MT5V51LiveTicket(
                ticket_id="1001",
                symbol="BTCUSD",
                side="long",
                volume_lots=volume,
                open_price=Decimal("60100"),
                current_price=Decimal("60200"),
                stop_loss=Decimal("60080"),
                take_profit=Decimal("60140"),
                unrealized_pnl_usd=Decimal("20"),
                opened_at=base - timedelta(minutes=5),
                magic_number=123,
                basket_id="BTCUSD-long-1",
            )
        ],
        health=MT5V51BridgeHealth(),
    )


def test_mt5_v51_ticket_registry_hydrates_ack_and_detects_partial_stage() -> None:
    registry = MT5V51TicketRegistry()
    command = MT5V51BridgeCommand(
        command_id="cmd-1",
        command_type="place_entry",
        symbol="BTCUSD",
        created_at=datetime(2026, 3, 12, 11, 59, tzinfo=timezone.utc),
        basket_id="BTCUSD-long-1",
        side="long",
        volume_lots=Decimal("0.20"),
        stop_loss=Decimal("60080"),
        take_profit=Decimal("60140"),
        comment="v51|BTCUSD-long-1|neutral",
        magic_number=123,
        reason="trend",
    )
    registry.register_pending_entry(
        command=command,
        plan_payload={
            "symbol": "BTCUSD",
            "side": "long",
            "volume_lots": 0.20,
            "entry_price": 60100,
            "stop_loss": 60080,
            "take_profit": 60140,
            "hard_take_profit": 60140,
            "soft_take_profit_1": 60110,
            "soft_take_profit_2": 60120,
            "r_distance_price": 20,
            "risk_amount_usd": 40,
            "basket_id": "BTCUSD-long-1",
            "magic_number": 123,
            "thesis_tags": ["trend"],
            "context_signature": "bull|bull|bull|tight",
            "followed_lessons": [],
        },
    )
    registry.record_ack(
        MT5V51ExecutionAck(
            command_id="cmd-1",
            status="applied",
            broker_time=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
            ticket_id="1001",
            fill_price=Decimal("60100"),
            fill_volume_lots=Decimal("0.20"),
        )
    )

    sync_result = registry.sync(_snapshot(Decimal("0.10")))
    ticket = registry.by_ticket_id("1001")

    assert ticket is not None
    assert ticket.partial_stage == 1
    assert registry.allowed_actions("1001") == ["hold", "close_ticket"]
    assert registry.scalp_final_ready(ticket) is True
    assert not sync_result.closed


def test_mt5_v51_ticket_registry_keeps_entry_unprotected_when_fill_ack_has_no_sl_tp() -> None:
    registry = MT5V51TicketRegistry()
    command = MT5V51BridgeCommand(
        command_id="cmd-2",
        command_type="place_entry",
        symbol="BTCUSD",
        created_at=datetime(2026, 3, 12, 11, 59, tzinfo=timezone.utc),
        basket_id="BTCUSD-long-2",
        side="long",
        volume_lots=Decimal("0.20"),
        comment="v51|BTCUSD-long-2|neutral",
        magic_number=456,
        reason="trend",
    )
    registry.register_pending_entry(
        command=command,
        plan_payload={
            "symbol": "BTCUSD",
            "side": "long",
            "volume_lots": 0.20,
            "entry_price": 60100,
            "stop_loss": 60080,
            "take_profit": 60140,
            "hard_take_profit": 60140,
            "soft_take_profit_1": 60110,
            "soft_take_profit_2": 60120,
            "r_distance_price": 20,
            "risk_amount_usd": 40,
            "basket_id": "BTCUSD-long-2",
            "magic_number": 456,
            "thesis_tags": ["trend"],
            "context_signature": "bull|bull|bull|tight",
            "followed_lessons": [],
        },
    )

    registry.record_ack(
        MT5V51ExecutionAck(
            command_id="cmd-2",
            status="applied",
            broker_time=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
            ticket_id="2002",
            fill_price=Decimal("60100"),
            fill_volume_lots=Decimal("0.20"),
        )
    )

    ticket = registry.by_ticket_id("2002")

    assert ticket is not None
    assert ticket.stop_loss is None
    assert ticket.take_profit is None
    assert ticket.initial_stop_loss == Decimal("60080")
    assert ticket.hard_take_profit == Decimal("60140")


def test_mt5_v51_ticket_registry_preserves_fixed_targets_after_fill() -> None:
    registry = MT5V51TicketRegistry()
    command = MT5V51BridgeCommand(
        command_id="cmd-2b",
        command_type="place_entry",
        symbol="BTCUSD",
        created_at=datetime(2026, 3, 12, 11, 59, tzinfo=timezone.utc),
        basket_id="BTCUSD-long-2b",
        side="long",
        volume_lots=Decimal("0.20"),
        stop_loss=Decimal("60080"),
        take_profit=Decimal("60140"),
        comment="v51|BTCUSD-long-2b|neutral",
        magic_number=457,
        reason="trend",
    )
    registry.register_pending_entry(
        command=command,
        plan_payload={
            "symbol": "BTCUSD",
            "side": "long",
            "volume_lots": 0.20,
            "entry_price": 60100,
            "stop_loss": 60080,
            "take_profit": 60140,
            "hard_take_profit": 60140,
            "soft_take_profit_1": 60110,
            "soft_take_profit_2": 60120,
            "r_distance_price": 20,
            "risk_amount_usd": 40,
            "basket_id": "BTCUSD-long-2b",
            "magic_number": 457,
            "thesis_tags": ["trend"],
            "context_signature": "bull|bull|bull|tight",
            "followed_lessons": [],
        },
    )

    registry.record_ack(
        MT5V51ExecutionAck(
            command_id="cmd-2b",
            status="applied",
            broker_time=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
            ticket_id="2003",
            fill_price=Decimal("60108"),
            fill_volume_lots=Decimal("0.20"),
        )
    )

    ticket = registry.by_ticket_id("2003")

    assert ticket is not None
    assert ticket.open_price == Decimal("60108")
    assert ticket.initial_stop_loss == Decimal("60080")
    assert ticket.hard_take_profit == Decimal("60140")
    assert ticket.soft_take_profit_1 == Decimal("60110")
    assert ticket.soft_take_profit_2 == Decimal("60120")
    assert ticket.r_distance_price == Decimal("28")


def test_mt5_v51_ticket_registry_matches_symbol_alias_and_uses_sane_fallback_r_distance() -> None:
    registry = MT5V51TicketRegistry()
    snapshot = _snapshot(Decimal("0.20")).model_copy(
        update={
            "symbol": "BTCUSD@",
            "open_tickets": [
                MT5V51LiveTicket(
                    ticket_id="3003",
                    symbol="BTCUSD@",
                    side="long",
                    volume_lots=Decimal("0.20"),
                    open_price=Decimal("60100"),
                    current_price=Decimal("60034"),
                    stop_loss=None,
                    take_profit=None,
                    unrealized_pnl_usd=Decimal("-66"),
                    opened_at=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
                    magic_number=789,
                    basket_id="BTCUSD@-long-3",
                )
            ],
        }
    )

    registry.sync(snapshot)
    ticket = registry.by_ticket_id("3003")

    assert ticket is not None
    assert registry.has_open_position("BTCUSD") is True
    assert ticket.r_distance_price > snapshot.symbol_spec.tick_size * Decimal("50")
    assert ticket.unrealized_r > -10.0


def test_mt5_v51_ticket_registry_waits_for_real_live_ticket_after_placeholder_ack() -> None:
    registry = MT5V51TicketRegistry()
    command = MT5V51BridgeCommand(
        command_id="cmd-3",
        command_type="place_entry",
        symbol="BTCUSD@",
        created_at=datetime(2026, 3, 12, 11, 59, tzinfo=timezone.utc),
        basket_id="BTCUSD@-long-3",
        side="long",
        volume_lots=Decimal("0.20"),
        stop_loss=Decimal("60080"),
        take_profit=Decimal("60140"),
        comment="v51|BTCUSD@-long-3|neutral",
        magic_number=789,
        reason="trend",
    )
    registry.register_pending_entry(
        command=command,
        plan_payload={
            "symbol": "BTCUSD@",
            "side": "long",
            "volume_lots": 0.20,
            "entry_price": 60100,
            "stop_loss": 60080,
            "take_profit": 60140,
            "hard_take_profit": 60140,
            "soft_take_profit_1": 60110,
            "soft_take_profit_2": 60120,
            "r_distance_price": 20,
            "risk_amount_usd": 40,
            "basket_id": "BTCUSD@-long-3",
            "magic_number": 789,
            "thesis_tags": ["trend"],
            "context_signature": "bull|bull|bull|tight",
            "followed_lessons": [],
        },
    )

    registry.record_ack(
        MT5V51ExecutionAck(
            command_id="cmd-3",
            status="applied",
            broker_time=datetime(2026, 3, 12, 12, 0, 2, tzinfo=timezone.utc),
            ticket_id="0",
            fill_price=Decimal("60103"),
            fill_volume_lots=Decimal("0.20"),
        )
    )

    assert registry.by_ticket_id("0") is None

    snapshot = _snapshot(Decimal("0.20")).model_copy(
        update={
            "symbol": "BTCUSD@",
            "open_tickets": [
                MT5V51LiveTicket(
                    ticket_id="3003",
                    symbol="BTCUSD@",
                    side="long",
                    volume_lots=Decimal("0.20"),
                    open_price=Decimal("60105"),
                    current_price=Decimal("60120"),
                    stop_loss=Decimal("60080"),
                    take_profit=Decimal("60140"),
                    unrealized_pnl_usd=Decimal("17"),
                    opened_at=datetime(2026, 3, 12, 12, 0, 5, tzinfo=timezone.utc),
                    magic_number=789,
                    basket_id="BTCUSD@-long-3",
                )
            ],
        }
    )

    sync_result = registry.sync(snapshot)
    ticket = registry.by_ticket_id("3003")

    assert ticket is not None
    assert not sync_result.closed
    assert len(sync_result.opened) == 1
    assert ticket.open_price == Decimal("60103")
    assert ticket.opened_at == datetime(2026, 3, 12, 12, 0, 2, tzinfo=timezone.utc)
    assert ticket.initial_stop_loss == Decimal("60080")
    assert ticket.hard_take_profit == Decimal("60140")
