from app.v6_1_config import V61Settings


def test_v6_1_settings_accepts_standard_openai_api_key_env(monkeypatch) -> None:
    monkeypatch.delenv("V61_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    settings = V61Settings(_env_file=None)

    assert settings.openai_api_key == "test-openai-key"


def test_v6_1_settings_allows_explicit_v61_supabase_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("V61_ENABLE_SUPABASE", "true")
    monkeypatch.setenv("V61_SUPABASE_DB_URL", "postgresql://v61-db")

    settings = V61Settings(_env_file=None)

    assert settings.supabase_db_dsn == "postgresql://v61-db"


def test_v6_1_settings_default_multi_symbol_compatibility(monkeypatch) -> None:
    monkeypatch.delenv("V61_MANAGER_REASONING_EFFORT", raising=False)

    settings = V61Settings(_env_file=None)

    assert settings.v61_bridge_port == 8093
    assert settings.v61_bridge_id == "mt5-v61-local"
    assert settings.v61_screenshot_relative_path == "AT_V61/screenshots/latest.png"
    assert settings.v60_bridge_port == 8093
    assert settings.v60_mt5_symbol == ""
    assert settings.manager_reasoning_effort == "low"
