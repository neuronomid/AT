alter table public.decisions
  drop constraint if exists decisions_action_check;

alter table public.decisions
  add constraint decisions_action_check
  check (action in ('buy', 'sell', 'hold', 'exit', 'do_nothing', 'reduce'));
