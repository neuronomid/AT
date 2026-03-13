# AGENTS.md

Date: 2026-03-13

## Project Summary

This repository is for a research-phase autonomous trading system that now spans multiple bounded runtime tracks instead of only the original Alpaca loop.

Current repo families:

- Alpaca `ETH/USD` paper-trading research and review flows
- historical backtesting, replay, refinement, and discovery research
- v4 Alpaca live-paper runtime with LLM-assisted decisions
- v5 MT5 bridge demo-paper runtime for `EURUSD`
- v5.1 MT5 bridge demo-paper runtime for `BTCUSD`
- Supabase-backed control plane plus dashboard API/frontend for operators

The shared goal across these tracks is to design and validate agent workflows that:

- consume live or historical market data
- analyze the market continuously or on bounded intervals
- recommend or execute actions only through explicit execution paths
- respect account balance and deterministic risk limits
- avoid overtrading
- log all decisions, commands, orders, and outcomes
- improve through offline review, backtesting, and versioned promotion

This project is still research-first. Paper trading, demo trading, shadow mode, and evaluation come before any live path.

## Current Scope

Primary runtime matrix:

- Alpaca baseline runtime
  - broker: Alpaca
  - symbol: `ETH/USD`
  - mode: paper trading only
  - account assumptions: Alpaca free tier
  - request budget: stay under `200` trading API requests per minute
- v4 live runtime
  - broker: Alpaca
  - symbol: `ETH/USD`
  - mode: paper trading only
  - analyst style: LLM-assisted, risk-gated
- v5 MT5 runtime
  - broker path: MT5 bridge
  - symbol: `EURUSD`
  - mode: demo/shadow by default
  - provider: OpenAI-compatible runtime
- v5.1 MT5 runtime
  - broker path: MT5 bridge
  - symbol: `BTCUSD`
  - mode: demo/shadow by default
  - provider: OpenRouter

Operational assumptions:

- near-term runtime is still local testing and refinement during research
- final always-on runtime belongs on a VPS/VM, not a personal laptop or desktop
- the dashboard is local-first and should sit behind a reverse proxy before remote exposure

## Default Assumptions For Work

Unless the user explicitly says otherwise:

- assume the project is still in research phase
- do not introduce live trading behavior by default
- keep Alpaca paper trading as the default broker integration
- keep `ETH/USD` as the default symbol for Alpaca work
- treat MT5 runtimes as separate research tracks, not as a replacement for Alpaca
- keep MT5 trade commands disabled and shadow mode enabled unless the user explicitly requests command execution

## Core Principles

1. The analyst model is advisory, not sovereign.
It can recommend actions, but it cannot bypass deterministic risk and execution rules.

2. Use streaming/event-driven paths where possible.
Prefer WebSockets and bridge pushes over high-frequency polling when streaming is available.

3. Keep risk deterministic and explicit.
Hard rules must govern position sizing, cooldowns, kill switches, exposure, stale-data handling, and execution vetoes.

4. Prefer simple, auditable architecture over agent sprawl.
Use a few bounded services with typed interfaces rather than many loosely defined agents.

5. Learn from logs, backtests, and reviews, not from uncontrolled self-modification.
Improvements should be tested offline and versioned before promotion.

6. The control plane may configure and promote versions, but it must not bypass runtime safety.
Dashboard and database controls are for versioning, observability, and explicit promotion, not for skipping risk checks.

7. Keep research tracks clearly separated.
Do not mix Alpaca assumptions, MT5 bridge assumptions, and strategy-family assumptions unless the user explicitly wants cross-track work.

## Current System Modules

- `Market Data / Broker Services`
  - Alpaca crypto market data, historical bars, account sync, trading, and trade updates
  - MT5 bridge ingestion, bridge state, snapshots, commands, and acknowledgements
- `Analyst Services`
  - baseline analyst
  - HMM regime analyst
  - live LLM analyst
  - MT5 entry analyst
  - MT5 position manager
  - research reviewer / strategy advisor
- `Risk / Execution Services`
  - Alpaca risk policy and sizing
  - v4 live policy
  - MT5 v5 and v5.1 risk arbiters
  - order manager, executor, position tracker, ticket registries, and entry planners
- `Evaluation / Research Services`
  - backtest runner
  - replay
  - refinement
  - challenger
  - HMM refinement
  - discovery research
- `Memory / Control Plane`
  - journals and lesson stores
  - Supabase/Postgres stores
  - policy versions, agent configs, heartbeats, promotions, and backtest jobs
- `Operator Interfaces`
  - React/Vite frontend in `frontend/`
  - FastAPI dashboard API in `src/dashboard_api/app.py`
  - Streamlit dashboard in `src/dashboard/app.py` as a legacy prototype

## Memory Model

Use multiple memory layers:

- hot state memory for current market, account, bridge, and order/ticket state
- episodic memory for decisions, orders, runtime decisions, trade outcomes, and bridge events
- lesson memory for recurring patterns extracted from reflections and reviews
- policy memory for prompts, thresholds, strategies, and promotion history

Structured storage is preferred over generic chat memory.
Supabase/Postgres is the primary persistent store when database persistence is needed.

## Risk Expectations

The project should include:

- max risk per trade
- max notional exposure
- max trades per hour
- max daily loss
- cooldown after losses or exits
- stale-data detection
- state mismatch detection
- duplicate-entry and pending-order protection
- manual and automatic kill switch support
- for MT5 paths: bridge snapshot freshness, command acknowledgement handling, and ticket-state reconciliation

## Engineering Expectations

- log every decision, order event, bridge command, acknowledgement, and trade reflection
- keep all prompts, thresholds, policies, and runtime settings versioned
- prefer deterministic code for execution and safety logic
- preserve separation between analysis, risk, execution, review, and control-plane code
- treat the database as structured memory for logs, lessons, policy history, runtime history, and promotions
- use Supabase as the primary database when persistent database storage is needed
- when reading, writing, or changing database state or schema, use the Supabase CLI
- prefer shared services that can be reused by CLI flows and the dashboard
- use the React + FastAPI dashboard path for new dashboard work; treat Streamlit as legacy unless the user explicitly wants it
- do not silently enable MT5 trade commands

## Current Entry Points

Main CLI entry points currently include:

- `at-agent`
- `at-agent-backtest`
- `at-agent-evaluate`
- `at-agent-review`
- `at-agent-strategy-review`
- `at-agent-strategy-cycle`
- `at-agent-discovery-cycle`
- `at-agent-v3-research`
- `at-agent-v3-2-research`
- `at-agent-v4-live`
- `at-agent-v5-mt5`
- `at-agent-v5-1-mt5`
- `at-agent-dashboard`
- `at-agent-dashboard-api`

Local helper scripts exist in `scripts/`, including the dashboard dev launcher and MT5 runtime launchers.

## Immediate Build Direction

Priority order for the current repository state:

1. stabilize and document the Supabase-backed control plane and seeded policy versions
2. keep Alpaca `ETH/USD` research, replay, and backtest flows reproducible
3. harden the v4 Alpaca paper runtime around stale-data handling, reconciliation, and review artifacts
4. harden MT5 v5 and v5.1 bridge safety around snapshot freshness, command acks, ticket tracking, and shadow-mode defaults
5. continue HMM/discovery/research loops only through versioned offline evaluation
6. complete the operator workflow in the dashboard for configs, policy history, backtests, promotions, and runtime history
7. move toward VPS/VM deployment only after local research loops are stable and auditable

## Working Note For Future Sessions

When continuing work in this repository:

- assume research phase unless the user explicitly changes scope
- optimize first for correctness, auditability, and risk control
- default to Alpaca paper trading and `ETH/USD` unless the task clearly targets a different runtime
- if the task is MT5-specific, determine whether it targets:
  - `v5` on `EURUSD` with the MT5 bridge in `src/brokers/mt5/`
  - `v5.1` on `BTCUSD` with the MT5 bridge in `src/brokers/mt5_v51/`
- for model-provider work:
  - Alpaca baseline, v4, and v5 use the OpenAI-compatible settings in `src/app/config.py`
  - v5.1 uses the OpenRouter settings in `src/app/v5_1_config.py`
- keep `mt5_enable_trade_commands=false` and `v51_mt5_enable_trade_commands=false` unless the user explicitly wants trade commands enabled
- keep `v51_mt5_shadow_mode=true` unless the user explicitly wants command execution behavior changed
- use the React frontend in `frontend/` and the FastAPI API in `src/dashboard_api/app.py` as the primary dashboard path
- treat `src/dashboard/app.py` as a legacy Streamlit prototype
- use the Supabase CLI for database operations instead of ad hoc manual database changes
- for this user's macOS + Wine MT5 setup, prefer copying EA/files into MT5 via Terminal rather than Finder paste
- MT5 files should be copied into Wine at `/Users/omid/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/...`
- for both `V5BridgeEA.mq5` and `V51BridgeEA.mq5`, the confirmed target directory is `/Users/omid/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/Advisors/`
