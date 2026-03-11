from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class MarketSnapshot(BaseModel):
    symbol: str
    timestamp: datetime
    bid_price: Decimal | None = None
    bid_size: Decimal | None = None
    ask_price: Decimal | None = None
    ask_size: Decimal | None = None
    last_trade_price: Decimal | None = None
    last_trade_size: Decimal | None = None


class AccountSnapshot(BaseModel):
    equity: Decimal = Decimal("0")
    cash: Decimal = Decimal("0")
    buying_power: Decimal = Decimal("0")
    open_position_qty: Decimal = Decimal("0")
    trading_blocked: bool = False
    account_status: str = ""
    crypto_status: str = ""


class TradeDecision(BaseModel):
    action: Literal["buy", "sell", "hold", "exit", "do_nothing"]
    confidence: float = Field(ge=0, le=1)
    rationale: str


class RiskDecision(BaseModel):
    approved: bool
    reason: str
    allowed_notional_usd: Decimal = Decimal("0")


class OrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop_limit"] = "market"
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    notional: Decimal | None = None
    qty: Decimal | None = None


class OrderSnapshot(BaseModel):
    id: str
    client_order_id: str
    symbol: str
    side: str
    type: str
    time_in_force: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    qty: Decimal | None = None
    notional: Decimal | None = None
    filled_qty: Decimal | None = None
    filled_avg_price: Decimal | None = None


class TradeUpdate(BaseModel):
    event: str
    order: OrderSnapshot
    timestamp: datetime | None = None
    price: Decimal | None = None
    qty: Decimal | None = None


class TradeReview(BaseModel):
    review_id: str
    order_id: str
    symbol: str
    action: str
    outcome: Literal[
        "entry_opened",
        "position_reduced",
        "rejected",
        "canceled",
        "expired",
        "timed_out",
        "state_mismatch",
    ]
    summary: str
    decision_confidence: float = Field(ge=0, le=1)
    spread_bps: float | None = None
    failure_mode: str | None = None
    cash_delta: Decimal = Decimal("0")
    position_qty_delta: Decimal = Decimal("0")
    filled_qty: Decimal | None = None
    filled_avg_price: Decimal | None = None
    lesson_candidates: list[str] = Field(default_factory=list)


class LessonRecord(BaseModel):
    lesson_id: str
    category: str
    message: str
    confidence: float = Field(ge=0, le=1)
    source: str


class ReviewSummary(BaseModel):
    total_records: int = 0
    decision_records: int = 0
    trade_reviews: int = 0
    executable_decisions: int = 0
    risk_rejections: int = 0
    action_counts: dict[str, int] = Field(default_factory=dict)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    review_outcomes: dict[str, int] = Field(default_factory=dict)
    lessons: list[LessonRecord] = Field(default_factory=list)


class ReplayMetrics(BaseModel):
    policy_name: str
    samples: int = 0
    executed_actions: int = 0
    opened_trades: int = 0
    closed_trades: int = 0
    action_counts: dict[str, int] = Field(default_factory=dict)
    win_rate: float = 0.0
    realized_pnl_bps: float = 0.0
    average_trade_bps: float = 0.0
    max_drawdown_bps: float = 0.0
    exposure_ratio: float = 0.0
    score: float = 0.0


class PromotionDecision(BaseModel):
    status: Literal["promote", "reject", "insufficient_data"]
    recommended: bool
    reason: str
    baseline_policy: str
    candidate_policy: str
    baseline_score: float
    candidate_score: float


class EvaluationReport(BaseModel):
    baseline: ReplayMetrics
    candidate: ReplayMetrics
    decision: PromotionDecision


class HistoricalBar(BaseModel):
    symbol: str
    timeframe: str
    location: str
    timestamp: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal = Decimal("0")
    trade_count: int = 0
    vwap: Decimal | None = None
    raw_bar: dict[str, object] = Field(default_factory=dict)


class BacktestTradeRecord(BaseModel):
    policy_name: str
    symbol: str
    side: Literal["buy", "sell"]
    entry_at: datetime
    exit_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    notional_usd: Decimal
    pnl_usd: Decimal
    return_bps: float
    bars_held: int
    exit_reason: str


class BacktestWindowSummary(BaseModel):
    window_index: int
    selected_policy_name: str
    train_start_at: datetime
    train_end_at: datetime
    test_start_at: datetime
    test_end_at: datetime
    train_scores: dict[str, float] = Field(default_factory=dict)
    baseline_test_metrics: ReplayMetrics
    selected_test_metrics: ReplayMetrics


class BacktestReport(BaseModel):
    symbol: str
    timeframe: str
    location: str
    start_at: datetime
    end_at: datetime
    total_bars: int
    bars_inserted: int
    baseline: ReplayMetrics
    candidate: ReplayMetrics
    decision: PromotionDecision
    windows: list[BacktestWindowSummary] = Field(default_factory=list)
