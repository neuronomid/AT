#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


EVENT_COLUMNS = [
    "recorded_at",
    "record_type",
    "snapshot_symbol",
    "snapshot_spread_bps",
    "snapshot_server_time",
    "snapshot_open_tickets",
    "snapshot_pending_commands",
    "account_balance",
    "account_equity",
    "account_free_margin",
    "account_open_profit",
    "entry_action",
    "entry_confidence",
    "risk_approved",
    "risk_reason",
    "ack_status",
    "ack_message",
]

REFLECTION_COLUMNS = [
    "recorded_at",
    "closed_at",
    "side",
    "exit_reason",
    "realized_pnl_usd",
    "realized_r",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize a V5.1 MT5 session from JSONL artifacts.")
    parser.add_argument("--session-dir", type=Path, default=None, help="Path to var/v5_1/<session-tag>.")
    parser.add_argument("--follow", action="store_true", help="Refresh the summary until interrupted.")
    parser.add_argument("--interval", type=float, default=30.0, help="Refresh interval in seconds when following.")
    parser.add_argument("--tail", type=int, default=3, help="How many recent decisions or trades to print.")
    return parser.parse_args()


def _latest_session_dir(root: Path) -> Path:
    session_dirs = [path for path in root.iterdir() if path.is_dir()]
    if not session_dirs:
        raise FileNotFoundError(f"No session directories found under {root}")
    return max(session_dirs, key=lambda path: path.stat().st_mtime)


def _coerce_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _age_seconds(value: datetime | None, *, now: datetime) -> float | None:
    if value is None:
        return None
    return max((now - value).total_seconds(), 0.0)


def _format_age(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rem = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{rem:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return records


def _flatten_event_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in records:
        snapshot = row.get("snapshot", {}) if isinstance(row.get("snapshot"), dict) else {}
        account = snapshot.get("account", {}) if isinstance(snapshot.get("account"), dict) else {}
        decision = row.get("decision", {}) if isinstance(row.get("decision"), dict) else {}
        risk_decision = row.get("risk_decision", {}) if isinstance(row.get("risk_decision"), dict) else {}
        ack = row.get("ack", {}) if isinstance(row.get("ack"), dict) else {}
        open_tickets = snapshot.get("open_tickets", []) if isinstance(snapshot.get("open_tickets"), list) else []
        pending_commands = snapshot.get("pending_command_ids", []) if isinstance(snapshot.get("pending_command_ids"), list) else []
        rows.append(
            {
                "recorded_at": row.get("recorded_at"),
                "record_type": row.get("record_type"),
                "snapshot_symbol": snapshot.get("symbol"),
                "snapshot_spread_bps": snapshot.get("spread_bps"),
                "snapshot_server_time": snapshot.get("server_time"),
                "snapshot_open_tickets": len(open_tickets),
                "snapshot_pending_commands": len(pending_commands),
                "account_balance": account.get("balance"),
                "account_equity": account.get("equity"),
                "account_free_margin": account.get("free_margin"),
                "account_open_profit": account.get("open_profit"),
                "entry_action": decision.get("action"),
                "entry_confidence": decision.get("confidence"),
                "risk_approved": risk_decision.get("approved"),
                "risk_reason": risk_decision.get("reason"),
                "ack_status": ack.get("status"),
                "ack_message": ack.get("message"),
            }
        )
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def _flatten_reflection_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in records:
        reflection = row.get("reflection", {}) if isinstance(row.get("reflection"), dict) else {}
        rows.append(
            {
                "recorded_at": row.get("recorded_at"),
                "closed_at": reflection.get("closed_at"),
                "side": reflection.get("side"),
                "exit_reason": reflection.get("exit_reason"),
                "realized_pnl_usd": reflection.get("realized_pnl_usd"),
                "realized_r": reflection.get("realized_r"),
            }
        )
    return pd.DataFrame(rows, columns=REFLECTION_COLUMNS)


def _latest_snapshot_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(records):
        if row.get("record_type") == "mt5_v51_bridge_snapshot":
            return row
    return None


def _fetch_event_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    rows = con.execute(
        """
        select record_type, count(*) as event_count
        from events
        where record_type is not null
        group by 1
        """,
    ).fetchall()
    return {str(record_type): int(event_count) for record_type, event_count in rows}


def _fetch_entry_summary(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    row = con.execute(
        """
        select
            count(*) filter (where record_type = 'mt5_v51_entry_analysis') as analyses,
            count(*) filter (where record_type = 'mt5_v51_entry_execution') as executions,
            count(*) filter (
                where record_type = 'mt5_v51_entry_execution'
                and coalesce(risk_approved, false)
            ) as approved,
            count(*) filter (
                where record_type = 'mt5_v51_entry_execution'
                and not coalesce(risk_approved, false)
            ) as rejected
        from events
        """,
    ).fetchone()
    rejection_rows = con.execute(
        """
        select risk_reason as reason, count(*) as rejection_count
        from events
        where record_type = 'mt5_v51_entry_execution'
          and not coalesce(risk_approved, false)
          and risk_reason is not null
        group by 1
        order by 2 desc, 1
        limit 3
        """,
    ).fetchall()
    recent_rows = con.execute(
        """
        select
            recorded_at,
            entry_action,
            entry_confidence,
            coalesce(risk_approved, false) as approved,
            risk_reason
        from events
        where record_type = 'mt5_v51_entry_execution'
        order by try_cast(recorded_at as timestamptz) desc
        limit 5
        """,
    ).fetchall()
    return {
        "analyses": int(row[0] or 0),
        "executions": int(row[1] or 0),
        "approved": int(row[2] or 0),
        "rejected": int(row[3] or 0),
        "rejection_reasons": [(str(reason), int(count)) for reason, count in rejection_rows],
        "recent": [
            {
                "recorded_at": recorded_at,
                "action": action,
                "confidence": float(confidence or 0.0),
                "approved": bool(approved),
                "reason": reason,
            }
            for recorded_at, action, confidence, approved, reason in recent_rows
        ],
    }


def _fetch_ack_summary(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    rows = con.execute(
        """
        select ack_status as status, count(*) as ack_count
        from events
        where record_type = 'mt5_v51_bridge_ack'
          and ack_status is not null
        group by 1
        order by 2 desc, 1
        """,
    ).fetchall()
    return [(str(status), int(ack_count)) for status, ack_count in rows]


def _fetch_trade_summary(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    row = con.execute(
        """
        select
            count(*) as trade_count,
            count(*) filter (where try_cast(realized_pnl_usd as double) > 0) as wins,
            count(*) filter (where try_cast(realized_pnl_usd as double) < 0) as losses,
            coalesce(sum(try_cast(realized_pnl_usd as double)), 0) as realized_pnl_usd,
            coalesce(avg(try_cast(realized_r as double)), 0) as avg_realized_r
        from reflections
        """,
    ).fetchone()
    recent_rows = con.execute(
        """
        select
            closed_at,
            side,
            exit_reason,
            try_cast(realized_pnl_usd as double),
            try_cast(realized_r as double)
        from reflections
        order by try_cast(closed_at as timestamptz) desc
        limit 5
        """,
    ).fetchall()
    return {
        "count": int(row[0] or 0),
        "wins": int(row[1] or 0),
        "losses": int(row[2] or 0),
        "realized_pnl_usd": float(row[3] or 0.0),
        "avg_realized_r": float(row[4] or 0.0),
        "recent": [
            {
                "closed_at": closed_at,
                "side": side,
                "exit_reason": exit_reason,
                "realized_pnl_usd": float(realized_pnl_usd or 0.0),
                "realized_r": float(realized_r or 0.0),
            }
            for closed_at, side, exit_reason, realized_pnl_usd, realized_r in recent_rows
        ],
    }


def _build_warnings(
    *,
    now: datetime,
    latest_snapshot: dict[str, Any] | None,
    entry_summary: dict[str, Any],
    ack_summary: list[tuple[str, int]],
    trade_summary: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if latest_snapshot is None:
        warnings.append("No bridge snapshots recorded yet.")
        return warnings

    snapshot_payload = latest_snapshot.get("snapshot", {}) if isinstance(latest_snapshot.get("snapshot"), dict) else {}
    snapshot_recorded_at = _coerce_datetime(latest_snapshot.get("recorded_at"))
    snapshot_age = _age_seconds(snapshot_recorded_at, now=now)
    if snapshot_age is not None and snapshot_age > 10:
        warnings.append(f"Latest snapshot record is stale at {_format_age(snapshot_age)}.")

    spread_bps = snapshot_payload.get("spread_bps")
    try:
        spread_value = float(spread_bps)
    except (TypeError, ValueError):
        spread_value = None
    if spread_value is not None and spread_value >= 12.0:
        warnings.append(f"Latest spread is wide at {spread_value:.2f} bps.")

    pending_command_ids = snapshot_payload.get("pending_command_ids", [])
    if isinstance(pending_command_ids, list) and len(pending_command_ids) >= 3:
        warnings.append(f"{len(pending_command_ids)} pending commands are still on the bridge.")

    if entry_summary["executions"] > 0 and entry_summary["approved"] == 0:
        warnings.append("Every recorded entry execution has been rejected so far.")

    for reason, count in entry_summary["rejection_reasons"]:
        if count >= 3:
            warnings.append(f"Repeated rejection reason: {reason}")
            break

    rejected_acks = sum(count for status, count in ack_summary if status in {"rejected", "expired", "ignored"})
    if rejected_acks > 0:
        warnings.append(f"Bridge reported {rejected_acks} rejected/expired/ignored acknowledgements.")

    if trade_summary["losses"] >= 2 and trade_summary["realized_pnl_usd"] < 0:
        warnings.append("Session has multiple losing trades and negative realized PnL.")
    return warnings


def _print_report(*, session_dir: Path, tail_count: int) -> None:
    now = datetime.now(timezone.utc)
    events_path = session_dir / "events.jsonl"
    reflections_path = session_dir / "trade_reflections.jsonl"

    event_records = _jsonl_records(events_path)
    reflection_records = _jsonl_records(reflections_path)
    events_df = _flatten_event_records(event_records)
    reflections_df = _flatten_reflection_records(reflection_records)

    con = duckdb.connect()
    con.register("events", events_df)
    con.register("reflections", reflections_df)

    event_counts = _fetch_event_counts(con) if not events_df.empty else {}
    entry_summary = _fetch_entry_summary(con) if not events_df.empty else {
        "analyses": 0,
        "executions": 0,
        "approved": 0,
        "rejected": 0,
        "rejection_reasons": [],
        "recent": [],
    }
    ack_summary = _fetch_ack_summary(con) if not events_df.empty else []
    trade_summary = _fetch_trade_summary(con) if not reflections_df.empty else {
        "count": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl_usd": 0.0,
        "avg_realized_r": 0.0,
        "recent": [],
    }
    latest_snapshot = _latest_snapshot_record(event_records)
    warnings = _build_warnings(
        now=now,
        latest_snapshot=latest_snapshot,
        entry_summary=entry_summary,
        ack_summary=ack_summary,
        trade_summary=trade_summary,
    )

    print(f"Session: {session_dir}")
    print(f"Generated: {now.isoformat()}")

    if latest_snapshot is not None:
        snapshot = latest_snapshot.get("snapshot", {}) if isinstance(latest_snapshot.get("snapshot"), dict) else {}
        recorded_at = _coerce_datetime(latest_snapshot.get("recorded_at"))
        server_time = _coerce_datetime(snapshot.get("server_time"))
        snapshot_age = _age_seconds(recorded_at, now=now)
        server_age = _age_seconds(server_time, now=now)
        account = snapshot.get("account", {}) if isinstance(snapshot.get("account"), dict) else {}
        open_tickets = snapshot.get("open_tickets", []) if isinstance(snapshot.get("open_tickets"), list) else []
        pending_command_ids = snapshot.get("pending_command_ids", []) if isinstance(snapshot.get("pending_command_ids"), list) else []
        print(
            "Snapshot:"
            f" symbol={snapshot.get('symbol', 'n/a')}"
            f" spread_bps={snapshot.get('spread_bps', 'n/a')}"
            f" open_tickets={len(open_tickets)}"
            f" pending_commands={len(pending_command_ids)}"
            f" recorded_age={_format_age(snapshot_age)}"
            f" server_age={_format_age(server_age)}"
        )
        print(
            "Account:"
            f" balance={account.get('balance', 'n/a')}"
            f" equity={account.get('equity', 'n/a')}"
            f" free_margin={account.get('free_margin', 'n/a')}"
            f" open_profit={account.get('open_profit', 'n/a')}"
        )
    else:
        print("Snapshot: none yet")

    print(
        "Events:"
        f" snapshots={event_counts.get('mt5_v51_bridge_snapshot', 0)}"
        f" analyses={entry_summary['analyses']}"
        f" executions={entry_summary['executions']}"
        f" approved={entry_summary['approved']}"
        f" rejected={entry_summary['rejected']}"
        f" acks={sum(count for _, count in ack_summary)}"
    )
    if ack_summary:
        ack_bits = ", ".join(f"{status}={count}" for status, count in ack_summary)
        print(f"Acks: {ack_bits}")

    print(
        "Trades:"
        f" count={trade_summary['count']}"
        f" wins={trade_summary['wins']}"
        f" losses={trade_summary['losses']}"
        f" pnl_usd={trade_summary['realized_pnl_usd']:.2f}"
        f" avg_r={trade_summary['avg_realized_r']:.3f}"
    )

    if entry_summary["rejection_reasons"]:
        reasons = "; ".join(f"{reason} x{count}" for reason, count in entry_summary["rejection_reasons"])
        print(f"Top rejections: {reasons}")

    if entry_summary["recent"]:
        print("Recent entry executions:")
        for row in entry_summary["recent"][:tail_count]:
            print(
                f"  - {row['recorded_at']} action={row['action']} confidence={row['confidence']:.2f}"
                f" approved={row['approved']} reason={row['reason']}"
            )

    if trade_summary["recent"]:
        print("Recent trade reflections:")
        for row in trade_summary["recent"][:tail_count]:
            print(
                f"  - {row['closed_at']} side={row['side']} exit={row['exit_reason']}"
                f" pnl_usd={row['realized_pnl_usd']:.2f} realized_r={row['realized_r']:.3f}"
            )

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("Warnings: none")

    con.close()


def main() -> None:
    args = _parse_args()
    root = Path("var/v5_1")
    session_dir = args.session_dir or _latest_session_dir(root)
    session_dir = session_dir.resolve()
    if not session_dir.exists():
        raise FileNotFoundError(f"Session directory not found: {session_dir}")

    while True:
        if args.follow:
            os.system("clear")
        _print_report(session_dir=session_dir, tail_count=max(args.tail, 1))
        if not args.follow:
            return
        time.sleep(max(args.interval, 1.0))


if __name__ == "__main__":
    main()
