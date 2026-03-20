from app.v6_0_config import V60Settings


def test_v6_0_settings_accepts_standard_openai_api_key_env(monkeypatch) -> None:
    monkeypatch.delenv("V60_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    settings = V60Settings(_env_file=None)

    assert settings.openai_api_key == "test-openai-key"


def test_v6_0_settings_ignores_shared_supabase_env_by_default(monkeypatch) -> None:
    monkeypatch.delenv("V60_ENABLE_SUPABASE", raising=False)
    monkeypatch.delenv("V60_SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://shared-db")

    settings = V60Settings(_env_file=None)

    assert settings.supabase_db_dsn is None


def test_v6_0_settings_allows_explicit_v60_supabase_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("V60_ENABLE_SUPABASE", "true")
    monkeypatch.setenv("V60_SUPABASE_DB_URL", "postgresql://v60-db")
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)

    settings = V60Settings(_env_file=None)

    assert settings.supabase_db_dsn == "postgresql://v60-db"


def test_v6_0_settings_default_manager_reasoning_is_low(monkeypatch) -> None:
    monkeypatch.delenv("V60_MANAGER_REASONING_EFFORT", raising=False)

    settings = V60Settings(_env_file=None)

    assert settings.v60_mt5_symbol == "EURUSD@"
    assert settings.v60_entry_reasoning_effort == "high"
    assert settings.v60_mt5_manager_sweep_seconds == 5
    assert settings.v60_manager_reasoning_effort == "low"
    assert settings.manager_reasoning_effort == "low"


def test_v6_0_settings_allows_explicit_manager_reasoning_effort(monkeypatch) -> None:
    monkeypatch.setenv("V60_MANAGER_REASONING_EFFORT", "minimal")

    settings = V60Settings(_env_file=None)

    assert settings.manager_reasoning_effort == "minimal"
