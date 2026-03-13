create table if not exists public.mt5_bridge_snapshots (
  id uuid primary key default gen_random_uuid(),
  bridge_id text not null,
  agent_name text not null,
  symbol text not null,
  server_time timestamptz not null,
  received_at timestamptz,
  spread_bps numeric(12, 4),
  snapshot_payload jsonb not null default '{}'::jsonb,
  health_payload jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.mt5_runtime_decisions (
  id uuid primary key default gen_random_uuid(),
  agent_name text not null,
  decision_kind text not null check (decision_kind in ('entry', 'management')),
  symbol text not null,
  action text not null,
  confidence numeric(5, 4) not null check (confidence >= 0 and confidence <= 1),
  rationale text not null,
  risk_posture text not null check (risk_posture in ('reduced', 'neutral', 'mildly_aggressive')),
  risk_approved boolean,
  risk_reason text,
  context_payload jsonb not null default '{}'::jsonb,
  decision_payload jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.mt5_bridge_commands (
  id uuid primary key default gen_random_uuid(),
  command_id text not null unique,
  bridge_id text not null,
  agent_name text not null,
  symbol text not null,
  command_type text not null check (command_type in ('place_entry', 'modify_ticket', 'close_ticket')),
  status text not null default 'queued',
  ticket_id text,
  basket_id text,
  created_at timestamptz not null,
  expires_at timestamptz,
  reason text not null,
  command_payload jsonb not null default '{}'::jsonb,
  ack_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.mt5_bridge_acks (
  id uuid primary key default gen_random_uuid(),
  command_id text not null references public.mt5_bridge_commands(command_id) on delete cascade,
  agent_name text not null,
  ack_status text not null,
  ticket_id text,
  broker_time timestamptz,
  message text,
  ack_payload jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.mt5_trade_reflections (
  id uuid primary key default gen_random_uuid(),
  reflection_id text not null unique,
  agent_name text not null,
  symbol text not null,
  side text not null check (side in ('long', 'short')),
  ticket_id text,
  basket_id text,
  risk_posture text check (risk_posture in ('reduced', 'neutral', 'mildly_aggressive')),
  opened_at timestamptz not null,
  closed_at timestamptz not null,
  realized_pnl_usd numeric(20, 8) not null default 0,
  realized_r numeric(12, 4) not null default 0,
  exit_reason text not null,
  reflection_payload jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default timezone('utc', now())
);

create index if not exists mt5_bridge_snapshots_symbol_recorded_at_idx
  on public.mt5_bridge_snapshots (symbol, recorded_at desc);

create index if not exists mt5_runtime_decisions_symbol_recorded_at_idx
  on public.mt5_runtime_decisions (symbol, recorded_at desc);

create index if not exists mt5_bridge_commands_symbol_status_idx
  on public.mt5_bridge_commands (symbol, status, created_at desc);

create index if not exists mt5_bridge_acks_command_id_idx
  on public.mt5_bridge_acks (command_id, recorded_at desc);

create index if not exists mt5_trade_reflections_symbol_closed_at_idx
  on public.mt5_trade_reflections (symbol, closed_at desc);

create trigger mt5_bridge_commands_set_updated_at
before update on public.mt5_bridge_commands
for each row
execute function public.set_updated_at();
