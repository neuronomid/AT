from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class V51Settings(BaseSettings):
    log_level: str = "INFO"

    v51_agent_name: str = "mt5_v51_primary"
    v51_bridge_host: str = "127.0.0.1"
    v51_bridge_port: int = Field(default=8091, ge=1, le=65535)
    v51_bridge_id: str = "mt5-v51-local"
    v51_mt5_symbol: str = "BTCUSD"
    v51_mt5_account_mode: Literal["hedging", "netting"] = "hedging"
    v51_mt5_entry_timeout_seconds: int = Field(default=15, ge=5, le=60)
    v51_mt5_manager_sweep_seconds: int = Field(default=15, ge=5, le=60)
    v51_mt5_enable_trade_commands: bool = False
    v51_mt5_shadow_mode: bool = True
    v51_enable_fast_entry_override: bool = True
    v51_enable_continuation_override: bool = True
    v51_max_trades_per_hour: int = Field(default=15, ge=1)
    v51_max_spread_bps: float = Field(default=12.0, ge=0)
    v51_stale_after_seconds: int = Field(default=5, ge=1, le=30)
    v51_analysis_signal_max_age_seconds: int = Field(default=30, ge=5, le=120)
    v51_require_5m_trend_alignment: bool = False
    v51_min_decision_confidence: float = Field(default=0.50, ge=0, le=1)
    v51_min_risk_fraction: float = Field(default=0.001, gt=0, le=1)
    v51_max_risk_fraction: float = Field(default=0.004, gt=0, le=1)
    v51_max_daily_loss_pct: float = Field(default=0.015, gt=0, le=1)
    v51_micro_lookback_bars: int = Field(default=90, ge=30)
    v51_micro_min_warmup_bars: int = Field(default=6, ge=6)
    v51_min_hold_bars: int = Field(default=1, ge=1, le=6)
    v51_partial_target_r: float = Field(default=0.5, gt=0, le=2)
    v51_final_target_r: float = Field(default=0.5, gt=0, le=3)
    v51_post_partial_stop_lock_r: float = Field(default=0.0, ge=0, le=1)

    v51_openrouter_api_key: SecretStr | None = None
    v51_openrouter_model: str = "deepseek/deepseek-v3.2"
    v51_openrouter_base_url: str = "https://openrouter.ai/api/v1"
    v51_entry_reasoning_enabled: bool = False
    v51_manager_reasoning_enabled: bool = False

    supabase_db_url: SecretStr | None = None
    supabase_url: str | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def supabase_db_dsn(self) -> str | None:
        if self.supabase_db_url is None:
            return None
        value = self.supabase_db_url.get_secret_value().strip()
        if value.startswith("SUPABASE_DB_URL="):
            return value.split("=", 1)[1]
        return value

    @property
    def openrouter_api_key(self) -> str | None:
        if self.v51_openrouter_api_key is None:
            return None
        return self.v51_openrouter_api_key.get_secret_value()


@lru_cache(maxsize=1)
def get_v51_settings() -> V51Settings:
    return V51Settings()
