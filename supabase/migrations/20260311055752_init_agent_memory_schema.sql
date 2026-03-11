create extension if not exists pgcrypto with schema extensions;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create table if not exists public.policy_versions (
  id uuid primary key default gen_random_uuid(),
  policy_name text not null,
  version text not null,
  status text not null default 'candidate' check (status in ('candidate', 'active', 'retired', 'rejected', 'baseline', 'shadow')),
  model_name text,
  prompt_version text,
  feature_set_version text,
  thresholds jsonb not null default '{}'::jsonb,
  risk_params jsonb not null default '{}'::jsonb,
  strategy_config jsonb not null default '{}'::jsonb,
  notes text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  promoted_at timestamptz,
  unique (policy_name, version)
);

create table if not exists public.active_policy (
  singleton boolean primary key default true check (singleton),
  policy_version_id uuid not null references public.policy_versions(id) on delete restrict,
  activated_at timestamptz not null default timezone('utc', now()),
  activated_by text,
  rationale text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.decisions (
  id uuid primary key default gen_random_uuid(),
  symbol text not null,
  action text not null check (action in ('buy', 'sell', 'hold', 'exit', 'do_nothing')),
  decision_confidence numeric(5, 4) not null check (decision_confidence >= 0 and decision_confidence <= 1),
  rationale text,
  risk_approved boolean,
  risk_reason text,
  allowed_notional_usd numeric(20, 8),
  trades_this_hour integer,
  reference_price numeric(20, 8),
  spread_bps numeric(12, 4),
  market_timestamp timestamptz,
  policy_version_id uuid references public.policy_versions(id) on delete set null,
  analyst_model text,
  analyst_prompt_version text,
  record_source text not null default 'agent',
  notes text,
  market_snapshot jsonb not null default '{}'::jsonb,
  account_snapshot jsonb not null default '{}'::jsonb,
  features jsonb not null default '{}'::jsonb,
  decision_payload jsonb not null default '{}'::jsonb,
  risk_payload jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.orders (
  id uuid primary key default gen_random_uuid(),
  decision_id uuid references public.decisions(id) on delete set null,
  broker text not null default 'alpaca',
  external_order_id text not null unique,
  client_order_id text unique,
  symbol text not null,
  side text not null check (side in ('buy', 'sell')),
  order_type text not null,
  time_in_force text not null,
  status text not null,
  requested_notional numeric(20, 8),
  requested_qty numeric(20, 8),
  filled_qty numeric(20, 8),
  filled_avg_price numeric(20, 8),
  submitted_at timestamptz,
  last_updated_at timestamptz,
  raw_order jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.fills (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.orders(id) on delete cascade,
  fill_event text not null,
  event_timestamp timestamptz not null default timezone('utc', now()),
  event_price numeric(20, 8),
  event_qty numeric(20, 8),
  filled_qty numeric(20, 8),
  filled_avg_price numeric(20, 8),
  raw_update jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.trade_outcomes (
  id uuid primary key default gen_random_uuid(),
  review_id text not null unique,
  decision_id uuid references public.decisions(id) on delete set null,
  order_id uuid references public.orders(id) on delete set null,
  symbol text not null,
  action text not null,
  outcome text not null,
  summary text not null,
  decision_confidence numeric(5, 4) not null check (decision_confidence >= 0 and decision_confidence <= 1),
  spread_bps numeric(12, 4),
  failure_mode text,
  cash_delta numeric(20, 8) not null default 0,
  position_qty_delta numeric(20, 8) not null default 0,
  filled_qty numeric(20, 8),
  filled_avg_price numeric(20, 8),
  lesson_candidates jsonb not null default '[]'::jsonb,
  raw_review jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.lessons (
  id uuid primary key default gen_random_uuid(),
  lesson_id text,
  category text not null,
  message text not null,
  confidence numeric(5, 4) not null check (confidence >= 0 and confidence <= 1),
  source text not null,
  status text not null default 'active' check (status in ('active', 'archived', 'candidate')),
  policy_version_id uuid references public.policy_versions(id) on delete set null,
  occurrence_count integer not null default 1 check (occurrence_count >= 1),
  metadata jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default timezone('utc', now()),
  last_seen_at timestamptz not null default timezone('utc', now()),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (category, message, source)
);

create index if not exists decisions_symbol_recorded_at_idx
  on public.decisions (symbol, recorded_at desc);

create index if not exists decisions_policy_version_idx
  on public.decisions (policy_version_id);

create index if not exists decisions_action_risk_idx
  on public.decisions (action, risk_approved, recorded_at desc);

create index if not exists orders_decision_id_idx
  on public.orders (decision_id);

create index if not exists orders_symbol_status_idx
  on public.orders (symbol, status, submitted_at desc);

create index if not exists fills_order_id_event_timestamp_idx
  on public.fills (order_id, event_timestamp desc);

create index if not exists trade_outcomes_order_id_idx
  on public.trade_outcomes (order_id);

create index if not exists trade_outcomes_outcome_recorded_at_idx
  on public.trade_outcomes (outcome, recorded_at desc);

create index if not exists lessons_status_category_idx
  on public.lessons (status, category, last_seen_at desc);

create index if not exists lessons_policy_version_idx
  on public.lessons (policy_version_id);

create trigger policy_versions_set_updated_at
before update on public.policy_versions
for each row
execute function public.set_updated_at();

create trigger active_policy_set_updated_at
before update on public.active_policy
for each row
execute function public.set_updated_at();

create trigger orders_set_updated_at
before update on public.orders
for each row
execute function public.set_updated_at();

create trigger lessons_set_updated_at
before update on public.lessons
for each row
execute function public.set_updated_at();
