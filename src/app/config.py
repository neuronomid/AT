from functools import lru_cache
from decimal import Decimal
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    agent_name: str = "primary"

    alpaca_api_key: SecretStr | None = None
    alpaca_api_secret: SecretStr | None = None
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_crypto_data_ws_url: str = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"
    alpaca_trading_stream_ws_url: str = "wss://paper-api.alpaca.markets/stream"
    alpaca_account_sync_seconds: int = Field(default=15, ge=5)

    trading_symbol: str = "ETH/USD"
    decision_interval_seconds: int = Field(default=60, ge=5)
    decision_loop_iterations: int = Field(default=1, ge=0)
    max_trades_per_hour: int = Field(default=6, ge=1)
    max_risk_per_trade_pct: float = Field(default=0.005, gt=0, le=1)
    max_daily_loss_pct: float = Field(default=0.02, gt=0, le=1)
    max_position_notional_usd: Decimal = Decimal("100")
    max_spread_bps: float = Field(default=20, ge=0)
    min_decision_confidence: float = Field(default=0.60, ge=0, le=1)
    cooldown_seconds_after_trade: int = Field(default=60, ge=0)
    enable_agent_orders: bool = False
    enable_paper_test_order: bool = False
    paper_test_order_notional_usd: Decimal = Decimal("25")
    journal_path: str = "var/decision_journal.jsonl"
    lessons_path: str = "var/lessons.jsonl"
    review_summary_path: str = "var/review_summary.json"
    evaluation_report_path: str = "var/evaluation_report.json"
    strategy_advice_path: str = "var/strategy_advice.md"
    evaluation_min_closed_trades: int = Field(default=1, ge=0)
    evaluation_min_score_improvement: float = 5.0
    evaluation_max_additional_drawdown_bps: float = Field(default=50.0, ge=0)
    backtest_report_path: str = "var/backtest_report.json"
    backtest_timeframe: str = "1Min"
    backtest_location: str = "us"
    backtest_lookback_days: int = Field(default=365, ge=1)
    backtest_train_window_days: int = Field(default=90, ge=1)
    backtest_test_window_days: int = Field(default=30, ge=1)
    backtest_step_days: int = Field(default=30, ge=1)
    backtest_warmup_bars: int = Field(default=20, ge=1)
    backtest_starting_cash_usd: Decimal = Decimal("10000")
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = Field(default=8501, ge=1, le=65535)
    dashboard_api_host: str = "127.0.0.1"
    dashboard_api_port: int = Field(default=8000, ge=1, le=65535)

    mt5_bridge_host: str = "127.0.0.1"
    mt5_bridge_port: int = Field(default=8090, ge=1, le=65535)
    mt5_bridge_id: str = "mt5-local"
    mt5_symbol: str = "EURUSD"
    mt5_account_mode: Literal["hedging", "netting"] = "hedging"
    mt5_entry_timeout_seconds: int = Field(default=60, ge=5, le=300)
    mt5_manager_sweep_seconds: int = Field(default=60, ge=10, le=300)
    mt5_enable_trade_commands: bool = False
    mt5_shadow_mode: bool = True

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    supabase_url: str | None = None
    supabase_db_url: SecretStr | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_project_ref: str | None = None
    supabase_access_token: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def has_alpaca_credentials(self) -> bool:
        return self.alpaca_api_key is not None and self.alpaca_api_secret is not None

    @property
    def has_supabase_runtime_config(self) -> bool:
        return self.supabase_url is not None and (
            self.supabase_db_url is not None or self.supabase_service_role_key is not None
        )

    @property
    def has_supabase_mcp_config(self) -> bool:
        return self.supabase_project_ref is not None and self.supabase_access_token is not None

    @property
    def supabase_db_dsn(self) -> str | None:
        if self.supabase_db_url is None:
            return None
        value = self.supabase_db_url.get_secret_value().strip()
        if value.startswith("SUPABASE_DB_URL="):
            return value.split("=", 1)[1]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
