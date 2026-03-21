# AT

AT is an MT5-bridge trading research system.

The center of this repository is no longer the old Alpaca loop. The main runtime family is now:

- V5 MT5 on `EURUSD`
- V5.1 MT5 on `BTCUSD`
- V6.0 MT5 on `EURUSD@`
- V6.1 MT5 multi-symbol on the V6 bridge stack

The shared design is consistent across versions:

- MT5 and Python are separated by a local bridge
- models analyze and recommend
- deterministic risk and execution code decides what is allowed
- every snapshot, command, acknowledgement, and trade outcome should be auditable

## What This Project Is

- A research-phase orchestration system for MT5 demo, paper, and shadow trading
- A set of bounded runtime versions, not one monolithic agent
- A local operator stack with Supabase, FastAPI, and React for control-plane and review work
- A codebase where execution safety lives in Python code, not in prompts

## The Core Mental Model

This repo is easiest to understand if you think in four layers:

1. MT5 terminal + EA
   The Expert Advisor running inside MetaTrader 5 sees broker prices, open tickets, chart data, and screenshots.
2. Local bridge
   A versioned FastAPI bridge receives snapshots from MT5, queues commands for MT5, and records acknowledgements.
3. Python runtime
   The runtime builds context, asks entry and manager agents for structured decisions, runs deterministic risk checks, and turns approved actions into bridge commands.
4. Memory and control plane
   Journals, Supabase stores, reflections, lessons, policies, backtests, and the dashboard sit here.

## What “MT5 Bridge” Means In This Repo

The Python runtime does not directly place MT5 orders through a native terminal API.
Instead, each runtime uses a local HTTP bridge with the same high-level loop:

1. An MT5 EA in `ops/mt5/` runs on a chart.
2. On each timer tick, the EA posts a snapshot to `/bridge/snapshot`.
3. The Python runtime consumes that snapshot through a versioned bridge state object.
4. If the runtime wants to act, it queues a command in bridge state.
5. The EA polls `/bridge/commands`.
6. MT5 executes the command inside the terminal.
7. The EA posts an acknowledgement to `/bridge/acks`.
8. The runtime reconciles ticket state, logs the event, and updates reflections/lessons.

Shared bridge endpoints:

- `POST /bridge/snapshot`
- `GET /bridge/commands`
- `POST /bridge/acks`
- `GET /bridge/health`

Bridge implementations:

- `src/brokers/mt5/`
- `src/brokers/mt5_v51/`
- `src/brokers/mt5_v60/`

Bridge EAs:

- `ops/mt5/V5BridgeEA.mq5`
- `ops/mt5/V51BridgeEA.mq5`
- `ops/mt5/V60BridgeEA.mq5`
- `ops/mt5/V61BridgeEA.mq5`

## Version Matrix

| Version | Symbol / scope | Main execution view | Model path | Entry logic | Open-position management |
| --- | --- | --- | --- | --- | --- |
| V5 | `EURUSD` | 5m entry, 15m/4h context | OpenAI-compatible | LLM entry analyst | LLM manager with constrained actions |
| V5.1 | `BTCUSD` | 1m scalp with synthetic 20s microbars | OpenRouter | LLM entry analyst plus deterministic fast-entry override | Deterministic protection and auto-scalp management |
| V6.0 | `EURUSD@` | 3m execution, 1m/2m support, screenshot-aware | OpenAI Responses | Multimodal LLM analyzer plus deterministic fast breakout | Deterministic scalp guard plus multimodal manager |
| V6.1 | dynamic multi-symbol | Same as V6.0, per symbol | OpenAI Responses | Reuses V6.0 entry paths per symbol | Reuses V6.0 management paths per symbol |

## Shared MT5 Building Blocks

### Bridge state

Each version has a bridge state class that tracks:

- the latest snapshot
- pending commands
- inflight commands for newer versions
- recent acknowledgements
- bridge health

Files:

- `src/brokers/mt5/bridge_state.py`
- `src/brokers/mt5_v51/bridge_state.py`
- `src/brokers/mt5_v60/bridge_state.py`

The bridge evolves across versions:

- V5 keeps a simple pending-command queue.
- V5.1 adds explicit inflight command tracking.
- V6 adds per-symbol latest snapshots and symbol-filtered command polling.

### Context builders

Context builders turn raw snapshots into the packets that the agents actually read.

Files:

- `src/runtime/mt5_context_packet.py`
- `src/runtime/mt5_v51_context_packet.py`
- `src/runtime/mt5_v60_context_packet.py`

These are some of the most important files in the repo because they decide what the agents can see.

### Risk arbiters

The model never gets to trade on its own. Risk arbiters gate every entry.

Files:

- `src/risk/mt5_v5_policy.py`
- `src/risk/mt5_v51_policy.py`
- `src/risk/mt5_v60_policy.py`

They enforce things like:

- snapshot freshness
- spread gates
- confidence floors
- trade frequency limits
- daily loss kill switches
- pending-command protection
- duplicate-entry protection
- account-mode and symbol checks

### Execution planners and registries

Execution code translates approved decisions into MT5 bridge commands and tracks what happened afterward.

Files:

- `src/execution/mt5_entry_planner.py`
- `src/execution/mt5_v51_entry_planner.py`
- `src/execution/mt5_v60_entry_planner.py`
- `src/execution/mt5_v60_immediate_entry.py`
- `src/execution/mt5_ticket_book.py`
- `src/execution/mt5_v51_ticket_registry.py`
- `src/execution/mt5_v60_ticket_registry.py`

### Reflections and lessons

Closed tickets become structured reflections, then recurring lessons.

Files:

- `src/feedback/reflection.py`
- `src/feedback/mt5_v51_reflection.py`
- `src/feedback/mt5_v60_reflection.py`

## Agents

The runtime versions use different agent shapes.

### V5 agents

- Entry analyst: `src/agents/mt5_entry_analyst.py`
- Position manager: `src/agents/mt5_position_manager.py`

The entry analyst decides `enter_long`, `enter_short`, or `hold`.
The manager is restricted to actions like:

- `hold`
- `take_partial_50`
- `move_stop_to_breakeven`
- `trail_stop_to_rule`
- `close_ticket`

### V5.1 agents

- Entry analyst: `src/agents/mt5_v51_entry_analyst.py`
- Optional manager class exists: `src/agents/mt5_v51_position_manager.py`

The important implementation detail is that the current V5.1 runtime in `src/app/v5_1_mt5.py` does not rely on the LLM manager in its main loop. Open-trade handling is mostly deterministic:

- async entry analysis harvesting
- deterministic fast intrabar override
- automatic protection attachment
- registry-driven partials and stop moves

### V6.0 and V6.1 agents

- Entry analyzer: `src/agents/mt5_v60_entry_analyst.py`
- Manager: `src/agents/mt5_v60_position_manager.py`

These are multimodal agents on the V6 stack:

- the entry analyzer can use both numeric context and a chart screenshot
- the manager can also receive a fresh screenshot and return a `visual_context_update`

V6 differs from V5/V5.1 in one major way: the entry analyzer returns internal stop-loss and take-profit anchors, but the live entry itself can still be sent without broker-side TP/SL. The runtime then attaches and reviews protection after the fill.

## Workflow By Version

### V5 workflow

Main files:

- `src/app/v5_mt5.py`
- `ops/mt5/V5BridgeEA.mq5`
- `src/brokers/mt5/`

What happens:

1. The EA publishes a snapshot with bid/ask, 5m/15m/4h bars, account state, and open tickets.
2. The runtime syncs the in-memory `MT5TicketBook`.
3. On a new 5m bar, the entry analyst receives an entry packet from `MT5ContextBuilder`.
4. `MT5V5RiskArbiter` checks freshness, spread, pending commands, daily loss, same-direction limits, and basket risk.
5. `MT5EntryPlanner` converts approved entries into `place_entry` bridge commands.
6. If shadow mode is off, the bridge queues the command and the EA later executes it.
7. When there are open tickets, the position manager receives a constrained manager packet and can request partials, stop moves, or exits.
8. Ticket closures create reflections and lessons.

How to think about V5:

- the original MT5 version
- one entry agent and one manager agent
- simpler ticket tracking than later versions
- 5m bar rhythm, not the faster intrabar style of V5.1

### V5.1 workflow

Main files:

- `src/app/v5_1_mt5.py`
- `ops/mt5/V51BridgeEA.mq5`
- `src/brokers/mt5_v51/`
- `src/runtime/mt5_v51_microbars.py`
- `src/execution/mt5_v51_ticket_registry.py`

What changes relative to V5:

- symbol changes to `BTCUSD`
- execution becomes a faster 1m scalp system
- the runtime synthesizes 20-second microbars from live snapshots
- entry analysis and execution are more decoupled

What happens:

1. The EA publishes a snapshot for `BTCUSD`.
2. The runtime enriches the snapshot with synthetic 20s microbars and updates `MT5V51ContextBuilder`.
3. On each new 1m bar, the runtime launches an async LLM entry analysis task.
4. Completed analysis tasks are harvested later and re-checked against current freshness before execution.
5. On live snapshot updates, a deterministic fast-entry override can fire before the slower LLM path finishes.
6. `MT5V51RiskArbiter` checks confidence, freshness, spread, trade budget, daily loss, and pending commands.
7. `MT5V51EntryPlanner` builds the bridge command and `MT5V51TicketRegistry` stores the pending entry plan.
8. After fills, the registry tracks setup-quality-aware partial targets and stop management.
9. The runtime runs deterministic post-entry logic:
   - attach first protection if needed
   - auto-scalp partials
   - ratchet stop loss without moving backward
10. Closed tickets become V5.1 reflections and lessons.

How to think about V5.1:

- fast BTC scalp runtime
- OpenRouter entry model
- async analysis plus deterministic override
- registry-centric management rather than an LLM manager in the main loop

### V6.0 workflow

Main files:

- `src/app/v6_0_mt5.py`
- `ops/mt5/V60BridgeEA.mq5`
- `src/brokers/mt5_v60/`
- `src/runtime/mt5_v60_context_packet.py`
- `src/execution/mt5_v60_immediate_entry.py`

What changes relative to V5.1:

- execution frame moves to 3m, with 1m and 2m as support
- snapshots include chart screenshot state and recent close events
- the entry and manager agents are multimodal
- entry protection is intentionally staged after naked fills

What happens:

1. The EA captures a chart screenshot on its own interval, then publishes a snapshot with:
   - 1m, 2m, 3m, and 5m bars
   - account state
   - open tickets
   - screenshot metadata
   - recent close events
2. The runtime updates `MT5V60BridgeState`, `MT5V60ContextBuilder`, and screenshot state.
3. Acknowledgements are drained and applied to `MT5V60TicketRegistry`.
4. Closed tickets generate reflections and lessons.
5. If there is no open position, entry can come from:
   - deterministic fast breakout logic
   - standard multimodal LLM entry
   - stop-loss reversal entry after a stopped trade
6. `MT5V60RiskArbiter` approves or vetoes the decision.
7. `MT5V60ImmediateEntryBuilder` turns the decision into an immediate entry command and plan payload.
8. After fills, the runtime may auto-attach first protection if the position is still naked.
9. Before the manager runs, deterministic scalp-guard rules can partially close or tighten stops on their own.
10. The multimodal manager then reviews open trades, optionally using a fresh screenshot, and can modify protection, reduce size, or exit.

How to think about V6.0:

- multimodal MT5 runtime
- stronger separation between entry analysis, immediate entry building, and later protection placement
- deterministic management rules run before the LLM manager

### V6.1 workflow

Main files:

- `src/app/v6_1_mt5.py`
- `ops/mt5/V61BridgeEA.mq5`
- `src/brokers/mt5_v60/`

V6.1 is not a brand-new stack. It is a multi-symbol orchestration layer built on top of the V6.0 bridge, agents, risk, and execution logic.

What happens:

1. The bridge stores the latest snapshot per symbol.
2. The runtime maintains per-symbol state:
   - risk arbiter
   - reflections
   - lessons
   - context builder
   - screenshot state
   - last entry bar
   - last manager run
3. When a snapshot arrives, the updated symbol is prioritized.
4. For each symbol, the runtime reuses the V6.0 helper flows:
   - ack processing
   - registry sync
   - stop-loss reversal entry
   - fast entry
   - standard entry
   - entry protection
   - deterministic management
   - manager sweep
5. Shutdown flattening can close remaining tickets across all symbols when the session ends and shadow mode is off.

How to think about V6.1:

- V6.0 logic, lifted to symbol-scoped runtime state
- same agents, same bridge protocol family, broader orchestration

## MT5-Focused Code Map

```text
ops/mt5/                 Expert Advisors that run inside MetaTrader 5
src/app/v5_mt5.py        V5 runtime
src/app/v5_1_mt5.py      V5.1 runtime
src/app/v6_0_mt5.py      V6.0 runtime
src/app/v6_1_mt5.py      V6.1 runtime
src/app/config.py        Shared settings, including V5 MT5 settings
src/app/v5_1_config.py   V5.1 settings
src/app/v6_0_config.py   V6.0 settings
src/app/v6_1_config.py   V6.1 settings
src/brokers/mt5*/        Bridge apps and bridge state
src/agents/mt5*.py       Entry and manager agents
src/data/mt5*_schemas.py Versioned MT5 payload schemas
src/runtime/mt5*.py      Context builders, quote tapes, microbars, symbol helpers
src/risk/mt5*.py         Deterministic MT5 risk logic
src/execution/mt5*.py    Entry planners, immediate entry builders, ticket registries
src/feedback/mt5*.py     Trade reflection and lesson extraction
src/memory/supabase_mt5* Versioned MT5 persistence stores
```

## Getting Started

### 1. Install dependencies

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
npm --prefix frontend install
```

### 2. Create `.env`

```bash
cp .env.example .env
```

Then fill in only the variables for the runtime you want to use.

Important config files:

- `src/app/config.py`
- `src/app/v5_1_config.py`
- `src/app/v6_0_config.py`
- `src/app/v6_1_config.py`

Important note:

- V5 reads its MT5 settings from `src/app/config.py` via `MT5_*` variables.
- `.env.example` covers the baseline shared settings and the V5.1 `V51_*` variables.
- The V5 `MT5_*` variables are not listed in `.env.example`, so check `src/app/config.py` before running V5.
- V6.0 and V6.1 also use `V60_*` and `V61_*` variables defined in their config modules.
- Read those config files before running the V6 runtimes.

### 3. Install the EA in MT5

The bridge EAs live in `ops/mt5/`.

Use the matching EA for the runtime you want:

- V5: `ops/mt5/V5BridgeEA.mq5`
- V5.1: `ops/mt5/V51BridgeEA.mq5`
- V6.0: `ops/mt5/V60BridgeEA.mq5`
- V6.1: `ops/mt5/V61BridgeEA.mq5`
- manual tester replay: `ops/mt5/ATManualReplayTesterEA.mq5`

Each EA has its own default local bridge port:

- V5: `8090`
- V5.1: `8091`
- V6.0: `8092`
- V6.1: `8093`

### 4. Start the runtime

Recommended commands:

```bash
.venv/bin/at-agent-v5-mt5 --duration-minutes 60
scripts/run_v5_1_mt5.sh --duration-minutes 60 --shadow-mode
scripts/run_v6_0_mt5.sh --duration-minutes 60 --shadow-mode
scripts/run_v6_1_mt5.sh --duration-minutes 60 --shadow-mode
```

Safety notes:

- V5.1 defaults to shadow mode in config.
- V6.0 and V6.1 config defaults are more execution-ready, so pass `--shadow-mode` unless you intentionally want command execution.
- `scripts/run_v5_mt5.sh` forces `--enable-trade-commands`, so avoid that helper if you want a safer non-command V5 session.

## Manual Replay In MT5 Strategy Tester

There is now a separate tester-safe path for manual practice trading in visual backtests.

Files:

- tester EA: `ops/mt5/ATManualReplayTesterEA.mq5`
- controller CLI: `src/app/mt5_manual_replay.py`
- helper launcher: `scripts/run_mt5_manual_replay.sh`

This path is intentionally separate from the live bridge runtimes:

- it does not use the local HTTP bridge
- it uses the MT5 `Common/Files` shared folder
- it is intended for personal replay practice, not unattended runtime execution

Quick start:

```bash
scripts/run_mt5_manual_replay.sh --session btc-practice-1 init --reset
scripts/run_mt5_manual_replay.sh --session btc-practice-1 buy 0.10 --sl-points 300 --tp-points 600
scripts/run_mt5_manual_replay.sh --session btc-practice-1 tail-acks --follow
```

Full instructions live in `docs/mt5_manual_replay.md`.

## Chart Replay Workspace

There is also a separate normal-chart replay workspace for manual practice that preserves MT5 drawing tools.

File:

- EA: `ops/mt5/ATChartReplayWorkspaceEA.mq5`

This path differs from Strategy Tester replay:

- it runs on a normal chart, not the tester visualizer
- it creates and updates a custom symbol
- it keeps MT5 drawing tools available
- it simulates manual trades on the replay chart

It supports:

- replay from a chosen start time
- `BAR` stepping
- `TICK` stepping when historical ticks are available
- `play`, `pause`, `reset`, and speed controls
- simulated `BUY`, `SELL`, and `CLOSE`

Usage instructions live in `docs/mt5_chart_replay_workspace.md`.

## Dashboard And Control Plane

The dashboard is still useful even though the repo is MT5-first.

Primary path:

- backend: `src/dashboard_api/app.py`
- frontend: `frontend/`
- database: Supabase/Postgres

Run it with:

```bash
.venv/bin/at-agent-dashboard-api
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Relevant control-plane code:

- `src/control_plane/`
- `src/memory/supabase.py`
- `src/memory/supabase_mt5_v51.py`
- `src/memory/supabase_mt5_v60.py`
- `supabase/migrations/`

The dashboard and database are where you inspect:

- agent configs
- policy versions
- heartbeats
- backtest jobs and runs
- runtime history
- lessons
- promotions

## Testing

Run the unit tests with:

```bash
pytest
```

There is broad MT5 coverage under `tests/unit/`, including:

- bridge state
- context packets
- entry planners
- ticket registries
- risk policies
- entry agents
- manager agents
- V5.1 runtime behavior
- V6.0 and V6.1 runtime behavior

## Legacy Note

There is still Alpaca and historical research code in the repository, along with earlier dashboard and review tooling.
It is no longer the main story of the project.
If you are trying to understand what this repo is for today, start with the MT5 files listed above and treat the Alpaca path as legacy or side-track research.
