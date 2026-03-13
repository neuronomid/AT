from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class PolicyVersionRecord(BaseModel):
    id: str
    policy_name: str
    version: str
    status: str
    thresholds: dict[str, Any] = Field(default_factory=dict)
    risk_params: dict[str, Any] = Field(default_factory=dict)
    strategy_config: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @property
    def label(self) -> str:
        return f"{self.policy_name}@{self.version}"


class AgentConfigRecord(BaseModel):
    id: str | None = None
    agent_name: str
    description: str | None = None
    status: Literal["active", "paused", "shadow", "stopped"] = "active"
    broker: str = "alpaca"
    mode: Literal["paper", "simulation", "disabled"] = "paper"
    symbols: list[str] = Field(default_factory=lambda: ["ETH/USD"], min_length=1, max_length=1)
    decision_interval_seconds: int = Field(default=60, ge=5)
    max_trades_per_hour: int = Field(default=6, ge=1)
    max_risk_per_trade_pct: float = Field(default=0.005, gt=0, le=1)
    max_daily_loss_pct: float = Field(default=0.02, gt=0, le=1)
    max_position_notional_usd: Decimal = Decimal("100")
    max_spread_bps: float = Field(default=20.0, ge=0)
    min_decision_confidence: float = Field(default=0.60, ge=0, le=1)
    cooldown_seconds_after_trade: int = Field(default=60, ge=0)
    enable_agent_orders: bool = False
    strategy_policy_version_id: str | None = None
    risk_params: dict[str, Any] = Field(default_factory=dict)
    analyst_params: dict[str, Any] = Field(default_factory=dict)
    execution_params: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @property
    def symbol(self) -> str:
        return self.symbols[0]

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, value: list[str]) -> list[str]:
        normalized = [symbol.strip() for symbol in value if symbol.strip()]
        if len(normalized) != 1:
            raise ValueError("Each agent must be configured with exactly one symbol.")
        return normalized


class AgentHeartbeatRecord(BaseModel):
    agent_config_id: str
    runtime_id: str
    status: Literal["healthy", "degraded", "paused", "stopped", "error"] = "healthy"
    current_symbol: str | None = None
    latest_decision_action: str | None = None
    latest_decision_at: str | None = None
    latest_order_at: str | None = None
    open_position_qty: Decimal | None = None
    cash: Decimal | None = None
    equity: Decimal | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BacktestJobRequest(BaseModel):
    run_name: str
    symbol: str
    timeframe: str
    location: str = "us"
    lookback_days: int = Field(default=365, ge=1)
    train_window_days: int = Field(default=90, ge=1)
    test_window_days: int = Field(default=30, ge=1)
    step_days: int = Field(default=30, ge=1)
    warmup_bars: int = Field(default=20, ge=1)
    starting_cash_usd: Decimal = Decimal("10000")
    baseline_policy_version_id: str
    candidate_policy_version_ids: list[str] = Field(default_factory=list)
    agent_config_id: str | None = None
    notes: str | None = None


class StrategyPromotionRecord(BaseModel):
    id: str
    agent_config_id: str
    previous_policy_version_id: str | None = None
    new_policy_version_id: str
    source_run_id: str | None = None
    promoted_by: str
    rationale: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class StrategyPromotionRequest(BaseModel):
    agent_config_id: str
    new_policy_version_id: str
    source_run_id: str | None = None
    promoted_by: str = "dashboard-ui"
    rationale: str = Field(min_length=8)
    metadata: dict[str, Any] = Field(default_factory=dict)
