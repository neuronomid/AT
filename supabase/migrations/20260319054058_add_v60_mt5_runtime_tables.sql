create table if not exists public.mt5_v60_bridge_snapshots (
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

create table if not exists public.mt5_v60_close_events (
  id uuid primary key default gen_random_uuid(),
  event_id text not null unique,
  agent_name text not null,
  symbol text not null,
  ticket_id text,
  basket_id text,
  closed_at timestamptz not null,
  close_reason text not null,
  event_payload jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.mt5_v60_runtime_decisions (
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

create table if not exists public.mt5_v60_bridge_commands (
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

create table if not exists public.mt5_v60_bridge_acks (
  id uuid primary key default gen_random_uuid(),
  command_id text not null references public.mt5_v60_bridge_commands(command_id) on delete cascade,
  agent_name text not null,
  ack_status text not null,
  ticket_id text,
  broker_time timestamptz,
  message text,
  ack_payload jsonb not null default '{}'::jsonb,
  recorded_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.mt5_v60_trade_reflections (
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

create table if not exists public.mt5_v60_ticket_state (
  id uuid primary key default gen_random_uuid(),
  ticket_id text not null unique,
  symbol text not null,
  side text not null check (side in ('long', 'short')),
  basket_id text,
  magic_number bigint,
  entry_command_id text,
  is_open boolean not null default true,
  opened_at timestamptz not null,
  last_seen_at timestamptz not null,
  current_price numeric(20, 8) not null,
  current_volume_lots numeric(20, 8) not null,
  unrealized_pnl_usd numeric(20, 8) not null default 0,
  unrealized_r numeric(12, 4) not null default 0,
  ticket_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists mt5_v60_bridge_snapshots_symbol_recorded_at_idx
  on public.mt5_v60_bridge_snapshots (symbol, recorded_at desc);

create index if not exists mt5_v60_close_events_symbol_closed_at_idx
  on public.mt5_v60_close_events (symbol, closed_at desc);

create index if not exists mt5_v60_runtime_decisions_symbol_recorded_at_idx
  on public.mt5_v60_runtime_decisions (symbol, recorded_at desc);

create index if not exists mt5_v60_bridge_commands_symbol_status_idx
  on public.mt5_v60_bridge_commands (symbol, status, created_at desc);

create index if not exists mt5_v60_bridge_acks_command_id_idx
  on public.mt5_v60_bridge_acks (command_id, recorded_at desc);

create index if not exists mt5_v60_trade_reflections_symbol_closed_at_idx
  on public.mt5_v60_trade_reflections (symbol, closed_at desc);

create index if not exists mt5_v60_ticket_state_symbol_open_idx
  on public.mt5_v60_ticket_state (symbol, is_open, last_seen_at desc);

create or replace function public.prune_mt5_v60_bridge_snapshots(retention interval default interval '2 hours')
returns integer
language plpgsql
as $$
declare
  deleted_count integer;
begin
  delete from public.mt5_v60_bridge_snapshots
  where recorded_at < timezone('utc', now()) - retention;

  get diagnostics deleted_count = row_count;
  return deleted_count;
end;
$$;

drop trigger if exists mt5_v60_bridge_commands_set_updated_at on public.mt5_v60_bridge_commands;
create trigger mt5_v60_bridge_commands_set_updated_at
before update on public.mt5_v60_bridge_commands
for each row
execute function public.set_updated_at();

drop trigger if exists mt5_v60_ticket_state_set_updated_at on public.mt5_v60_ticket_state;
create trigger mt5_v60_ticket_state_set_updated_at
before update on public.mt5_v60_ticket_state
for each row
execute function public.set_updated_at();
