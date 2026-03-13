export type DashboardView = 'overview' | 'agents' | 'strategies' | 'backtests' | 'history'

export interface OverviewSummary {
  configured_agents: number
  active_agents: number
  healthy_agents: number
  recent_decisions: number
  recent_orders: number
  recent_outcomes: number
}

export interface AgentStatus {
  id: string
  agent_name: string
  configured_status: string
  runtime_status: string | null
  current_symbol: string | null
  latest_decision_action: string | null
  latest_decision_at: string | null
  latest_order_at: string | null
  equity: number | null
  cash: number | null
  symbols: string[]
  strategy_policy_name: string | null
  strategy_version: string | null
}

export interface AgentConfig {
  id?: string
  agent_name: string
  description?: string | null
  status: 'active' | 'paused' | 'shadow' | 'stopped'
  broker: string
  mode: 'paper' | 'simulation' | 'disabled'
  symbols: string[]
  decision_interval_seconds: number
  max_trades_per_hour: number
  max_risk_per_trade_pct: number
  max_daily_loss_pct: number
  max_position_notional_usd: number
  max_spread_bps: number
  min_decision_confidence: number
  cooldown_seconds_after_trade: number
  enable_agent_orders: boolean
  strategy_policy_version_id?: string | null
  risk_params: Record<string, unknown>
  analyst_params: Record<string, unknown>
  execution_params: Record<string, unknown>
  notes?: string | null
}

export interface PolicyVersion {
  id: string
  policy_name: string
  version: string
  status: string
  thresholds: Record<string, number>
  risk_params: Record<string, unknown>
  strategy_config: Record<string, unknown>
  notes?: string | null
}

export interface BacktestJob {
  id: string
  requested_by: string
  run_name: string
  status: string
  symbol: string
  timeframe: string
  lookback_days: number
  train_window_days: number
  test_window_days: number
  step_days: number
  warmup_bars: number
  starting_cash_usd: number
  notes?: string | null
  error_message?: string | null
  requested_at: string
  started_at?: string | null
  completed_at?: string | null
  run_id?: string | null
}

export interface MetricsPayload {
  score?: number
  realized_pnl_bps?: number
  average_trade_bps?: number
  max_drawdown_bps?: number
  exposure_ratio?: number
  win_rate?: number
  closed_trades?: number
  opened_trades?: number
  samples?: number
}

export interface BacktestRun {
  id: string
  run_name: string
  status: string
  symbol: string
  timeframe: string
  location: string
  created_at: string
  start_at: string
  end_at: string
  agent_name: string
  total_bars: number
  baseline_metrics: MetricsPayload
  candidate_metrics: MetricsPayload
  decision_payload: {
    status?: string
    reason?: string
  }
  baseline_policy_name?: string | null
  baseline_version?: string | null
  candidate_policy_name?: string | null
  candidate_version?: string | null
}

export interface BacktestWindowResult {
  id: string
  window_index: number
  policy_name: string
  train_start_at: string
  train_end_at: string
  test_start_at: string
  test_end_at: string
  metrics: {
    train_scores?: Record<string, number>
    selected_policy_name?: string
    metrics?: MetricsPayload
    baseline_metrics?: MetricsPayload
  }
}

export interface BacktestTrade {
  policy_name: string
  symbol: string
  side: string
  entry_at: string
  exit_at: string
  entry_price: number
  exit_price: number
  qty: number
  notional_usd: number
  pnl_usd: number
  return_bps: number
  bars_held: number
  exit_reason: string
}

export interface BacktestRunDetail {
  run: BacktestRun
  windows: BacktestWindowResult[]
  trades: BacktestTrade[]
}

export interface PromotionRecord {
  id: string
  agent_config_id: string
  agent_name: string
  previous_policy_version_id?: string | null
  previous_policy_name?: string | null
  previous_policy_version?: string | null
  new_policy_version_id: string
  new_policy_name?: string | null
  new_policy_version?: string | null
  source_run_id?: string | null
  source_run_name?: string | null
  promoted_by: string
  rationale: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface HistoryRecord {
  recorded_at?: string
  created_at?: string
  agent_name?: string
  symbol?: string
  action?: string
  outcome?: string
  summary?: string
  rationale?: string
  status?: string
  [key: string]: unknown
}

export interface OverviewPayload {
  summary: OverviewSummary
  agent_statuses: AgentStatus[]
  decisions: HistoryRecord[]
  orders: HistoryRecord[]
  outcomes: HistoryRecord[]
  lessons: HistoryRecord[]
  backtest_runs: BacktestRun[]
  backtest_jobs: BacktestJob[]
  promotions: PromotionRecord[]
}

export interface HistoryPayload {
  decisions: HistoryRecord[]
  orders: HistoryRecord[]
  outcomes: HistoryRecord[]
  lessons: HistoryRecord[]
  promotions: PromotionRecord[]
}
