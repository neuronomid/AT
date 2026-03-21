from pathlib import Path

from app.mt5_manual_replay import (
    COMMON_DIR_ENV,
    build_command_row,
    build_paths,
    resolve_common_dir,
    sanitize_field,
    sanitize_session_id,
)


def test_sanitize_session_id_replaces_path_breakers() -> None:
    assert sanitize_session_id("btc/replay:session?") == "btc_replay_session_"


def test_sanitize_field_strips_tabs_and_newlines() -> None:
    assert sanitize_field("  hello\tthere\nfriend  ") == "hello there friend"


def test_build_paths_uses_repo_session_layout(tmp_path: Path) -> None:
    paths = build_paths(common_dir=tmp_path, session="btc-replay")
    assert paths.session_dir == tmp_path / "AT" / "manual_replay" / "btc-replay"
    assert paths.commands_path == paths.session_dir / "commands.tsv"
    assert paths.acks_path == paths.session_dir / "acks.tsv"
    assert paths.status_path == paths.session_dir / "status.tsv"


def test_build_command_row_emits_fixed_schema() -> None:
    row = build_command_row(
        action="place_order",
        symbol="BTCUSD",
        order_type="buy_limit",
        volume_lots=0.25,
        entry_price=84000.5,
        sl_points=100,
        tp_points=200,
        comment="swing entry",
    )
    assert len(row) == 13
    assert row[2] == "place_order"
    assert row[3] == "BTCUSD"
    assert row[4] == "buy_limit"
    assert row[5] == "0.250000"
    assert row[6] == "84000.5000000000"
    assert row[9] == "100"
    assert row[10] == "200"
    assert row[12] == "swing entry"


def test_resolve_common_dir_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(COMMON_DIR_ENV, str(tmp_path))
    assert resolve_common_dir() == tmp_path
