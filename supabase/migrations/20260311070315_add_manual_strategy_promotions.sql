do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'agent_configs_single_symbol_check'
  ) then
    alter table public.agent_configs
      add constraint agent_configs_single_symbol_check
      check (jsonb_array_length(symbols) = 1);
  end if;
end;
$$;

create table if not exists public.agent_strategy_promotions (
  id uuid primary key default gen_random_uuid(),
  agent_config_id uuid not null references public.agent_configs(id) on delete cascade,
  previous_policy_version_id uuid references public.policy_versions(id) on delete set null,
  new_policy_version_id uuid not null references public.policy_versions(id) on delete restrict,
  source_run_id uuid references public.backtest_runs(id) on delete set null,
  promoted_by text not null default 'dashboard-ui',
  rationale text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists agent_strategy_promotions_agent_created_idx
  on public.agent_strategy_promotions (agent_config_id, created_at desc);

create index if not exists agent_strategy_promotions_run_created_idx
  on public.agent_strategy_promotions (source_run_id, created_at desc);
