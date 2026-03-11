# AGENTS.md

Date: 2026-03-11

## Project Summary

This repository is for a research-phase autonomous trading system built around Alpaca paper trading for `ETH/USD`.

The goal is to design and validate an agent workflow that:

- consumes live crypto market data
- analyzes the market continuously
- decides whether to trade
- respects account balance and user-defined risk
- avoids overtrading
- executes paper trades through Alpaca
- logs all decisions and outcomes
- improves through a controlled feedback loop

This project is not for live trading yet. Paper trading and evaluation come first.

## Current Scope

- broker: Alpaca
- symbol: `ETH/USD`
- mode: paper trading only
- runtime: 24/7
- near-term runtime: local testing and refinement are expected during research
- deployment target: final always-on runtime should be a VPS/VM, not a personal laptop or desktop
- account type: free tier
- request budget: stay under `200` trading API requests per minute

## Core Principles

1. The analyst model is advisory, not sovereign.
It can recommend actions, but it cannot bypass deterministic risk and execution rules.

2. Use WebSockets where possible.
Do not design the system around high-frequency polling if streaming is available.

3. Keep risk deterministic and explicit.
Hard rules must govern position sizing, cooldowns, kill switches, and account exposure.

4. Prefer simple, auditable architecture over agent sprawl.
Start with a few bounded modules rather than many loosely defined agents.

5. Learn from logs, not from uncontrolled self-modification.
Improvements should be tested offline and versioned before being promoted.

## Recommended System Modules

- `Market Data Service`: live Alpaca crypto stream ingestion
- `Portfolio State Service`: account, positions, and open order state
- `Analyst Service`: market interpretation and trade recommendation
- `Risk Service`: approval, sizing, and veto logic
- `Execution Service`: order placement and reconciliation
- `Review Service`: post-trade analysis and lesson extraction

## Memory Model

Use multiple memory layers:

- hot state memory for current market and account state
- episodic memory for decisions and trades
- lesson memory for recurring patterns
- policy memory for prompt and strategy versions

Structured storage is preferred over generic chat memory.

## Risk Expectations

The project should include:

- max risk per trade
- max notional exposure
- max trades per hour
- max daily loss
- cooldown after losses or exits
- stale-data detection
- state mismatch detection
- manual and automatic kill switch support

## Engineering Expectations

- log every decision and every order event
- keep all prompts, thresholds, and policies versioned
- prefer deterministic code for execution and safety logic
- keep the first version focused on one symbol only
- use paper trading to validate behavior before considering any live path
- treat the database as structured memory for logs, lessons, and policy history, not as permission for uncontrolled live self-modification
- use Supabase as the primary database when persistent database storage is needed
- when reading, writing, or changing database state or schema, use the Supabase CLI

## Immediate Build Direction

Build order for the next implementation steps:

1. project scaffolding
2. Alpaca paper client
3. `ETH/USD` market data stream
4. account and position synchronization
5. order placement and order updates
6. risk engine
7. decision journal
8. analyst layer
9. review and learning loop

## Working Note For Future Sessions

When continuing work in this repository:

- assume the project is still in research phase unless the user explicitly changes scope
- do not introduce live trading behavior by default
- keep Alpaca paper trading as the default broker integration
- keep `ETH/USD` as the default symbol unless the user explicitly expands scope
- assume near-term work is local testing and refinement, with VPS/VM deployment later after the agent is stable enough
- use the Supabase CLI for database operations instead of ad hoc manual database changes
- optimize first for correctness, auditability, and risk control
