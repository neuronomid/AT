from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class V60Settings(BaseSettings):
    log_level: str = "INFO"

    v60_agent_name: str = "mt5_v60_primary"
    v60_bridge_host: str = "127.0.0.1"
    v60_bridge_port: int = Field(default=8092, ge=1, le=65535)
    v60_bridge_id: str = "mt5-v60-local"
    v60_mt5_symbol: str = "EURUSD@"
    v60_mt5_account_mode: Literal["hedging", "netting"] = "hedging"
    v60_mt5_entry_timeout_seconds: int = Field(default=90, ge=10, le=300)
    v60_mt5_manager_sweep_seconds: int = Field(default=5, ge=5, le=60)
    v60_mt5_enable_trade_commands: bool = True
    v60_mt5_shadow_mode: bool = False
    v60_max_trades_per_hour: int = Field(default=15, ge=1)
    v60_max_spread_bps: float = Field(default=18.0, ge=0)
    v60_stale_after_seconds: int = Field(default=5, ge=1, le=30)
    v60_min_decision_confidence: float = Field(default=0.50, ge=0, le=1)
    v60_min_risk_fraction: float = Field(default=0.001, gt=0, le=1)
    v60_max_risk_fraction: float = Field(default=0.005, gt=0, le=1)
    v60_max_daily_loss_pct: float = Field(default=0.015, gt=0, le=1)
    v60_mt5_files_root: str = "/Users/omid/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
    v60_screenshot_relative_path: str = "AT_V60/screenshots/latest.png"

    v60_openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("V60_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    v60_openai_model: str = "gpt-5-nano"
    v60_openai_base_url: str = "https://api.openai.com/v1"
    v60_entry_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "high"
    v60_manager_reasoning_effort: Literal["off", "minimal", "low", "medium", "high"] = "low"

    v60_enable_supabase: bool = False
    v60_supabase_db_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("V60_SUPABASE_DB_URL"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def supabase_db_dsn(self) -> str | None:
        if not self.v60_enable_supabase or self.v60_supabase_db_url is None:
            return None
        value = self.v60_supabase_db_url.get_secret_value().strip()
        if value.startswith("V60_SUPABASE_DB_URL="):
            return value.split("=", 1)[1]
        if value.startswith("SUPABASE_DB_URL="):
            return value.split("=", 1)[1]
        return value

    @property
    def openai_api_key(self) -> str | None:
        if self.v60_openai_api_key is None:
            return None
        return self.v60_openai_api_key.get_secret_value()

    @property
    def screenshot_absolute_path(self) -> str:
        root = Path(self.v60_mt5_files_root).expanduser()
        return str(root / self.v60_screenshot_relative_path)

    @property
    def manager_reasoning_effort(self) -> str | None:
        if self.v60_manager_reasoning_effort == "off":
            return None
        return self.v60_manager_reasoning_effort


@lru_cache(maxsize=1)
def get_v60_settings() -> V60Settings:
    return V60Settings()
