from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


MACOS_WINE_COMMON_CANDIDATES = (
    "~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files",
    "~/Library/Application Support/MetaTrader 5/Bottles/metatrader5/drive_c/users/crossover/Application Data/MetaQuotes/Terminal/Common/Files",
)
WINDOWS_COMMON_CANDIDATES = (
    "~/AppData/Roaming/MetaQuotes/Terminal/Common/Files",
)
COMMON_DIR_ENV = "AT_MT5_COMMON_FILES_DIR"


@dataclass(frozen=True)
class ManualReplayPaths:
    common_dir: Path
    session_dir: Path
    commands_path: Path
    acks_path: Path
    status_path: Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Queue manual MT5 Strategy Tester replay commands through the shared Common/Files folder."
    )
    parser.add_argument(
        "--common-dir",
        type=Path,
        default=None,
        help="Override the MT5 Common/Files directory. Defaults to auto-detection or $AT_MT5_COMMON_FILES_DIR.",
    )
    parser.add_argument(
        "--session",
        default="default",
        help="Manual replay session id. Must match the MT5 EA session input.",
    )
    parser.add_argument(
        "--symbol",
        default="",
        help="Optional symbol filter. Leave blank to target the symbol attached to the tester EA.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create the session folder and optionally clear old files.")
    init_parser.add_argument("--reset", action="store_true", help="Truncate commands, acks, and status files for the session.")

    status_parser = subparsers.add_parser("status", help="Show the latest tester status and the most recent acknowledgements.")
    status_parser.add_argument("--acks", type=int, default=5, help="How many recent acknowledgements to print.")

    tail_parser = subparsers.add_parser("tail-acks", help="Print recent acknowledgements and optionally follow new ones.")
    tail_parser.add_argument("--lines", type=int, default=10, help="How many recent acknowledgement rows to print first.")
    tail_parser.add_argument("--follow", action="store_true", help="Continue printing acknowledgements until interrupted.")
    tail_parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds when following.")

    for name in ("buy", "sell"):
        order_parser = subparsers.add_parser(name, help=f"Queue a {name} market order.")
        order_parser.add_argument("volume", nargs="?", type=float, default=0.10, help="Lots to send.")
        _add_protection_args(order_parser)
        order_parser.add_argument("--comment", default="", help="Optional order comment suffix.")

    for name in ("buy-limit", "sell-limit", "buy-stop", "sell-stop"):
        order_parser = subparsers.add_parser(name, help=f"Queue a {name} pending order.")
        order_parser.add_argument("volume", type=float, help="Lots to send.")
        order_parser.add_argument("price", type=float, help="Entry price for the pending order.")
        _add_protection_args(order_parser)
        order_parser.add_argument("--comment", default="", help="Optional order comment suffix.")

    close_ticket_parser = subparsers.add_parser("close-ticket", help="Close a specific open position or cancel a pending order.")
    close_ticket_parser.add_argument("ticket_id", type=int, help="MT5 ticket id to close.")
    close_ticket_parser.add_argument("--volume", type=float, default=0.0, help="Optional partial-close volume in lots.")

    protect_parser = subparsers.add_parser("protect-ticket", help="Update SL/TP for an open position or pending order.")
    protect_parser.add_argument("ticket_id", type=int, help="MT5 ticket id to modify.")
    protect_parser.add_argument("--sl-price", type=float, default=0.0, help="Absolute stop-loss price.")
    protect_parser.add_argument("--tp-price", type=float, default=0.0, help="Absolute take-profit price.")

    subparsers.add_parser("close-all", help="Close every open position for the attached symbol.")
    subparsers.add_parser("cancel-all", help="Cancel every pending order for the attached symbol.")
    subparsers.add_parser("flatten", help="Close open positions and cancel pending orders for the attached symbol.")

    return parser.parse_args()


def _add_protection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sl-price", type=float, default=0.0, help="Absolute stop-loss price.")
    parser.add_argument("--tp-price", type=float, default=0.0, help="Absolute take-profit price.")
    parser.add_argument("--sl-points", type=int, default=0, help="Stop-loss offset in symbol points.")
    parser.add_argument("--tp-points", type=int, default=0, help="Take-profit offset in symbol points.")


def _candidate_common_dirs() -> list[Path]:
    candidates = [Path(path).expanduser() for path in MACOS_WINE_COMMON_CANDIDATES + WINDOWS_COMMON_CANDIDATES]
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        deduped.append(path)
        seen.add(path)
    return deduped


def resolve_common_dir(override: Path | None = None) -> Path:
    if override is not None:
        resolved = override.expanduser()
        if not resolved.exists():
            raise FileNotFoundError(f"MT5 Common/Files directory does not exist: {resolved}")
        return resolved

    env_value = os.getenv(COMMON_DIR_ENV)
    if env_value:
        resolved = Path(env_value).expanduser()
        if not resolved.exists():
            raise FileNotFoundError(f"{COMMON_DIR_ENV} points to a missing directory: {resolved}")
        return resolved

    for candidate in _candidate_common_dirs():
        if candidate.exists():
            return candidate

    searched = "\n".join(f"- {candidate}" for candidate in _candidate_common_dirs())
    raise FileNotFoundError(
        "Could not locate the MT5 Common/Files directory.\n"
        f"Set {COMMON_DIR_ENV} or pass --common-dir.\n"
        f"Searched:\n{searched}"
    )


def sanitize_session_id(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "default"
    for bad in ("/", "\\", ":", "*", "?", "\"", "<", ">", "|", "\t", "\r", "\n"):
        text = text.replace(bad, "_")
    return text.encode("ascii", "ignore").decode("ascii") or "default"


def sanitize_field(value: str) -> str:
    text = value.strip()
    for bad in ("\t", "\r", "\n"):
        text = text.replace(bad, " ")
    return " ".join(text.split()).encode("ascii", "ignore").decode("ascii")


def build_paths(*, common_dir: Path, session: str) -> ManualReplayPaths:
    safe_session = sanitize_session_id(session)
    session_dir = common_dir / "AT" / "manual_replay" / safe_session
    return ManualReplayPaths(
        common_dir=common_dir,
        session_dir=session_dir,
        commands_path=session_dir / "commands.tsv",
        acks_path=session_dir / "acks.tsv",
        status_path=session_dir / "status.tsv",
    )


def ensure_session_dir(paths: ManualReplayPaths) -> None:
    paths.session_dir.mkdir(parents=True, exist_ok=True)


def reset_session_files(paths: ManualReplayPaths) -> None:
    ensure_session_dir(paths)
    for path in (paths.commands_path, paths.acks_path, paths.status_path):
        path.write_text("", encoding="utf-8")


def build_command_row(
    *,
    action: str,
    symbol: str,
    order_type: str = "",
    volume_lots: float = 0.0,
    entry_price: float = 0.0,
    sl_price: float = 0.0,
    tp_price: float = 0.0,
    sl_points: int = 0,
    tp_points: int = 0,
    ticket_id: int = 0,
    comment: str = "",
) -> list[str]:
    command_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return [
        command_id,
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        sanitize_field(action),
        sanitize_field(symbol),
        sanitize_field(order_type),
        f"{float(volume_lots):.6f}",
        f"{float(entry_price):.10f}",
        f"{float(sl_price):.10f}",
        f"{float(tp_price):.10f}",
        str(int(sl_points)),
        str(int(tp_points)),
        str(int(ticket_id)),
        sanitize_field(comment),
    ]


def append_tsv_row(path: Path, row: Sequence[str]) -> None:
    ensure_parent = path.parent
    ensure_parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(list(row))


def read_tsv_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row in reader:
            if not row:
                continue
            rows.append(row)
    return rows


def queue_command(paths: ManualReplayPaths, row: Sequence[str]) -> None:
    ensure_session_dir(paths)
    append_tsv_row(paths.commands_path, row)
    print(f"queued {row[2]} command {row[0]} -> {paths.commands_path}")


def _queue_order_command(args: argparse.Namespace, *, order_type: str) -> None:
    common_dir = resolve_common_dir(args.common_dir)
    paths = build_paths(common_dir=common_dir, session=args.session)
    row = build_command_row(
        action="place_order",
        symbol=args.symbol,
        order_type=order_type,
        volume_lots=args.volume,
        entry_price=getattr(args, "price", 0.0),
        sl_price=getattr(args, "sl_price", 0.0),
        tp_price=getattr(args, "tp_price", 0.0),
        sl_points=getattr(args, "sl_points", 0),
        tp_points=getattr(args, "tp_points", 0),
        comment=getattr(args, "comment", ""),
    )
    queue_command(paths, row)


def _queue_simple_action(args: argparse.Namespace, action: str, *, volume_lots: float = 0.0, ticket_id: int = 0) -> None:
    common_dir = resolve_common_dir(args.common_dir)
    paths = build_paths(common_dir=common_dir, session=args.session)
    row = build_command_row(
        action=action,
        symbol=args.symbol,
        volume_lots=volume_lots,
        ticket_id=ticket_id,
    )
    queue_command(paths, row)


def _queue_protect_ticket(args: argparse.Namespace) -> None:
    if args.sl_price <= 0 and args.tp_price <= 0:
        raise SystemExit("protect-ticket requires --sl-price, --tp-price, or both.")
    common_dir = resolve_common_dir(args.common_dir)
    paths = build_paths(common_dir=common_dir, session=args.session)
    row = build_command_row(
        action="protect_ticket",
        symbol=args.symbol,
        ticket_id=args.ticket_id,
        sl_price=args.sl_price,
        tp_price=args.tp_price,
    )
    queue_command(paths, row)


def _print_status(args: argparse.Namespace) -> None:
    common_dir = resolve_common_dir(args.common_dir)
    paths = build_paths(common_dir=common_dir, session=args.session)
    rows = read_tsv_rows(paths.status_path)
    print(f"common dir: {paths.common_dir}")
    print(f"session dir: {paths.session_dir}")
    if not rows:
        print("status: no tester status written yet")
    else:
        latest = rows[-1]
        padded = latest + [""] * (11 - len(latest))
        print(
            "status:"
            f" updated_at={padded[0]}"
            f" symbol={padded[1]}"
            f" bid={padded[2]}"
            f" ask={padded[3]}"
            f" spread_points={padded[4]}"
            f" open_positions={padded[5]}"
            f" pending_orders={padded[6]}"
            f" volume_lots={padded[7]}"
            f" floating_pnl={padded[8]}"
            f" balance={padded[9]}"
            f" equity={padded[10]}"
        )

    ack_rows = read_tsv_rows(paths.acks_path)
    if not ack_rows:
        print("acks: none")
        return
    print("acks:")
    for row in ack_rows[-max(args.acks, 0) :]:
        print(_format_ack_row(row))


def _format_ack_row(row: Sequence[str]) -> str:
    padded = list(row) + [""] * (6 - len(row))
    return (
        f"  {padded[1]} status={padded[2]} action={padded[3]} "
        f"ticket={padded[4] or '-'} command_id={padded[0]} message={padded[5]}"
    )


def _tail_acks(args: argparse.Namespace) -> None:
    common_dir = resolve_common_dir(args.common_dir)
    paths = build_paths(common_dir=common_dir, session=args.session)
    seen = 0
    rows = read_tsv_rows(paths.acks_path)
    if rows:
        for row in rows[-max(args.lines, 0) :]:
            print(_format_ack_row(row))
        seen = len(rows)
    else:
        print("no acknowledgements yet")
    if not args.follow:
        return

    try:
        while True:
            time.sleep(max(args.interval, 0.1))
            rows = read_tsv_rows(paths.acks_path)
            if len(rows) <= seen:
                continue
            for row in rows[seen:]:
                print(_format_ack_row(row))
            seen = len(rows)
    except KeyboardInterrupt:
        return


def main() -> None:
    args = _parse_args()

    if args.command == "init":
        common_dir = resolve_common_dir(args.common_dir)
        paths = build_paths(common_dir=common_dir, session=args.session)
        ensure_session_dir(paths)
        if args.reset:
            reset_session_files(paths)
        print(f"session ready: {paths.session_dir}")
        return

    if args.command == "status":
        _print_status(args)
        return

    if args.command == "tail-acks":
        _tail_acks(args)
        return

    if args.command == "buy":
        _queue_order_command(args, order_type="buy")
        return

    if args.command == "sell":
        _queue_order_command(args, order_type="sell")
        return

    if args.command == "buy-limit":
        _queue_order_command(args, order_type="buy_limit")
        return

    if args.command == "sell-limit":
        _queue_order_command(args, order_type="sell_limit")
        return

    if args.command == "buy-stop":
        _queue_order_command(args, order_type="buy_stop")
        return

    if args.command == "sell-stop":
        _queue_order_command(args, order_type="sell_stop")
        return

    if args.command == "close-ticket":
        _queue_simple_action(args, "close_ticket", volume_lots=args.volume, ticket_id=args.ticket_id)
        return

    if args.command == "protect-ticket":
        _queue_protect_ticket(args)
        return

    if args.command == "close-all":
        _queue_simple_action(args, "close_all")
        return

    if args.command == "cancel-all":
        _queue_simple_action(args, "cancel_all")
        return

    if args.command == "flatten":
        _queue_simple_action(args, "flatten")
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
