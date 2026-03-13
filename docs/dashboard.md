# Dashboard Plan

## What The Dashboard Does

The dashboard is the operator console for this research-phase paper-trading system.
It brings together:

- live agent health and heartbeat status
- editable agent runtime controls
- strategy version management
- historical decision and trade review history
- walk-forward backtest execution and comparison
- lesson and failure-mode review

Trade history is intentionally treated as immutable audit data.
The dashboard is designed to let you review, filter, and compare history, not rewrite what happened.

## Why This Shape

The design follows four practical rules:

1. The dashboard should reflect deterministic control points, not bypass them.
2. Strategies, risk settings, and agent runtime settings should be versioned and queryable.
3. Monitoring should separate current health from historical performance.
4. Backtest comparison should surface robustness metrics, not only raw return.

## Current Architecture

The current primary dashboard path is:

- React/Vite frontend for the operator UI
- FastAPI for the local dashboard API
- Supabase/Postgres as the source of truth

The earlier Streamlit UI remains in the repo as a prototype, but the intended path is the web app.

- `frontend/`
  Modern web frontend for operators.
- `src/dashboard_api/app.py`
  FastAPI endpoints for dashboard data and actions.
- `src/dashboard/app.py`
  Legacy Streamlit prototype.
- `src/control_plane/models.py`
  Typed records for agent configs, policy versions, heartbeats, and backtest requests.
- `src/control_plane/policies.py`
  Policy seeding plus conversion from stored configs into analyst and risk objects.
- `src/memory/supabase.py`
  Shared Supabase/Postgres store for control-plane state, runtime history, and backtests.
- `src/evaluation/backtest_runner.py`
  Reusable backtest workflow used by the CLI and dashboard.

## Database Additions

The dashboard migration adds:

- `agent_configs`
- `agent_heartbeats`
- `backtest_jobs`
- `agent_dashboard_status` view

It also attaches `agent_name` and `agent_config_id` to:

- `decisions`
- `orders`
- `trade_outcomes`
- `backtest_runs`

## How To Run

Local:

```bash
.venv/bin/pip install -e '.[dev]'
npm --prefix frontend install
scripts/run_dashboard_dev.sh
```

The API listens on the configured host and port from [src/app/config.py](/Users/omid/Documents/Projects/AT/src/app/config.py), and the frontend runs on Vite's local dev server.

## Nginx Reverse Proxy

An example reverse-proxy config is in [ops/nginx/at-dashboard.conf](/Users/omid/Documents/Projects/AT/ops/nginx/at-dashboard.conf).

Typical pattern:

1. Run the dashboard locally on `127.0.0.1:8501`.
2. Put nginx in front of it.
3. Add TLS and access control at the nginx layer before exposing it on a VPS.

## Recommended Operator Workflow

1. Configure or pause agents in the `Agents` tab.
2. Save new policy versions in the `Strategies` tab.
3. Run walk-forward comparisons in the `Backtests` tab.
4. Promote only strategies that improve score without unacceptable drawdown.
5. Use `Overview` and `History` to inspect runtime behavior and lessons.

## Current Constraints

- The live loop currently trades the first symbol in each agent's symbol list.
- The dashboard is local-first and suitable for a VM or VPS behind nginx.
- Authentication is not built into the Streamlit layer yet, so reverse-proxy protection matters before remote exposure.
