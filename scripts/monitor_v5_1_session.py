#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


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


def _latest_snapshot_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(records):
        if row.get("record_type") == "mt5_v51_bridge_snapshot":
            return row
    return None


def _fetch_event_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in records:
        record_type = row.get("record_type")
        if not isinstance(record_type, str) or not record_type:
            continue
        counts[record_type] = counts.get(record_type, 0) + 1
    return counts


def _fetch_entry_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    analyses = 0
    executions = 0
    approved = 0
    rejected = 0
    rejection_counts: dict[str, int] = {}
    recent: list[dict[str, Any]] = []

    for row in records:
        record_type = row.get("record_type")
        if record_type == "mt5_v51_entry_analysis":
            analyses += 1
            continue
        if record_type != "mt5_v51_entry_execution":
            continue

        executions += 1
        decision = row.get("decision", {}) if isinstance(row.get("decision"), dict) else {}
        risk_decision = row.get("risk_decision", {}) if isinstance(row.get("risk_decision"), dict) else {}
        approved_flag = bool(risk_decision.get("approved"))
        if approved_flag:
            approved += 1
        else:
            rejected += 1
            reason = risk_decision.get("reason")
            if isinstance(reason, str) and reason:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

        recent.append(
            {
                "recorded_at": row.get("recorded_at"),
                "action": decision.get("action"),
                "confidence": _safe_float(decision.get("confidence")),
                "approved": approved_flag,
                "reason": risk_decision.get("reason"),
            }
        )

    recent.sort(key=lambda row: _coerce_datetime(row.get("recorded_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    rejection_rows = sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    return {
        "analyses": analyses,
        "executions": executions,
        "approved": approved,
        "rejected": rejected,
        "rejection_reasons": rejection_rows,
        "recent": recent[:5],
    }


def _fetch_ack_summary(records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in records:
        if row.get("record_type") != "mt5_v51_bridge_ack":
            continue
        ack = row.get("ack", {}) if isinstance(row.get("ack"), dict) else {}
        status = ack.get("status")
        if not isinstance(status, str) or not status:
            continue
        counts[status] = counts.get(status, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _fetch_trade_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    wins = 0
    losses = 0
    realized_pnl_usd = 0.0
    realized_r_total = 0.0
    realized_r_count = 0
    recent: list[dict[str, Any]] = []

    for row in records:
        reflection = row.get("reflection", {}) if isinstance(row.get("reflection"), dict) else {}
        pnl = _safe_float(reflection.get("realized_pnl_usd"))
        realized_r = _safe_float(reflection.get("realized_r"))
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        realized_pnl_usd += pnl
        realized_r_total += realized_r
        realized_r_count += 1
        recent.append(
            {
                "closed_at": reflection.get("closed_at"),
                "side": reflection.get("side"),
                "exit_reason": reflection.get("exit_reason"),
                "realized_pnl_usd": pnl,
                "realized_r": realized_r,
            }
        )

    recent.sort(key=lambda row: _coerce_datetime(row.get("closed_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return {
        "count": len(records),
        "wins": wins,
        "losses": losses,
        "realized_pnl_usd": realized_pnl_usd,
        "avg_realized_r": (realized_r_total / realized_r_count) if realized_r_count else 0.0,
        "recent": recent[:5],
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

    spread_value = _safe_float(snapshot_payload.get("spread_bps"))
    if spread_value >= 12.0:
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
    event_counts = _fetch_event_counts(event_records)
    entry_summary = _fetch_entry_summary(event_records)
    ack_summary = _fetch_ack_summary(event_records)
    trade_summary = _fetch_trade_summary(reflection_records)
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
