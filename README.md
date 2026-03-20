# AT

AT is a research-phase autonomous trading system. The repository combines:

- an Alpaca `ETH/USD` paper-trading baseline
- offline backtesting, replay, review, refinement, and discovery workflows
- a v4 Alpaca paper runtime with LLM-assisted decisions
- several MT5 bridge runtimes for demo/shadow trading research
- a Supabase-backed control plane with a FastAPI API and React operator dashboard

The code is built around one idea: models can analyze and recommend, but deterministic risk and execution code decides what is allowed to happen.

## What This Project Is

- Research-first, not production live trading.
- Paper trading, demo trading, shadow mode, and offline evaluation come before any real-money path.
- Multiple runtime tracks live in the same repo, but they are intentionally separated by broker, symbol, and execution model.
- Auditability matters more than cleverness. Decisions, orders, bridge commands, acknowledgements, and reflections are all meant to be logged.

## Runtime Tracks

| Track | Entry point | Purpose |
| --- | --- | --- |
| Alpaca baseline | `at-agent` | Baseline `ETH/USD` paper-trading loop using deterministic analyst/risk/execution services. |
| Historical backtesting | `at-agent-backtest` | Walk-forward backtests against Alpaca historical data and stored policy versions. |
| Journal review | `at-agent-review` | Summarizes journaled runtime records into lessons and a review report. |
| Candidate evaluation | `at-agent-evaluate` | Replays journal records and compares baseline vs challenger logic. |
| Strategy advisor | `at-agent-strategy-review` | Uses an LLM to review backtest and journal outputs and propose improvements. |
| Strategy refinement cycle | `at-agent-strategy-cycle` | Iterative offline policy refinement loop for Alpaca research. |
| Discovery cycle | `at-agent-discovery-cycle` | Discovery-first research loop that mines patterns from history before backtesting them. |
| v4 Alpaca live-paper runtime | `at-agent-v4-live` | LLM-assisted paper runtime with candle/context packets, deterministic risk, and review artifacts. |
| v5 MT5 runtime | `at-agent-v5-mt5` | Original MT5 bridge orchestrator for `EURUSD`. |
| v5.1 MT5 runtime | `at-agent-v5-1-mt5` | MT5 `BTCUSD` runtime using OpenRouter-backed entry/manager agents. |
| v6.0 MT5 runtime | `at-agent-v6-0-mt5` | Newer MT5 runtime with richer context, ticket registry, and screenshot-aware management. |
| v6.1 MT5 runtime | `at-agent-v6-1-mt5` | Multi-symbol extension of the v6.0 MT5 runtime. |
| Dashboard API | `at-agent-dashboard-api` | FastAPI control-plane and dashboard backend. |
| Legacy dashboard | `at-agent-dashboard` | Streamlit prototype kept for compatibility. |

## Architecture In One Pass

Most runtime tracks follow the same shape:

1. Broker or bridge adapters ingest live or historical market/account state.
2. Typed schemas normalize that state into runtime packets.
3. Analyst agents produce structured decisions.
4. Deterministic risk policy approves, sizes, or vetoes those decisions.
5. Execution code turns approved decisions into orders or MT5 bridge commands.
6. Journals, reflections, lessons, and database records capture what happened.
7. Offline review, backtesting, and the dashboard use those records to compare and promote strategy versions.

The important design boundary is that analysis is advisory. Execution safety lives in code under `src/risk/` and `src/execution/`.

## How The Codebase Is Organized

```text
src/
  app/            CLI entry points and runtime orchestration
  agents/         Analyst, reviewer, strategy, and MT5 manager agents
  brokers/        Alpaca services and MT5 bridge apps/state
  control_plane/  Agent configs, policy versions, promotions, backtest jobs
  data/           Pydantic schemas and feature inputs
  evaluation/     Backtest, replay, challenger, refinement, and reporting logic
  execution/      Order managers, ticket registries, planners, and executors
  feedback/       Trade reflection and lesson extraction
  infra/          Logging, metrics, OpenAI/OpenRouter wrappers, scheduler
  memory/         Journal files plus Supabase/Postgres stores
  research/       Discovery-first research workflows and report rendering
  risk/           Deterministic risk policies for Alpaca and MT5 tracks
  runtime/        Context packet builders, candle builders, quote tapes
  dashboard_api/  FastAPI backend for the operator dashboard
  dashboard/      Legacy Streamlit dashboard

frontend/         React + Vite operator UI
supabase/         Supabase config and SQL migrations
scripts/          Helper launch scripts
strategies/       Human-readable strategy notes by version
docs/             Design and planning documents
var/              Runtime artifacts, journals, reports, and experiment output
tests/            Unit tests
```

## If You Are New, Read The Code In This Order

1. `src/app/`
   Start with the entry point for the runtime you care about. This is where dependencies are wired together.
2. `src/app/config.py`, `src/app/v5_1_config.py`, `src/app/v6_0_config.py`, `src/app/v6_1_config.py`
   These show the actual environment surface and defaults for each track.
3. `src/data/`
   The schemas define the contract between brokers, agents, risk, execution, and storage.
4. `src/brokers/` and `src/runtime/`
   These files explain how live inputs become normalized context.
5. `src/agents/`
   Advisory logic lives here.
6. `src/risk/`
   Hard rules live here. This is the first place to look if you want to understand safety boundaries.
7. `src/execution/`
   Order placement, MT5 bridge commands, ticket registries, and position handling live here.
8. `src/memory/`, `src/control_plane/`, `src/evaluation/`, `src/feedback/`
   These power persistence, backtests, promotions, reflections, and the dashboard.
9. `src/dashboard_api/app.py` and `frontend/src/App.tsx`
   Read these if you want the operator/control-plane path.

## Track-Specific Code Map

### Alpaca baseline and research

- `src/app/main.py`
- `src/brokers/alpaca/`
- `src/agents/analyst.py`
- `src/risk/policy.py`
- `src/execution/executor.py`
- `src/evaluation/`

This is the original `ETH/USD` paper-trading and offline-research path. It also feeds the backtest, replay, evaluation, review, and strategy-cycle tooling.

### v4 Alpaca live-paper runtime

- `src/app/v4_live.py`
- `src/agents/llm_live_analyst.py`
- `src/risk/v4_policy.py`
- `src/runtime/candle_builder.py`
- `src/runtime/context_packet.py`
- `src/execution/position_tracker.py`

This is the LLM-assisted Alpaca runtime. It still relies on deterministic risk and produces session artifacts under `var/v4/`.

### v5 MT5 runtime

- `src/app/v5_mt5.py`
- `src/brokers/mt5/`
- `src/agents/mt5_entry_analyst.py`
- `src/agents/mt5_position_manager.py`
- `src/risk/mt5_v5_policy.py`
- `src/execution/mt5_entry_planner.py`
- `src/execution/mt5_ticket_book.py`

This is the older MT5 `EURUSD` bridge track.

### v5.1 MT5 runtime

- `src/app/v5_1_mt5.py`
- `src/app/v5_1_config.py`
- `src/brokers/mt5_v51/`
- `src/data/mt5_v51_schemas.py`
- `src/runtime/mt5_v51_context_packet.py`
- `src/runtime/mt5_v51_microbars.py`
- `src/execution/mt5_v51_entry_planner.py`
- `src/execution/mt5_v51_ticket_registry.py`
- `src/risk/mt5_v51_policy.py`

This is the BTCUSD MT5 path. It uses OpenRouter settings, finer-grained microbar logic, and a dedicated Supabase store.

### v6.0 and v6.1 MT5 runtimes

- `src/app/v6_0_mt5.py`
- `src/app/v6_1_mt5.py`
- `src/app/v6_0_config.py`
- `src/app/v6_1_config.py`
- `src/brokers/mt5_v60/`
- `src/data/mt5_v60_schemas.py`
- `src/runtime/mt5_v60_context_packet.py`
- `src/execution/mt5_v60_*`
- `src/risk/mt5_v60_policy.py`

These are the newest MT5 tracks. v6.0 is a single-symbol runtime with richer context and screenshot-aware management. v6.1 reuses much of that machinery for dynamic multi-symbol handling.

## Getting Started

### 1. Install dependencies

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
npm --prefix frontend install
```

### 2. Create your local environment file

```bash
cp .env.example .env
```

Then fill in only the credentials and settings for the track you want to run.

### 3. Optional: set up Supabase for the control plane

This repo expects database work to go through the Supabase CLI, not ad hoc SQL changes.

Useful files:

- `supabase/config.toml`
- `supabase/migrations/`
- `src/memory/supabase.py`
- `src/memory/supabase_mt5_v51.py`
- `src/memory/supabase_mt5_v60.py`

Typical local workflow:

```bash
supabase start
supabase db reset
```

Typical linked-project workflow:

```bash
supabase link
supabase db push
```

## Environment Variables

`.env.example` covers the baseline Alpaca flow, common OpenAI settings, v5.1 OpenRouter settings, and Supabase access.

Important groups:

- Alpaca baseline and v4:
  `ALPACA_*`, `TRADING_SYMBOL`, `ENABLE_AGENT_ORDERS`, `OPENAI_*`
- Offline evaluation and reports:
  `JOURNAL_PATH`, `LESSONS_PATH`, `BACKTEST_*`, `REVIEW_SUMMARY_PATH`, `EVALUATION_REPORT_PATH`
- v5.1 MT5:
  `V51_*`
- Supabase:
  `SUPABASE_*`

Important note about newer MT5 tracks:

- v6.0 and v6.1 read their own settings modules in `src/app/v6_0_config.py` and `src/app/v6_1_config.py`.
- Those settings use `V60_*` and `V61_*` variables that are not currently listed in `.env.example`.
- Read those config files before running the v6 tracks.

Important safety note:

- `.env.example` keeps `ENABLE_AGENT_ORDERS=false`.
- `.env.example` also keeps `V51_MT5_ENABLE_TRADE_COMMANDS=false` and `V51_MT5_SHADOW_MODE=true`.
- The newer v6 config modules currently default differently, so do not run them blindly without checking their config.

## Common Commands

### Baseline Alpaca loop

```bash
.venv/bin/at-agent
```

### Backtest, review, and evaluation

```bash
.venv/bin/at-agent-backtest --run-name ethusd-walk-forward
.venv/bin/at-agent-review
.venv/bin/at-agent-evaluate
.venv/bin/at-agent-strategy-review
```

### Strategy and discovery research

```bash
.venv/bin/at-agent-strategy-cycle
.venv/bin/at-agent-discovery-cycle
```

### v4 Alpaca paper runtime

```bash
.venv/bin/at-agent-v4-live --duration-minutes 60
```

### MT5 runtimes

```bash
.venv/bin/at-agent-v5-mt5 --duration-minutes 60
scripts/run_v5_1_mt5.sh --duration-minutes 60 --shadow-mode
scripts/run_v6_0_mt5.sh --duration-minutes 60 --shadow-mode
scripts/run_v6_1_mt5.sh --duration-minutes 60 --shadow-mode
```

Notes:

- `scripts/run_v5_mt5.sh` always adds `--enable-trade-commands`, so use the CLI entry point directly if you want to keep that runtime in a safer non-command path.
- MT5 tracks depend on the local bridge/EA side being installed and pointed at the matching host and port.

## Dashboard

Primary dashboard path:

- backend: `src/dashboard_api/app.py`
- frontend: `frontend/`
- database: Supabase/Postgres

Recommended local startup:

```bash
.venv/bin/at-agent-dashboard-api
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Legacy Streamlit dashboard:

```bash
.venv/bin/at-agent-dashboard
```

The React app is the intended operator UI. Streamlit is still present, but it is no longer the main path for new dashboard work.

## What Gets Stored Where

- Local journals and reports:
  `var/`
- Session-specific runtime artifacts:
  `var/v4/`, `var/v5/`, `var/v5_1/`, `var/v6_0/`, `var/v6_1/`
- Strategy-cycle and discovery artifacts:
  `var/strategy_cycles/`, `var/research/`
- Database-backed structured memory:
  agent configs, policy versions, heartbeats, decisions, orders, trade outcomes, lessons, bridge commands, acknowledgements, and backtest jobs/runs

## Testing

Run the unit test suite with:

```bash
pytest
```

Most tests live under `tests/unit/` and are organized by subsystem: Alpaca services, analysts, risk policies, context builders, MT5 runtimes, backtests, and review logic.

## Extra Documents

- `AGENTS.md`
  Repository operating assumptions and current project direction.
- `docs/alpaca-eth-agent-plan.md`
  Original baseline architecture plan.
- `docs/dashboard.md`
  Dashboard/control-plane design notes.
- `docs/hmm-regime-research.md`
  HMM research notes.
- `strategies/*.md`
  Human-readable strategy version notes.

## Practical Caveats

- This repo contains generated and runtime files such as `frontend/node_modules/`, `frontend/dist/`, `var/`, and `__pycache__/`. Those are not the source of truth.
- The project has evolved from a single Alpaca loop into several bounded runtime tracks. Do not assume the MT5 paths are drop-in replacements for the Alpaca path.
- If you are changing execution behavior, read the matching config module and risk policy before you touch the agent prompt.

