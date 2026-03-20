from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class V61Settings(BaseSettings):
    log_level: str = "INFO"

    v61_agent_name: str = "mt5_v61_multi"
    v61_bridge_host: str = "127.0.0.1"
    v61_bridge_port: int = Field(default=8093, ge=1, le=65535)
    v61_bridge_id: str = "mt5-v61-local"
    v61_mt5_account_mode: Literal["hedging", "netting"] = "hedging"
    v61_mt5_entry_timeout_seconds: int = Field(default=90, ge=10, le=300)
    v61_mt5_manager_sweep_seconds: int = Field(default=5, ge=5, le=60)
    v61_mt5_enable_trade_commands: bool = True
    v61_mt5_shadow_mode: bool = False
    v61_max_trades_per_hour: int = Field(default=15, ge=1)
    v61_max_spread_bps: float = Field(default=18.0, ge=0)
    v61_stale_after_seconds: int = Field(default=5, ge=1, le=30)
    v61_min_decision_confidence: float = Field(default=0.50, ge=0, le=1)
    v61_min_risk_fraction: float = Field(default=0.001, gt=0, le=1)
    v61_max_risk_fraction: float = Field(default=0.005, gt=0, le=1)
    v61_max_daily_loss_pct: float = Field(default=0.015, gt=0, le=1)
    v61_mt5_files_root: str = "/Users/omid/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"
    v61_screenshot_relative_path: str = "AT_V61/screenshots/latest.png"

    v61_openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("V61_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    v61_openai_model: str = "gpt-5-nano"
    v61_openai_base_url: str = "https://api.openai.com/v1"
    v61_entry_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "high"
    v61_manager_reasoning_effort: Literal["off", "minimal", "low", "medium", "high"] = "low"

    v61_enable_supabase: bool = False
    v61_supabase_db_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("V61_SUPABASE_DB_URL"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def supabase_db_dsn(self) -> str | None:
        if not self.v61_enable_supabase or self.v61_supabase_db_url is None:
            return None
        value = self.v61_supabase_db_url.get_secret_value().strip()
        if value.startswith("V61_SUPABASE_DB_URL="):
            return value.split("=", 1)[1]
        if value.startswith("SUPABASE_DB_URL="):
            return value.split("=", 1)[1]
        return value

    @property
    def openai_api_key(self) -> str | None:
        if self.v61_openai_api_key is None:
            return None
        return self.v61_openai_api_key.get_secret_value()

    @property
    def manager_reasoning_effort(self) -> str | None:
        if self.v61_manager_reasoning_effort == "off":
            return None
        return self.v61_manager_reasoning_effort

    @property
    def screenshot_absolute_path(self) -> str:
        root = Path(self.v61_mt5_files_root).expanduser()
        return str(root / self.v61_screenshot_relative_path)

    # Compatibility aliases for shared V6.0 helper functions reused by V6.1.
    @property
    def v60_agent_name(self) -> str:
        return self.v61_agent_name

    @property
    def v60_bridge_host(self) -> str:
        return self.v61_bridge_host

    @property
    def v60_bridge_port(self) -> int:
        return self.v61_bridge_port

    @property
    def v60_bridge_id(self) -> str:
        return self.v61_bridge_id

    @property
    def v60_mt5_symbol(self) -> str:
        return ""

    @property
    def v60_mt5_account_mode(self) -> str:
        return self.v61_mt5_account_mode

    @property
    def v60_mt5_entry_timeout_seconds(self) -> int:
        return self.v61_mt5_entry_timeout_seconds

    @property
    def v60_mt5_manager_sweep_seconds(self) -> int:
        return self.v61_mt5_manager_sweep_seconds

    @property
    def v60_mt5_enable_trade_commands(self) -> bool:
        return self.v61_mt5_enable_trade_commands

    @property
    def v60_mt5_shadow_mode(self) -> bool:
        return self.v61_mt5_shadow_mode

    @property
    def v60_max_trades_per_hour(self) -> int:
        return self.v61_max_trades_per_hour

    @property
    def v60_max_spread_bps(self) -> float:
        return self.v61_max_spread_bps

    @property
    def v60_stale_after_seconds(self) -> int:
        return self.v61_stale_after_seconds

    @property
    def v60_min_decision_confidence(self) -> float:
        return self.v61_min_decision_confidence

    @property
    def v60_min_risk_fraction(self) -> float:
        return self.v61_min_risk_fraction

    @property
    def v60_max_risk_fraction(self) -> float:
        return self.v61_max_risk_fraction

    @property
    def v60_max_daily_loss_pct(self) -> float:
        return self.v61_max_daily_loss_pct

    @property
    def v60_mt5_files_root(self) -> str:
        return self.v61_mt5_files_root

    @property
    def v60_screenshot_relative_path(self) -> str:
        return self.v61_screenshot_relative_path

    @property
    def v60_openai_model(self) -> str:
        return self.v61_openai_model

    @property
    def v60_openai_base_url(self) -> str:
        return self.v61_openai_base_url

    @property
    def v60_entry_reasoning_effort(self) -> str:
        return self.v61_entry_reasoning_effort


@lru_cache(maxsize=1)
def get_v61_settings() -> V61Settings:
    return V61Settings()
