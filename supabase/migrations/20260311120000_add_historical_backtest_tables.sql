create table if not exists public.market_bars (
  id uuid primary key default gen_random_uuid(),
  symbol text not null,
  timeframe text not null,
  location text not null default 'us',
  bar_timestamp timestamptz not null,
  open_price numeric(20, 8) not null,
  high_price numeric(20, 8) not null,
  low_price numeric(20, 8) not null,
  close_price numeric(20, 8) not null,
  volume numeric(20, 8) not null default 0,
  trade_count integer not null default 0,
  vwap numeric(20, 8),
  source text not null default 'alpaca',
  raw_bar jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  unique (symbol, timeframe, location, bar_timestamp)
);

create table if not exists public.backtest_runs (
  id uuid primary key default gen_random_uuid(),
  run_name text not null,
  symbol text not null,
  timeframe text not null,
  location text not null default 'us',
  start_at timestamptz not null,
  end_at timestamptz not null,
  train_window_days integer not null,
  test_window_days integer not null,
  step_days integer not null,
  warmup_bars integer not null,
  starting_cash_usd numeric(20, 8) not null,
  bars_inserted integer not null default 0,
  total_bars integer not null default 0,
  status text not null default 'running' check (status in ('running', 'completed', 'failed')),
  baseline_policy_version_id uuid references public.policy_versions(id) on delete set null,
  candidate_policy_version_id uuid references public.policy_versions(id) on delete set null,
  baseline_metrics jsonb not null default '{}'::jsonb,
  candidate_metrics jsonb not null default '{}'::jsonb,
  decision_payload jsonb not null default '{}'::jsonb,
  notes text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.backtest_window_results (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.backtest_runs(id) on delete cascade,
  window_index integer not null,
  policy_version_id uuid references public.policy_versions(id) on delete set null,
  policy_name text not null,
  train_start_at timestamptz not null,
  train_end_at timestamptz not null,
  test_start_at timestamptz not null,
  test_end_at timestamptz not null,
  metrics jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  unique (run_id, window_index, policy_name)
);

create table if not exists public.backtest_trades (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.backtest_runs(id) on delete cascade,
  window_id uuid references public.backtest_window_results(id) on delete set null,
  policy_version_id uuid references public.policy_versions(id) on delete set null,
  policy_name text not null,
  symbol text not null,
  side text not null check (side in ('buy', 'sell')),
  entry_at timestamptz not null,
  exit_at timestamptz not null,
  entry_price numeric(20, 8) not null,
  exit_price numeric(20, 8) not null,
  qty numeric(20, 8) not null,
  notional_usd numeric(20, 8) not null,
  pnl_usd numeric(20, 8) not null,
  return_bps numeric(12, 4) not null,
  bars_held integer not null default 0,
  exit_reason text not null,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists market_bars_symbol_timeframe_timestamp_idx
  on public.market_bars (symbol, timeframe, location, bar_timestamp asc);

create index if not exists backtest_runs_symbol_created_at_idx
  on public.backtest_runs (symbol, created_at desc);

create index if not exists backtest_window_results_run_id_idx
  on public.backtest_window_results (run_id, window_index asc);

create index if not exists backtest_trades_run_id_policy_idx
  on public.backtest_trades (run_id, policy_name, entry_at asc);

create trigger backtest_runs_set_updated_at
before update on public.backtest_runs
for each row
execute function public.set_updated_at();
