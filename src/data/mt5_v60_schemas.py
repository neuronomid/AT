from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class MT5V60SymbolSpec(BaseModel):
    digits: int = Field(ge=0)
    point: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    tick_value: Decimal = Field(gt=0)
    volume_min: Decimal = Field(gt=0)
    volume_step: Decimal = Field(gt=0)
    volume_max: Decimal = Field(gt=0)
    stops_level_points: int = Field(default=0, ge=0)

    @property
    def min_stop_distance_price(self) -> Decimal:
        return self.point * Decimal(self.stops_level_points)


class MT5V60AccountSnapshot(BaseModel):
    login: str | None = None
    balance: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    free_margin: Decimal = Decimal("0")
    margin: Decimal = Decimal("0")
    margin_level: float | None = None
    currency: str = "USD"
    leverage: int | None = None
    demo: bool = True
    account_mode: Literal["hedging", "netting"] = "hedging"
    trade_allowed: bool = True
    open_profit: Decimal = Decimal("0")
    broker: str | None = None


class MT5V60Bar(BaseModel):
    timeframe: Literal["1m", "2m", "3m", "5m"]
    start_at: datetime
    end_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal = Decimal("0")
    tick_volume: int = 0
    spread_bps: float | None = None
    complete: bool = True


class MT5V60ChartScreenshot(BaseModel):
    relative_path: str | None = None
    fingerprint: str | None = None
    captured_at: datetime | None = None
    chart_timeframe: Literal["3m"] = "3m"
    capture_ok: bool = False
    message: str | None = None


class MT5V60CloseEvent(BaseModel):
    event_id: str
    symbol: str
    ticket_id: str | None = None
    basket_id: str | None = None
    side: Literal["long", "short"] | None = None
    closed_at: datetime
    close_reason: Literal["stop_loss", "take_profit", "manual_or_command", "unknown"] = "unknown"
    exit_price: Decimal | None = Field(default=None, gt=0)
    volume_lots: Decimal | None = Field(default=None, gt=0)
    realized_pnl_usd: Decimal | None = None
    comment: str | None = None


class MT5V60LiveTicket(BaseModel):
    ticket_id: str
    symbol: str
    side: Literal["long", "short"]
    volume_lots: Decimal = Field(gt=0)
    open_price: Decimal = Field(gt=0)
    current_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    unrealized_pnl_usd: Decimal = Decimal("0")
    protected: bool = False
    opened_at: datetime | None = None
    magic_number: int | None = None
    comment: str | None = None
    basket_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MT5V60BridgeHealth(BaseModel):
    bridge_id: str = "mt5-v60-local"
    connected: bool = True
    last_error: str | None = None
    last_snapshot_at: datetime | None = None
    last_command_at: datetime | None = None
    pending_command_count: int = Field(default=0, ge=0)


class MT5V60BridgeSnapshot(BaseModel):
    bridge_id: str = "mt5-v60-local"
    sequence: int = Field(default=0, ge=0)
    received_at: datetime | None = None
    server_time: datetime
    symbol: str
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    spread_bps: float | None = None
    symbol_spec: MT5V60SymbolSpec
    bars_1m: list[MT5V60Bar] = Field(default_factory=list)
    bars_2m: list[MT5V60Bar] = Field(default_factory=list)
    bars_3m: list[MT5V60Bar] = Field(default_factory=list)
    bars_5m: list[MT5V60Bar] = Field(default_factory=list)
    account: MT5V60AccountSnapshot
    open_tickets: list[MT5V60LiveTicket] = Field(default_factory=list)
    chart_screenshot: MT5V60ChartScreenshot = Field(default_factory=MT5V60ChartScreenshot)
    recent_close_events: list[MT5V60CloseEvent] = Field(default_factory=list)
    pending_command_ids: list[str] = Field(default_factory=list)
    event_reasons: list[str] = Field(default_factory=list)
    health: MT5V60BridgeHealth = Field(default_factory=MT5V60BridgeHealth)

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")


class MT5V60BridgeCommand(BaseModel):
    command_id: str
    command_type: Literal["place_entry", "modify_ticket", "close_ticket"]
    symbol: str
    created_at: datetime
    expires_at: datetime | None = None
    ticket_id: str | None = None
    basket_id: str | None = None
    side: Literal["long", "short"] | None = None
    volume_lots: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    comment: str | None = None
    magic_number: int | None = None
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MT5V60ExecutionAck(BaseModel):
    command_id: str
    status: Literal["accepted", "rejected", "filled", "partial_fill", "applied", "expired", "ignored"]
    broker_time: datetime | None = None
    ticket_id: str | None = None
    message: str | None = None
    fill_price: Decimal | None = Field(default=None, gt=0)
    fill_volume_lots: Decimal | None = Field(default=None, gt=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class MT5V60BridgeCommandPollResponse(BaseModel):
    commands: list[MT5V60BridgeCommand] = Field(default_factory=list)


class MT5V60EntryDecision(BaseModel):
    action: Literal["enter_long", "enter_short", "hold"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    thesis_tags: list[str] = Field(default_factory=list)
    requested_risk_fraction: float | None = Field(default=None, ge=0, le=1)
    stop_loss_price: Decimal | None = Field(default=None, gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    context_signature: str | None = None


class MT5V60ManagementCommand(BaseModel):
    action: Literal["hold", "modify_ticket", "close_partial", "close_ticket"]
    stop_loss_price: Decimal | None = Field(default=None, gt=0)
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    close_fraction: float | None = Field(default=None, gt=0, le=1)


class MT5V60ManagementDecision(BaseModel):
    ticket_id: str
    confidence: float = Field(ge=0, le=1)
    rationale: str
    commands: list[MT5V60ManagementCommand] = Field(default_factory=list)
    visual_context_update: dict[str, Any] | None = None


class MT5V60ManagementDecisionBatch(BaseModel):
    decisions: list[MT5V60ManagementDecision] = Field(default_factory=list)


class MT5V60RiskDecision(BaseModel):
    approved: bool
    reason: str
    risk_fraction: float | None = Field(default=None, ge=0, le=1)
    risk_posture: Literal["reduced", "neutral", "mildly_aggressive"] = "neutral"


class MT5V60EntryPlan(BaseModel):
    symbol: str
    side: Literal["long", "short"]
    volume_lots: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    risk_fraction: float = Field(gt=0, le=1)
    risk_amount_usd: Decimal = Field(ge=0)
    r_distance_price: Decimal = Field(gt=0)
    analysis_mode: Literal["standard_entry", "stop_loss_reversal"] = "standard_entry"
    basket_id: str
    magic_number: int
    comment: str


class MT5V60TicketRecord(BaseModel):
    ticket_id: str
    symbol: str
    side: Literal["long", "short"]
    basket_id: str | None = None
    entry_command_id: str | None = None
    magic_number: int | None = None
    original_volume_lots: Decimal = Field(gt=0)
    current_volume_lots: Decimal = Field(gt=0)
    open_price: Decimal = Field(gt=0)
    current_price: Decimal = Field(gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    initial_stop_loss: Decimal = Field(gt=0)
    hard_take_profit: Decimal = Field(gt=0)
    r_distance_price: Decimal = Field(gt=0)
    risk_amount_usd: Decimal = Field(ge=0)
    analysis_mode: Literal["standard_entry", "stop_loss_reversal"] = "standard_entry"
    highest_favorable_close: Decimal
    lowest_favorable_close: Decimal
    thesis_tags: list[str] = Field(default_factory=list)
    context_signature: str | None = None
    followed_lessons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    opened_at: datetime
    last_seen_at: datetime
    last_close_reason: Literal["stop_loss", "take_profit", "manual_or_command", "unknown"] | None = None
    is_open: bool = True
    unrealized_pnl_usd: Decimal = Decimal("0")
    unrealized_r: float = 0.0


class MT5V60ScreenshotState(BaseModel):
    absolute_path: str | None = None
    latest_screenshot_capture_ts: datetime | None = None
    latest_screenshot_fingerprint: str | None = None
    last_manager_image_sent_fingerprint: str | None = None
    cached_visual_context: dict[str, Any] | None = None
    cached_visual_context_capture_ts: datetime | None = None
