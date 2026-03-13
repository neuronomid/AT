create table if not exists public.agent_configs (
  id uuid primary key default gen_random_uuid(),
  agent_name text not null unique,
  description text,
  status text not null default 'active' check (status in ('active', 'paused', 'shadow', 'stopped')),
  broker text not null default 'alpaca',
  mode text not null default 'paper' check (mode in ('paper', 'simulation', 'disabled')),
  symbols jsonb not null default '["ETH/USD"]'::jsonb,
  decision_interval_seconds integer not null default 60 check (decision_interval_seconds >= 5),
  max_trades_per_hour integer not null default 6 check (max_trades_per_hour >= 1),
  max_risk_per_trade_pct numeric(8, 6) not null default 0.005 check (max_risk_per_trade_pct > 0 and max_risk_per_trade_pct <= 1),
  max_daily_loss_pct numeric(8, 6) not null default 0.02 check (max_daily_loss_pct > 0 and max_daily_loss_pct <= 1),
  max_position_notional_usd numeric(20, 8) not null default 100,
  max_spread_bps numeric(12, 4) not null default 20,
  min_decision_confidence numeric(5, 4) not null default 0.6 check (min_decision_confidence >= 0 and min_decision_confidence <= 1),
  cooldown_seconds_after_trade integer not null default 60 check (cooldown_seconds_after_trade >= 0),
  enable_agent_orders boolean not null default false,
  strategy_policy_version_id uuid references public.policy_versions(id) on delete set null,
  risk_params jsonb not null default '{}'::jsonb,
  analyst_params jsonb not null default '{}'::jsonb,
  execution_params jsonb not null default '{}'::jsonb,
  notes text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  check (jsonb_typeof(symbols) = 'array')
);

create table if not exists public.agent_heartbeats (
  id uuid primary key default gen_random_uuid(),
  agent_config_id uuid not null references public.agent_configs(id) on delete cascade,
  runtime_id text not null,
  status text not null default 'healthy' check (status in ('healthy', 'degraded', 'paused', 'stopped', 'error')),
  current_symbol text,
  latest_decision_action text,
  latest_decision_at timestamptz,
  latest_order_at timestamptz,
  open_position_qty numeric(20, 8),
  cash numeric(20, 8),
  equity numeric(20, 8),
  details jsonb not null default '{}'::jsonb,
  observed_at timestamptz not null default timezone('utc', now()),
  unique (agent_config_id, runtime_id)
);

create table if not exists public.backtest_jobs (
  id uuid primary key default gen_random_uuid(),
  requested_by text not null default 'dashboard',
  agent_config_id uuid references public.agent_configs(id) on delete set null,
  run_name text not null,
  status text not null default 'queued' check (status in ('queued', 'running', 'completed', 'failed')),
  symbol text not null,
  timeframe text not null,
  location text not null default 'us',
  lookback_days integer not null check (lookback_days >= 1),
  train_window_days integer not null check (train_window_days >= 1),
  test_window_days integer not null check (test_window_days >= 1),
  step_days integer not null check (step_days >= 1),
  warmup_bars integer not null check (warmup_bars >= 1),
  starting_cash_usd numeric(20, 8) not null,
  baseline_policy_version_id uuid references public.policy_versions(id) on delete set null,
  candidate_policy_version_ids jsonb not null default '[]'::jsonb,
  run_id uuid references public.backtest_runs(id) on delete set null,
  notes text,
  error_message text,
  requested_at timestamptz not null default timezone('utc', now()),
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  check (jsonb_typeof(candidate_policy_version_ids) = 'array')
);

alter table public.decisions
  add column if not exists agent_config_id uuid references public.agent_configs(id) on delete set null,
  add column if not exists agent_name text not null default 'primary';

alter table public.orders
  add column if not exists agent_config_id uuid references public.agent_configs(id) on delete set null,
  add column if not exists agent_name text not null default 'primary';

alter table public.trade_outcomes
  add column if not exists agent_config_id uuid references public.agent_configs(id) on delete set null,
  add column if not exists agent_name text not null default 'primary';

alter table public.backtest_runs
  add column if not exists agent_config_id uuid references public.agent_configs(id) on delete set null,
  add column if not exists agent_name text not null default 'primary',
  add column if not exists backtest_job_id uuid references public.backtest_jobs(id) on delete set null;

create index if not exists agent_configs_status_idx
  on public.agent_configs (status, updated_at desc);

create index if not exists agent_heartbeats_agent_observed_idx
  on public.agent_heartbeats (agent_config_id, observed_at desc);

create index if not exists backtest_jobs_status_requested_idx
  on public.backtest_jobs (status, requested_at desc);

create index if not exists decisions_agent_recorded_idx
  on public.decisions (agent_name, recorded_at desc);

create index if not exists orders_agent_submitted_idx
  on public.orders (agent_name, submitted_at desc);

create index if not exists trade_outcomes_agent_recorded_idx
  on public.trade_outcomes (agent_name, recorded_at desc);

create index if not exists backtest_runs_agent_created_idx
  on public.backtest_runs (agent_name, created_at desc);

create trigger agent_configs_set_updated_at
before update on public.agent_configs
for each row
execute function public.set_updated_at();

create trigger backtest_jobs_set_updated_at
before update on public.backtest_jobs
for each row
execute function public.set_updated_at();

create or replace view public.agent_dashboard_status as
with latest_heartbeats as (
  select distinct on (agent_config_id)
    agent_config_id,
    runtime_id,
    status as runtime_status,
    current_symbol,
    latest_decision_action,
    latest_decision_at,
    latest_order_at,
    open_position_qty,
    cash,
    equity,
    details,
    observed_at
  from public.agent_heartbeats
  order by agent_config_id, observed_at desc
)
select
  config.id,
  config.agent_name,
  config.description,
  config.status as configured_status,
  config.mode,
  config.broker,
  config.symbols,
  config.decision_interval_seconds,
  config.max_trades_per_hour,
  config.max_risk_per_trade_pct,
  config.max_daily_loss_pct,
  config.max_position_notional_usd,
  config.max_spread_bps,
  config.min_decision_confidence,
  config.cooldown_seconds_after_trade,
  config.enable_agent_orders,
  config.strategy_policy_version_id,
  policy.policy_name as strategy_policy_name,
  policy.version as strategy_version,
  heartbeat.runtime_id,
  heartbeat.runtime_status,
  heartbeat.current_symbol,
  heartbeat.latest_decision_action,
  heartbeat.latest_decision_at,
  heartbeat.latest_order_at,
  heartbeat.open_position_qty,
  heartbeat.cash,
  heartbeat.equity,
  heartbeat.details,
  heartbeat.observed_at,
  config.notes,
  config.created_at,
  config.updated_at
from public.agent_configs as config
left join latest_heartbeats as heartbeat on heartbeat.agent_config_id = config.id
left join public.policy_versions as policy on policy.id = config.strategy_policy_version_id;

insert into public.agent_configs (
  agent_name,
  description,
  status,
  broker,
  mode,
  symbols,
  decision_interval_seconds,
  max_trades_per_hour,
  max_risk_per_trade_pct,
  max_daily_loss_pct,
  max_position_notional_usd,
  max_spread_bps,
  min_decision_confidence,
  cooldown_seconds_after_trade,
  enable_agent_orders,
  notes
)
values (
  'primary',
  'Default paper-trading research agent.',
  'active',
  'alpaca',
  'paper',
  '["ETH/USD"]'::jsonb,
  60,
  6,
  0.005,
  0.02,
  100,
  20,
  0.6,
  60,
  false,
  'Created automatically for the dashboard control plane.'
)
on conflict (agent_name) do nothing;
