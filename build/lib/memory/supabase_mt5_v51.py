from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from data.mt5_v51_schemas import (
    MT5V51BridgeCommand,
    MT5V51BridgeSnapshot,
    MT5V51ExecutionAck,
    MT5V51TicketRecord,
)
from data.schemas import LessonRecord, TradeReflection


class SupabaseMT5V51Store:
    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._last_prune_at: datetime | None = None

    def insert_mt5_v51_bridge_snapshot(self, *, agent_name: str, snapshot: MT5V51BridgeSnapshot) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_v51_bridge_snapshots (
                      bridge_id, agent_name, symbol, server_time, received_at, spread_bps,
                      snapshot_payload, health_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        snapshot.bridge_id,
                        agent_name,
                        snapshot.symbol,
                        snapshot.server_time,
                        snapshot.received_at,
                        snapshot.spread_bps,
                        Jsonb(snapshot.model_dump(mode="json")),
                        Jsonb(snapshot.health.model_dump(mode="json")),
                    ),
                )
                snapshot_id = str(cur.fetchone()["id"])
                self._maybe_prune_snapshots(cur)
                return snapshot_id

    def insert_mt5_v51_runtime_decision(
        self,
        *,
        agent_name: str,
        decision_kind: str,
        symbol: str,
        action: str,
        confidence: float,
        rationale: str,
        risk_posture: str,
        risk_approved: bool | None,
        risk_reason: str | None,
        context_payload: dict[str, Any],
        decision_payload: dict[str, Any],
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_v51_runtime_decisions (
                      agent_name, decision_kind, symbol, action, confidence, rationale,
                      risk_posture, risk_approved, risk_reason, context_payload, decision_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        agent_name,
                        decision_kind,
                        symbol,
                        action,
                        confidence,
                        rationale,
                        risk_posture,
                        risk_approved,
                        risk_reason,
                        Jsonb(context_payload),
                        Jsonb(decision_payload),
                    ),
                )
                return str(cur.fetchone()["id"])

    def insert_mt5_v51_bridge_command(
        self,
        *,
        agent_name: str,
        command: MT5V51BridgeCommand,
        bridge_id: str,
        status: str = "queued",
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_v51_bridge_commands (
                      command_id, bridge_id, agent_name, symbol, command_type, status,
                      ticket_id, basket_id, created_at, expires_at, reason, command_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (command_id) do update
                      set status = excluded.status,
                          ticket_id = excluded.ticket_id,
                          basket_id = excluded.basket_id,
                          expires_at = excluded.expires_at,
                          reason = excluded.reason,
                          command_payload = excluded.command_payload
                    returning id
                    """,
                    (
                        command.command_id,
                        bridge_id,
                        agent_name,
                        command.symbol,
                        command.command_type,
                        status,
                        command.ticket_id,
                        command.basket_id,
                        command.created_at,
                        command.expires_at,
                        command.reason,
                        Jsonb(command.model_dump(mode="json")),
                    ),
                )
                return str(cur.fetchone()["id"])

    def insert_mt5_v51_bridge_ack(self, *, agent_name: str, ack: MT5V51ExecutionAck) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_v51_bridge_acks (
                      command_id, agent_name, ack_status, ticket_id, broker_time, message, ack_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        ack.command_id,
                        agent_name,
                        ack.status,
                        ack.ticket_id,
                        ack.broker_time,
                        ack.message,
                        Jsonb(ack.model_dump(mode="json")),
                    ),
                )
                ack_id = str(cur.fetchone()["id"])
                cur.execute(
                    """
                    update public.mt5_v51_bridge_commands
                    set status = %s,
                        ticket_id = coalesce(%s, ticket_id),
                        ack_payload = %s
                    where command_id = %s
                    """,
                    (
                        ack.status,
                        ack.ticket_id,
                        Jsonb(ack.model_dump(mode="json")),
                        ack.command_id,
                    ),
                )
                return ack_id

    def insert_mt5_v51_trade_reflection(
        self,
        *,
        agent_name: str,
        reflection: TradeReflection,
        ticket_id: str | None = None,
        basket_id: str | None = None,
        risk_posture: str | None = None,
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_v51_trade_reflections (
                      reflection_id, agent_name, symbol, side, ticket_id, basket_id, risk_posture,
                      opened_at, closed_at, realized_pnl_usd, realized_r, exit_reason, reflection_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (reflection_id) do update
                      set realized_pnl_usd = excluded.realized_pnl_usd,
                          realized_r = excluded.realized_r,
                          exit_reason = excluded.exit_reason,
                          reflection_payload = excluded.reflection_payload
                    returning id
                    """,
                    (
                        reflection.reflection_id,
                        agent_name,
                        reflection.symbol,
                        reflection.side,
                        ticket_id,
                        basket_id,
                        risk_posture,
                        reflection.opened_at,
                        reflection.closed_at,
                        reflection.realized_pnl_usd,
                        reflection.realized_r,
                        reflection.exit_reason,
                        Jsonb(reflection.model_dump(mode="json")),
                    ),
                )
                return str(cur.fetchone()["id"])

    def upsert_mt5_v51_ticket_state(self, ticket: MT5V51TicketRecord) -> str:
        payload = ticket.model_dump(mode="json")
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_v51_ticket_state (
                      ticket_id, symbol, side, basket_id, magic_number, entry_command_id,
                      is_open, opened_at, last_seen_at, partial_stage, current_price,
                      current_volume_lots, unrealized_pnl_usd, unrealized_r, ticket_payload
                    )
                    values (%(ticket_id)s, %(symbol)s, %(side)s, %(basket_id)s, %(magic_number)s, %(entry_command_id)s,
                            %(is_open)s, %(opened_at)s, %(last_seen_at)s, %(partial_stage)s, %(current_price)s,
                            %(current_volume_lots)s, %(unrealized_pnl_usd)s, %(unrealized_r)s, %(ticket_payload)s)
                    on conflict (ticket_id) do update
                      set symbol = excluded.symbol,
                          side = excluded.side,
                          basket_id = excluded.basket_id,
                          magic_number = excluded.magic_number,
                          entry_command_id = excluded.entry_command_id,
                          is_open = excluded.is_open,
                          opened_at = excluded.opened_at,
                          last_seen_at = excluded.last_seen_at,
                          partial_stage = excluded.partial_stage,
                          current_price = excluded.current_price,
                          current_volume_lots = excluded.current_volume_lots,
                          unrealized_pnl_usd = excluded.unrealized_pnl_usd,
                          unrealized_r = excluded.unrealized_r,
                          ticket_payload = excluded.ticket_payload
                    returning id
                    """,
                    {
                        "ticket_id": ticket.ticket_id,
                        "symbol": ticket.symbol,
                        "side": ticket.side,
                        "basket_id": ticket.basket_id,
                        "magic_number": ticket.magic_number,
                        "entry_command_id": ticket.entry_command_id,
                        "is_open": ticket.is_open,
                        "opened_at": ticket.opened_at,
                        "last_seen_at": ticket.last_seen_at,
                        "partial_stage": ticket.partial_stage,
                        "current_price": ticket.current_price,
                        "current_volume_lots": ticket.current_volume_lots,
                        "unrealized_pnl_usd": ticket.unrealized_pnl_usd,
                        "unrealized_r": ticket.unrealized_r,
                        "ticket_payload": Jsonb(payload),
                    },
                )
                return str(cur.fetchone()["id"])

    def list_open_ticket_states(self, symbol: str | None = None) -> list[MT5V51TicketRecord]:
        sql = """
            select ticket_payload
            from public.mt5_v51_ticket_state
            where is_open = true
        """
        params: list[Any] = []
        if symbol is not None:
            sql += " and symbol = %s"
            params.append(symbol)
        sql += " order by opened_at asc"
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [MT5V51TicketRecord.model_validate(row["ticket_payload"]) for row in rows]

    def find_entry_command_payload(
        self,
        *,
        symbol: str,
        basket_id: str | None,
        magic_number: int | None,
    ) -> dict[str, Any] | None:
        if basket_id is None and magic_number is None:
            return None
        sql = """
            select command_payload
            from public.mt5_v51_bridge_commands
            where symbol = %s
              and command_type = 'place_entry'
        """
        params: list[Any] = [symbol]
        if basket_id is not None:
            sql += " and basket_id = %s"
            params.append(basket_id)
        if magic_number is not None:
            sql += " and (command_payload ->> 'magic_number')::bigint = %s"
            params.append(magic_number)
        sql += " order by created_at desc limit 1"
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        return None if row is None else dict(row["command_payload"])

    def list_recent_approved_entry_times(self, *, symbol: str, since: datetime) -> list[datetime]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select recorded_at
                    from public.mt5_v51_runtime_decisions
                    where symbol = %s
                      and decision_kind = 'entry'
                      and risk_approved = true
                      and action in ('enter_long', 'enter_short')
                      and recorded_at >= %s
                    order by recorded_at asc
                    """,
                    (symbol, since),
                )
                rows = cur.fetchall()
        return [row["recorded_at"] for row in rows]

    def list_recent_trade_reflections(self, *, symbol: str, limit: int = 10) -> list[TradeReflection]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select reflection_payload
                    from public.mt5_v51_trade_reflections
                    where symbol = %s
                    order by closed_at desc
                    limit %s
                    """,
                    (symbol, limit),
                )
                rows = cur.fetchall()
        return [TradeReflection.model_validate(row["reflection_payload"]) for row in rows]

    def list_recent_lessons(self, *, limit: int = 20) -> list[LessonRecord]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select lesson_id, category, message, confidence, source, metadata
                    from public.lessons
                    where category = 'v5_1_feedback'
                    order by last_seen_at desc
                    limit %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
        return [
            LessonRecord(
                lesson_id=str(row["lesson_id"] or f"{row['source']}:{row['message']}"),
                category=row["category"],
                message=row["message"],
                confidence=float(row["confidence"]),
                source=row["source"],
                metadata=dict(row["metadata"] or {}),
            )
            for row in rows
        ]

    def upsert_lessons(self, lessons: Sequence[LessonRecord]) -> None:
        if not lessons:
            return
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                for lesson in lessons:
                    cur.execute(
                        """
                        insert into public.lessons (
                          lesson_id, category, message, confidence, source, status, policy_version_id, metadata
                        )
                        values (%s, %s, %s, %s, %s, 'active', null, %s)
                        on conflict (category, message, source) do update
                          set confidence = greatest(public.lessons.confidence, excluded.confidence),
                              metadata = public.lessons.metadata || excluded.metadata,
                              occurrence_count = public.lessons.occurrence_count + 1,
                              last_seen_at = timezone('utc', now())
                        """,
                        (
                            lesson.lesson_id,
                            lesson.category,
                            lesson.message,
                            lesson.confidence,
                            lesson.source,
                            Jsonb(lesson.metadata),
                        ),
                    )

    def _maybe_prune_snapshots(self, cur) -> None:
        now = datetime.now(timezone.utc)
        if self._last_prune_at is not None and now < self._last_prune_at + timedelta(minutes=1):
            return
        cur.execute("select public.prune_mt5_v51_bridge_snapshots(interval '2 hours')")
        self._last_prune_at = now
