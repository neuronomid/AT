from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class MarketSnapshot(BaseModel):
    symbol: str
    timestamp: datetime
    event_type: Literal["quote", "trade"] | None = None
    open_price: Decimal | None = None
    high_price: Decimal | None = None
    low_price: Decimal | None = None
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
    avg_entry_price: Decimal = Decimal("0")
    market_value: Decimal = Decimal("0")
    unrealized_pl: Decimal = Decimal("0")
    trading_blocked: bool = False
    account_status: str = ""
    crypto_status: str = ""


class TradePlan(BaseModel):
    stop_loss_bps: float = Field(gt=0)
    take_profit_bps: float = Field(gt=0)
    max_take_profit_bps: float = Field(gt=0)
    trailing_stop_bps: float = Field(gt=0)
    time_stop_bars: int = Field(ge=1)
    partial_take_profit_fraction: float = Field(gt=0, le=1)


class ExecutionPlan(BaseModel):
    requested_notional_usd: float | None = Field(default=None, gt=0)
    order_type: Literal["market", "limit"] = "market"
    time_in_force: Literal["gtc", "ioc"] = "gtc"
    entry_reference_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)
    max_take_profit_price: float | None = Field(default=None, gt=0)
    expected_slippage_bps: float = Field(default=0.0, ge=0)
    planned_risk_usd: float | None = Field(default=None, ge=0)


class TradeDecision(BaseModel):
    action: Literal["buy", "sell", "hold", "exit", "do_nothing", "reduce"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    regime: str | None = None
    regime_probability: float = Field(default=0.0, ge=0, le=1)
    regime_probabilities: dict[str, float] = Field(default_factory=dict)
    continuation_probabilities: dict[str, float] = Field(default_factory=dict)
    expected_edge_bps: float | None = None
    signal_quality_score: int = Field(default=0, ge=0)
    confirmation_count: int = Field(default=0, ge=0)
    entry_blockers: list[str] = Field(default_factory=list)
    trade_plan: TradePlan | None = None
    execution_plan: ExecutionPlan | None = None
    planned_risk_usd: float | None = Field(default=None, ge=0)
    risk_fraction_equity: float | None = Field(default=None, ge=0)
    take_profit_r: float | None = Field(default=None, ge=0)
    reduce_fraction: float | None = Field(default=None, gt=0, le=1)
    thesis_tags: list[str] = Field(default_factory=list)
    context_signature: str | None = None


class LLMRuntimeDecision(BaseModel):
    action: Literal["buy", "reduce", "exit", "do_nothing"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    risk_fraction_equity: float | None = Field(default=None, ge=0)
    take_profit_r: float | None = Field(default=None, ge=0)
    reduce_fraction: float | None = Field(default=None, gt=0, le=1)
    thesis_tags: list[str] = Field(default_factory=list)


class LiveCandle(BaseModel):
    symbol: str
    start_at: datetime
    end_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal = Decimal("0")
    trade_count: int = 0
    vwap: Decimal | None = None
    bid_price: Decimal | None = None
    ask_price: Decimal | None = None
    spread_bps: float | None = None
    body_pct: float = 0.0
    upper_wick_pct: float = 0.0
    lower_wick_pct: float = 0.0
    close_range_position: float = 0.5


class TradeReflection(BaseModel):
    reflection_id: str
    symbol: str
    side: Literal["long", "short"]
    opened_at: datetime
    closed_at: datetime
    bars_held: int = Field(ge=0)
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    realized_pnl_usd: Decimal
    realized_r: float
    mae_r: float = 0.0
    mfe_r: float = 0.0
    exit_reason: str
    spread_bps_entry: float | None = None
    spread_bps_exit: float | None = None
    thesis_tags: list[str] = Field(default_factory=list)
    context_signature: str | None = None
    entry_packet_summary: dict[str, Any] = Field(default_factory=dict)
    followed_lessons: list[str] = Field(default_factory=list)
    avoid_lessons: list[str] = Field(default_factory=list)
    reinforce_lessons: list[str] = Field(default_factory=list)


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
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class MT5AccountSnapshot(BaseModel):
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


class MT5Bar(BaseModel):
    timeframe: str
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


class TicketState(BaseModel):
    ticket_id: str
    symbol: str
    side: Literal["long", "short"]
    volume_lots: Decimal = Field(gt=0)
    open_price: Decimal = Field(gt=0)
    current_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    initial_stop_loss: Decimal | None = Field(default=None, gt=0)
    initial_take_profit: Decimal | None = Field(default=None, gt=0)
    risk_amount_usd: Decimal | None = Field(default=None, ge=0)
    unrealized_pnl_usd: Decimal = Decimal("0")
    unrealized_r: float = 0.0
    partial_taken: bool = False
    protected: bool = False
    opened_at: datetime | None = None
    magic_number: int | None = None
    comment: str | None = None
    basket_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BasketState(BaseModel):
    basket_id: str
    symbol: str
    side: Literal["long", "short"]
    ticket_ids: list[str] = Field(default_factory=list)
    open_risk_usd: Decimal = Decimal("0")
    protected_ticket_ids: list[str] = Field(default_factory=list)
    opened_at: datetime | None = None


class BridgeHealth(BaseModel):
    bridge_id: str = "mt5-local"
    connected: bool = True
    last_error: str | None = None
    last_snapshot_at: datetime | None = None
    last_command_at: datetime | None = None
    pending_command_count: int = Field(default=0, ge=0)


class BridgeSnapshot(BaseModel):
    bridge_id: str = "mt5-local"
    sequence: int = Field(default=0, ge=0)
    received_at: datetime | None = None
    server_time: datetime
    symbol: str
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    spread_bps: float | None = None
    bars_5m: list[MT5Bar] = Field(default_factory=list)
    bars_15m: list[MT5Bar] = Field(default_factory=list)
    bars_4h: list[MT5Bar] = Field(default_factory=list)
    account: MT5AccountSnapshot
    open_tickets: list[TicketState] = Field(default_factory=list)
    pending_command_ids: list[str] = Field(default_factory=list)
    event_reasons: list[str] = Field(default_factory=list)
    health: BridgeHealth = Field(default_factory=BridgeHealth)

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")


class BridgeCommand(BaseModel):
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


class ExecutionAck(BaseModel):
    command_id: str
    status: Literal["accepted", "rejected", "filled", "partial_fill", "applied", "expired", "ignored"]
    broker_time: datetime | None = None
    ticket_id: str | None = None
    message: str | None = None
    fill_price: Decimal | None = Field(default=None, gt=0)
    fill_volume_lots: Decimal | None = Field(default=None, gt=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class BridgeCommandPollResponse(BaseModel):
    commands: list[BridgeCommand] = Field(default_factory=list)


class EntryDecision(BaseModel):
    action: Literal["enter_long", "enter_short", "hold"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    thesis_tags: list[str] = Field(default_factory=list)
    requested_risk_fraction: float | None = Field(default=None, ge=0, le=1)
    context_signature: str | None = None


class ManagementDecision(BaseModel):
    ticket_id: str
    action: Literal["hold", "take_partial_50", "move_stop_to_breakeven", "trail_stop_to_rule", "close_ticket"]
    confidence: float = Field(ge=0, le=1)
    rationale: str


class ManagementDecisionBatch(BaseModel):
    decisions: list[ManagementDecision] = Field(default_factory=list)


class MT5RiskDecision(BaseModel):
    approved: bool
    reason: str
    risk_fraction: float | None = Field(default=None, ge=0, le=1)
    risk_posture: Literal["reduced", "neutral", "mildly_aggressive"] = "neutral"


class MT5EntryPlan(BaseModel):
    symbol: str
    side: Literal["long", "short"]
    volume_lots: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profit: Decimal = Field(gt=0)
    risk_fraction: float = Field(gt=0, le=1)
    risk_amount_usd: Decimal = Field(ge=0)
    stop_distance_pips: float = Field(gt=0)
    take_profit_distance_pips: float = Field(gt=0)
    basket_id: str
    magic_number: int
    comment: str


class StrategyAdvice(BaseModel):
    generated_at: datetime
    model: str
    summary: str
    recommendations: list[str] = Field(default_factory=list)
    prompt: str
    raw_response: str


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

    def to_market_snapshot(self) -> MarketSnapshot:
        return MarketSnapshot(
            symbol=self.symbol,
            timestamp=self.timestamp,
            open_price=self.open_price,
            high_price=self.high_price,
            low_price=self.low_price,
            last_trade_price=self.close_price,
            last_trade_size=self.volume,
        )


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
    fees_usd: Decimal = Decimal("0")
    slippage_bps: float = 0.0
    fill_ratio: float = Field(default=1.0, ge=0, le=1)
    entry_regime: str | None = None
    entry_regime_probability: float | None = Field(default=None, ge=0, le=1)
    entry_regime_probabilities: dict[str, float] = Field(default_factory=dict)
    entry_continuation_probabilities: dict[str, float] = Field(default_factory=dict)
    planned_stop_loss_bps: float | None = Field(default=None, gt=0)
    planned_take_profit_bps: float | None = Field(default=None, gt=0)
    planned_max_take_profit_bps: float | None = Field(default=None, gt=0)
    planned_trailing_stop_bps: float | None = Field(default=None, gt=0)
    planned_time_stop_bars: int | None = Field(default=None, ge=1)
    planned_risk_usd: float | None = Field(default=None, ge=0)


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


class BacktestTradeSummary(BaseModel):
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    win_rate: float = 0.0
    average_planned_risk_usd: float = 0.0
    average_planned_stop_loss_bps: float = 0.0
    average_planned_take_profit_bps: float = 0.0
    average_planned_max_take_profit_bps: float = 0.0
    average_planned_trailing_stop_bps: float = 0.0
    average_bars_held: float = 0.0
    exit_reason_counts: dict[str, int] = Field(default_factory=dict)


class BacktestRegimeSummary(BaseModel):
    regime_occupancy: dict[str, int] = Field(default_factory=dict)
    entry_regime_counts: dict[str, int] = Field(default_factory=dict)
    average_regime_probability: dict[str, float] = Field(default_factory=dict)


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
    trade_summary: BacktestTradeSummary | None = None
    regime_summary: BacktestRegimeSummary | None = None


class DiscoveryDatasetSummary(BaseModel):
    symbol: str
    timeframe: str
    start_at: datetime
    end_at: datetime
    warmup_start_at: datetime
    total_bars: int = 0
    evaluation_bars: int = 0
    evaluable_bars: int = 0
    estimated_round_trip_cost_bps: float = 0.0


class DiscoveryRegimeSummary(BaseModel):
    regime_occupancy: dict[str, int] = Field(default_factory=dict)
    regime_transitions: dict[str, int] = Field(default_factory=dict)
    average_forward_60m_bps: dict[str, float] = Field(default_factory=dict)
    average_probability: dict[str, float] = Field(default_factory=dict)


class IndicatorBucketTable(BaseModel):
    indicator: str
    direction: Literal["long", "short"]
    buckets: dict[str, float] = Field(default_factory=dict)


class PatternFinding(BaseModel):
    direction: Literal["long", "short"]
    regime: str
    support_count: int = 0
    score_bps: float = 0.0
    estimated_round_trip_cost_bps: float = 0.0
    forward_15m_mean_bps: float = 0.0
    forward_30m_mean_bps: float = 0.0
    forward_60m_mean_bps: float = 0.0
    mean_favorable_excursion_bps: float = 0.0
    mean_adverse_excursion_bps: float = 0.0
    percentile_60_favorable_excursion_bps: float = 0.0
    percentile_60_adverse_excursion_bps: float = 0.0
    percentile_85_favorable_excursion_bps: float = 0.0
    median_bars_to_peak_favorable: int = 0
    thresholds: dict[str, float] = Field(default_factory=dict)
    atr_band: list[float] = Field(default_factory=list)


class DiscoveredStrategySpec(BaseModel):
    policy_name: str
    version: str
    policy_label: str
    direction: Literal["long_flat", "inverse_research"]
    source_regime: str
    thresholds: dict[str, Any] = Field(default_factory=dict)
    strategy_config: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    selected_pattern: PatternFinding


class InverseAppendixSummary(BaseModel):
    enabled: bool = True
    headline: str = ""
    selected_pattern: PatternFinding | None = None
    strategy: DiscoveredStrategySpec | None = None


class DiscoveryReport(BaseModel):
    dataset: DiscoveryDatasetSummary
    regime_summary: DiscoveryRegimeSummary
    indicator_bucket_tables: list[IndicatorBucketTable] = Field(default_factory=list)
    headline_findings: list[str] = Field(default_factory=list)
    long_patterns: list[PatternFinding] = Field(default_factory=list)
    selected_pattern: PatternFinding | None = None
    candidate_strategy: DiscoveredStrategySpec | None = None
    inverse_appendix: InverseAppendixSummary | None = None
