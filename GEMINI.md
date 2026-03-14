# GEMINI.md - at-agent (Algorithmic Trading Agent)

## Project Overview
`at-agent` is a research-phase autonomous trading system designed to validate agent workflows across multiple runtime tracks. It emphasizes deterministic risk control, observability, and an advisory analyst model.

- **Primary Symbol (Alpaca):** ETH/USD (Paper trading only)
- **Primary Symbol (MT5 v5):** EURUSD (Demo/Shadow mode)
- **Primary Symbol (MT5 v5.1):** BTCUSD (Demo/Shadow mode)
- **Control Plane:** Supabase-backed for policy versioning, heartbeats, and operator management.

## Key Technologies
- **Backend:** Python 3.11+, FastAPI, Pydantic (Settings), Pandas, NumPy, Scikit-learn, HMM (Hidden Markov Models), Psycopg (PostgreSQL).
- **Frontend:** React 19, TypeScript, Vite, Recharts, Lucide-react.
- **Brokers:** Alpaca (Crypto API), MT5 (via bridge EA).
- **Database:** Supabase / PostgreSQL.
- **AI/LLM:** OpenAI-compatible APIs, OpenRouter.

## Building and Running

### Backend Setup
1. Create and activate a virtual environment (`python -m venv .venv`).
2. Install dependencies in editable mode: `pip install -e .`.
3. Configure environment variables in `.env` (refer to `.env.example`).

### Frontend Setup
1. `cd frontend`
2. `npm install`
3. `npm run dev` (starts the Vite dev server)

### Core Runtimes
- **Alpaca v4 Live:** `at-agent-v4-live`
- **MT5 v5:** `at-agent-v5-mt5`
- **MT5 v5.1:** `at-agent-v5-1-mt5`
- **Dashboard API:** `at-agent-dashboard-api` (FastAPI)
- **Legacy Dashboard:** `at-agent-dashboard` (Streamlit)

### Database Management
Use the Supabase CLI for migrations and schema changes:
`supabase migration up`
`supabase db push`

## Development Conventions

### General Rules
- **Research Phase:** Default to shadow, paper, or demo modes. Never enable live trading commands unless explicitly requested.
- **Risk Control:** Execution and risk logic (position sizing, stop-loss, cooldowns) must remain deterministic and code-based.
- **Logging:** Every analyst recommendation, risk decision, and execution outcome must be logged to the `var/` directory or Supabase.

### Directory Structure
- `src/agents/`: Specialized agents (analysts, reviewers, position managers).
- `src/app/`: Main CLI entry points and runtime configurations.
- `src/brokers/`: Integration logic for Alpaca and MT5.
- `src/dashboard_api/`: FastAPI backend for the React frontend.
- `frontend/`: React + Vite operator dashboard.
- `scripts/`: Helper scripts for local execution and deployment.
- `strategies/`: Markdown documentation of strategy versions and research.
- `var/`: Local journals, lessons, and report artifacts.

### MT5 Workflow
MT5 files (`V5BridgeEA.mq5`, `V51BridgeEA.mq5`) should be copied to the Wine-based MetaTrader 5 directory on macOS:
`/Users/omid/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/Advisors/`
