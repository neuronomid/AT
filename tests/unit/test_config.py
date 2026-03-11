from app.config import Settings


def test_default_symbol_is_eth_usd() -> None:
    settings = Settings()
    assert settings.trading_symbol == "ETH/USD"


def test_default_decision_loop_iterations_is_one() -> None:
    settings = Settings(_env_file=None)
    assert settings.decision_loop_iterations == 1


def test_openai_settings_load_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")

    settings = Settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-openai-key"
    assert settings.openai_model == "gpt-5-mini"
