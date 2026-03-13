from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from app.config import get_settings
from control_plane.models import AgentConfigRecord, BacktestJobRequest
from control_plane.policies import ensure_default_policies
from evaluation.backtest_runner import run_backtest_job
from memory.supabase import SupabaseStore


def _running_in_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=Space+Grotesk:wght@500;700&display=swap');

        :root {
          --deck-bg: #f4f1e8;
          --deck-surface: rgba(255, 255, 255, 0.72);
          --deck-ink: #172119;
          --deck-accent: #1d7b56;
          --deck-danger: #b14935;
          --deck-border: rgba(23, 33, 25, 0.10);
        }

        .stApp {
          background:
            radial-gradient(circle at top left, rgba(29, 123, 86, 0.12), transparent 28%),
            radial-gradient(circle at top right, rgba(177, 73, 53, 0.08), transparent 18%),
            linear-gradient(180deg, #f8f5ed 0%, #f0ebdf 100%);
          color: var(--deck-ink);
          font-family: "IBM Plex Sans", sans-serif;
        }

        h1, h2, h3 {
          font-family: "Space Grotesk", sans-serif !important;
          letter-spacing: -0.03em;
        }

        div[data-testid="stMetric"] {
          background: var(--deck-surface);
          border: 1px solid var(--deck-border);
          border-radius: 18px;
          padding: 0.8rem 1rem;
          backdrop-filter: blur(12px);
        }

        div[data-testid="stDataFrame"] {
          border-radius: 18px;
          overflow: hidden;
        }

        .deck-panel {
          padding: 1rem 1.15rem;
          border: 1px solid var(--deck-border);
          border-radius: 22px;
          background: var(--deck-surface);
          backdrop-filter: blur(14px);
        }

        .deck-caption {
          color: rgba(23, 33, 25, 0.72);
          font-size: 0.92rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _require_store() -> tuple[Any, SupabaseStore]:
    settings = get_settings()
    if settings.supabase_db_dsn is None:
        st.error("SUPABASE_DB_URL is required to use the dashboard.")
        st.stop()
    store = SupabaseStore(settings.supabase_db_dsn)
    ensure_default_policies(store, settings)
    return settings, store


def _policy_options(policies: list[Any]) -> dict[str, str]:
    return {f"{policy.policy_name}@{policy.version} [{policy.status}]": policy.id for policy in policies}


def _format_money(value: Any) -> str:
    if value in (None, ""):
        return "-"
    return f"${float(value):,.2f}"


def _format_pct(value: Any) -> str:
    if value in (None, ""):
        return "-"
    return f"{float(value):.2f}%"


def _safe_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _render_overview(store: SupabaseStore) -> None:
    statuses = store.list_agent_status()
    decisions = store.list_recent_decisions(limit=150)
    outcomes = store.list_recent_trade_outcomes(limit=150)
    runs = store.list_backtest_runs(limit=20)

    active_agents = sum(1 for row in statuses if row.get("configured_status") == "active")
    healthy_agents = sum(1 for row in statuses if row.get("runtime_status") == "healthy")
    total_decisions = len(decisions)
    total_outcomes = len(outcomes)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Configured Agents", len(statuses))
    metric_cols[1].metric("Active Agents", active_agents)
    metric_cols[2].metric("Healthy Agents", healthy_agents)
    metric_cols[3].metric("Recent Decisions", total_decisions)

    status_frame = _safe_frame(statuses)
    if not status_frame.empty:
        status_frame = status_frame[
            [
                "agent_name",
                "configured_status",
                "runtime_status",
                "current_symbol",
                "latest_decision_action",
                "latest_decision_at",
                "equity",
                "cash",
                "strategy_policy_name",
                "strategy_version",
            ]
        ]
        st.markdown("### Agent Health")
        st.dataframe(status_frame, width="stretch", hide_index=True)

    chart_cols = st.columns(2)
    outcomes_frame = _safe_frame(outcomes)
    if not outcomes_frame.empty:
        outcomes_frame["recorded_at"] = pd.to_datetime(outcomes_frame["recorded_at"])
        outcomes_frame = outcomes_frame.sort_values("recorded_at")
        outcomes_frame["cumulative_cash_delta"] = outcomes_frame["cash_delta"].astype(float).cumsum()
        pnl_fig = px.line(
            outcomes_frame,
            x="recorded_at",
            y="cumulative_cash_delta",
            color="agent_name",
            title="Cumulative Cash Delta From Trade Outcomes",
        )
        chart_cols[0].plotly_chart(pnl_fig, width="stretch")

        outcome_counts = outcomes_frame.groupby("outcome").size().reset_index(name="count")
        outcome_fig = px.bar(
            outcome_counts,
            x="outcome",
            y="count",
            color="outcome",
            title="Outcome Mix",
        )
        chart_cols[1].plotly_chart(outcome_fig, width="stretch")
    else:
        chart_cols[0].info("No trade outcomes recorded yet.")
        chart_cols[1].info("Run the agent or review step to populate outcomes.")

    runs_frame = _safe_frame(runs)
    if not runs_frame.empty:
        runs_frame["created_at"] = pd.to_datetime(runs_frame["created_at"])
        runs_frame["candidate_score"] = runs_frame["candidate_metrics"].apply(lambda item: float((item or {}).get("score", 0.0)))
        runs_frame["baseline_score"] = runs_frame["baseline_metrics"].apply(lambda item: float((item or {}).get("score", 0.0)))
        score_fig = px.line(
            runs_frame.sort_values("created_at"),
            x="created_at",
            y=["baseline_score", "candidate_score"],
            title="Backtest Score Trend",
            markers=True,
        )
        st.plotly_chart(score_fig, width="stretch")
    else:
        st.info("No stored backtests yet.")


def _render_agents(store: SupabaseStore) -> None:
    policies = store.list_policy_versions()
    policy_options = _policy_options(policies)
    existing_agents = store.list_agent_configs()

    st.markdown("### Agent Registry")
    st.caption("Use separate agents for separate symbols. The live runtime currently trades the first symbol in each agent's list.")
    st.dataframe(_safe_frame([agent.model_dump(mode="json") for agent in existing_agents]), width="stretch", hide_index=True)

    agent_lookup = {agent.agent_name: agent for agent in existing_agents}
    selected_name = st.selectbox("Agent", options=["Create new"] + list(agent_lookup.keys()))
    selected_agent = agent_lookup.get(selected_name)

    with st.form("agent_form"):
        agent_name = st.text_input("Agent name", value="" if selected_agent is None else selected_agent.agent_name)
        description = st.text_input(
            "Description",
            value="" if selected_agent is None or selected_agent.description is None else selected_agent.description,
        )
        status = st.selectbox(
            "Status",
            options=["active", "paused", "shadow", "stopped"],
            index=["active", "paused", "shadow", "stopped"].index(
                "active" if selected_agent is None else selected_agent.status
            ),
        )
        symbols = st.text_input(
            "Symbols (comma-separated)",
            value="ETH/USD" if selected_agent is None else ", ".join(selected_agent.symbols),
        )
        cols = st.columns(4)
        decision_interval_seconds = cols[0].number_input(
            "Decision Interval",
            min_value=5,
            value=60 if selected_agent is None else selected_agent.decision_interval_seconds,
        )
        max_trades_per_hour = cols[1].number_input(
            "Max Trades/Hour",
            min_value=1,
            value=6 if selected_agent is None else selected_agent.max_trades_per_hour,
        )
        max_risk_per_trade_pct = cols[2].number_input(
            "Risk/Trade",
            min_value=0.0001,
            max_value=1.0,
            value=0.005 if selected_agent is None else float(selected_agent.max_risk_per_trade_pct),
            step=0.001,
            format="%.4f",
        )
        max_daily_loss_pct = cols[3].number_input(
            "Max Daily Loss",
            min_value=0.0001,
            max_value=1.0,
            value=0.02 if selected_agent is None else float(selected_agent.max_daily_loss_pct),
            step=0.001,
            format="%.4f",
        )

        cols = st.columns(4)
        max_position_notional_usd = cols[0].number_input(
            "Max Position USD",
            min_value=1.0,
            value=100.0 if selected_agent is None else float(selected_agent.max_position_notional_usd),
            step=10.0,
        )
        max_spread_bps = cols[1].number_input(
            "Max Spread Bps",
            min_value=0.0,
            value=20.0 if selected_agent is None else float(selected_agent.max_spread_bps),
            step=1.0,
        )
        min_decision_confidence = cols[2].number_input(
            "Min Confidence",
            min_value=0.0,
            max_value=1.0,
            value=0.6 if selected_agent is None else float(selected_agent.min_decision_confidence),
            step=0.01,
            format="%.2f",
        )
        cooldown_seconds_after_trade = cols[3].number_input(
            "Cooldown Seconds",
            min_value=0,
            value=60 if selected_agent is None else selected_agent.cooldown_seconds_after_trade,
            step=5,
        )

        strategy_labels = [""] + list(policy_options.keys())
        selected_strategy_label = ""
        if selected_agent is not None and selected_agent.strategy_policy_version_id is not None:
            for label, policy_id in policy_options.items():
                if policy_id == selected_agent.strategy_policy_version_id:
                    selected_strategy_label = label
                    break

        strategy_label = st.selectbox(
            "Active Strategy",
            options=strategy_labels,
            index=strategy_labels.index(selected_strategy_label),
        )
        enable_agent_orders = st.checkbox(
            "Allow agent order submissions",
            value=False if selected_agent is None else selected_agent.enable_agent_orders,
        )
        notes = st.text_area("Notes", value="" if selected_agent is None or selected_agent.notes is None else selected_agent.notes)
        submitted = st.form_submit_button("Save Agent")

    if submitted:
        if not agent_name.strip():
            st.error("Agent name is required.")
        else:
            config = AgentConfigRecord(
                agent_name=agent_name.strip(),
                description=description.strip() or None,
                status=status,
                broker="alpaca",
                mode="paper",
                symbols=[item.strip() for item in symbols.split(",") if item.strip()],
                decision_interval_seconds=int(decision_interval_seconds),
                max_trades_per_hour=int(max_trades_per_hour),
                max_risk_per_trade_pct=float(max_risk_per_trade_pct),
                max_daily_loss_pct=float(max_daily_loss_pct),
                max_position_notional_usd=Decimal(str(max_position_notional_usd)),
                max_spread_bps=float(max_spread_bps),
                min_decision_confidence=float(min_decision_confidence),
                cooldown_seconds_after_trade=int(cooldown_seconds_after_trade),
                enable_agent_orders=enable_agent_orders,
                strategy_policy_version_id=policy_options.get(strategy_label),
                notes=notes.strip() or None,
            )
            store.upsert_agent_config(config)
            st.success(f"Saved agent {config.agent_name}.")
            st.rerun()


def _render_strategies(store: SupabaseStore) -> None:
    policies = store.list_policy_versions()
    st.markdown("### Strategy Registry")
    st.dataframe(_safe_frame([policy.model_dump(mode="json") for policy in policies]), width="stretch", hide_index=True)

    with st.form("strategy_form"):
        cols = st.columns(3)
        policy_name = cols[0].text_input("Policy name", value="strategy")
        version = cols[1].text_input("Version", value="v1")
        status = cols[2].selectbox("Status", options=["candidate", "baseline", "shadow", "active", "retired", "rejected"])

        cols = st.columns(3)
        entry_momentum_3_bps = cols[0].number_input("Entry Momentum 3", value=8.0, step=1.0)
        entry_momentum_5_bps = cols[1].number_input("Entry Momentum 5", value=12.0, step=1.0)
        max_spread_bps = cols[2].number_input("Max Spread Bps", value=20.0, step=1.0)

        cols = st.columns(3)
        exit_momentum_3_bps = cols[0].number_input("Exit Momentum 3", value=-8.0, step=1.0)
        exit_momentum_5_bps = cols[1].number_input("Exit Momentum 5", value=-12.0, step=1.0)
        max_volatility_5_bps = cols[2].number_input("Max Volatility 5", value=25.0, step=1.0)

        notes = st.text_area("Notes", placeholder="What hypothesis is this strategy testing?")
        submitted = st.form_submit_button("Save Strategy")

    if submitted:
        policy_id = store.upsert_policy_version(
            policy_name=policy_name.strip(),
            version=version.strip(),
            status=status,
            thresholds={
                "entry_momentum_3_bps": float(entry_momentum_3_bps),
                "entry_momentum_5_bps": float(entry_momentum_5_bps),
                "exit_momentum_3_bps": float(exit_momentum_3_bps),
                "exit_momentum_5_bps": float(exit_momentum_5_bps),
                "max_spread_bps": float(max_spread_bps),
            },
            risk_params={},
            strategy_config={"max_volatility_5_bps": float(max_volatility_5_bps)},
            notes=notes.strip(),
        )
        st.success(f"Saved strategy version {policy_name}@{version} ({policy_id}).")
        st.rerun()


def _render_backtests(settings, store: SupabaseStore) -> None:
    policies = store.list_policy_versions()
    policy_options = _policy_options(policies)
    recent_jobs = store.list_backtest_jobs(limit=20)
    recent_runs = store.list_backtest_runs(limit=20)

    st.markdown("### Backtest Control")
    with st.form("backtest_form"):
        cols = st.columns(3)
        run_name = cols[0].text_input("Run Name", value=f"{settings.agent_name}-walk-forward")
        symbol = cols[1].text_input("Symbol", value=settings.trading_symbol)
        timeframe = cols[2].text_input("Timeframe", value=settings.backtest_timeframe)

        cols = st.columns(5)
        lookback_days = cols[0].number_input("Lookback Days", min_value=1, value=settings.backtest_lookback_days)
        train_window_days = cols[1].number_input("Train Window", min_value=1, value=settings.backtest_train_window_days)
        test_window_days = cols[2].number_input("Test Window", min_value=1, value=settings.backtest_test_window_days)
        step_days = cols[3].number_input("Step Days", min_value=1, value=settings.backtest_step_days)
        warmup_bars = cols[4].number_input("Warmup Bars", min_value=1, value=settings.backtest_warmup_bars)

        starting_cash = st.number_input(
            "Starting Cash USD",
            min_value=100.0,
            value=float(settings.backtest_starting_cash_usd),
            step=100.0,
        )
        baseline_label = st.selectbox("Baseline Strategy", options=list(policy_options.keys()))
        candidate_defaults = [
            label
            for label in policy_options.keys()
            if label != baseline_label and "[candidate]" in label.lower()
        ][:2]
        candidate_labels = st.multiselect(
            "Candidate Strategies",
            options=[label for label in policy_options.keys() if label != baseline_label],
            default=candidate_defaults,
        )
        notes = st.text_area("Notes", placeholder="Hypothesis, market regime, or data caveats.")
        submitted = st.form_submit_button("Run Backtest")

    if submitted:
        if not candidate_labels:
            st.error("Select at least one candidate strategy.")
        else:
            request = BacktestJobRequest(
                run_name=run_name.strip(),
                symbol=symbol.strip(),
                timeframe=timeframe.strip(),
                location=settings.backtest_location,
                lookback_days=int(lookback_days),
                train_window_days=int(train_window_days),
                test_window_days=int(test_window_days),
                step_days=int(step_days),
                warmup_bars=int(warmup_bars),
                starting_cash_usd=Decimal(str(starting_cash)),
                baseline_policy_version_id=policy_options[baseline_label],
                candidate_policy_version_ids=[policy_options[label] for label in candidate_labels],
                notes=notes.strip() or None,
            )
            with st.spinner("Running backtest against Alpaca historical data..."):
                _, run_id, report = asyncio.run(
                    run_backtest_job(settings=settings, store=store, request=request, requested_by="dashboard")
                )
            output_path = Path(settings.backtest_report_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
            st.success(f"Backtest completed. Run id: {run_id}")
            st.rerun()

    if recent_jobs:
        st.markdown("#### Recent Jobs")
        st.dataframe(_safe_frame(recent_jobs), width="stretch", hide_index=True)

    if not recent_runs:
        st.info("No backtest runs have been stored yet.")
        return

    run_labels = {f"{run['run_name']} ({run['created_at']:%Y-%m-%d %H:%M})": run["id"] for run in recent_runs}
    selected_run_label = st.selectbox("Compare Run", options=list(run_labels.keys()))
    selected_run = store.get_backtest_run_details(run_labels[selected_run_label])
    if selected_run is None:
        st.warning("The selected run could not be loaded.")
        return

    run = selected_run["run"]
    baseline_metrics = run.get("baseline_metrics") or {}
    candidate_metrics = run.get("candidate_metrics") or {}
    decision_payload = run.get("decision_payload") or {}
    metrics_cols = st.columns(4)
    metrics_cols[0].metric("Baseline Score", f"{float(baseline_metrics.get('score', 0.0)):.2f}")
    metrics_cols[1].metric("Candidate Score", f"{float(candidate_metrics.get('score', 0.0)):.2f}")
    metrics_cols[2].metric("Decision", str(decision_payload.get("status", "n/a")).replace("_", " ").title())
    metrics_cols[3].metric("Candidate Win Rate", _format_pct(float(candidate_metrics.get("win_rate", 0.0)) * 100))

    comparison_frame = pd.DataFrame(
        [
            {"metric": "Realized PnL (bps)", "baseline": baseline_metrics.get("realized_pnl_bps", 0.0), "candidate": candidate_metrics.get("realized_pnl_bps", 0.0)},
            {"metric": "Average Trade (bps)", "baseline": baseline_metrics.get("average_trade_bps", 0.0), "candidate": candidate_metrics.get("average_trade_bps", 0.0)},
            {"metric": "Max Drawdown (bps)", "baseline": baseline_metrics.get("max_drawdown_bps", 0.0), "candidate": candidate_metrics.get("max_drawdown_bps", 0.0)},
            {"metric": "Exposure Ratio", "baseline": baseline_metrics.get("exposure_ratio", 0.0), "candidate": candidate_metrics.get("exposure_ratio", 0.0)},
        ]
    )
    comparison_fig = px.bar(
        comparison_frame.melt(id_vars="metric", var_name="strategy", value_name="value"),
        x="metric",
        y="value",
        color="strategy",
        barmode="group",
        title="Baseline vs Candidate Metrics",
    )
    st.plotly_chart(comparison_fig, width="stretch")

    windows_frame = _safe_frame(selected_run["windows"])
    if not windows_frame.empty:
        windows_frame["test_start_at"] = pd.to_datetime(windows_frame["test_start_at"])
        windows_frame["selected_policy_name"] = windows_frame["metrics"].apply(lambda item: (item or {}).get("selected_policy_name"))
        windows_frame["score"] = windows_frame["metrics"].apply(lambda item: float(((item or {}).get("metrics") or {}).get("score", 0.0)))
        windows_frame["realized_pnl_bps"] = windows_frame["metrics"].apply(lambda item: float(((item or {}).get("metrics") or {}).get("realized_pnl_bps", 0.0)))
        window_fig = px.line(
            windows_frame.sort_values("test_start_at"),
            x="test_start_at",
            y="score",
            color="policy_name",
            markers=True,
            hover_data=["selected_policy_name", "realized_pnl_bps"],
            title="Walk-Forward Window Scores",
        )
        st.plotly_chart(window_fig, width="stretch")

    trades_frame = _safe_frame(selected_run["trades"])
    if not trades_frame.empty:
        trades_frame["entry_at"] = pd.to_datetime(trades_frame["entry_at"])
        trades_frame["return_bps"] = trades_frame["return_bps"].astype(float)
        returns_fig = px.histogram(
            trades_frame,
            x="return_bps",
            color="policy_name",
            nbins=30,
            title="Trade Return Distribution",
        )
        st.plotly_chart(returns_fig, width="stretch")
        st.dataframe(trades_frame, width="stretch", hide_index=True)


def _render_history(store: SupabaseStore) -> None:
    statuses = store.list_agent_status()
    agent_names = ["All"] + [row["agent_name"] for row in statuses]
    selected_agent = st.selectbox("Filter by agent", options=agent_names)
    agent_filter = None if selected_agent == "All" else selected_agent

    decisions = store.list_recent_decisions(agent_name=agent_filter, limit=200)
    orders = store.list_recent_orders(agent_name=agent_filter, limit=200)
    outcomes = store.list_recent_trade_outcomes(agent_name=agent_filter, limit=200)
    lessons = store.list_recent_lessons(limit=100)

    tabs = st.tabs(["Decisions", "Orders", "Trade Outcomes", "Lessons"])
    tabs[0].dataframe(_safe_frame(decisions), width="stretch", hide_index=True)
    tabs[1].dataframe(_safe_frame(orders), width="stretch", hide_index=True)
    tabs[2].dataframe(_safe_frame(outcomes), width="stretch", hide_index=True)
    tabs[3].dataframe(_safe_frame(lessons), width="stretch", hide_index=True)


def render_dashboard() -> None:
    settings, store = _require_store()
    st.set_page_config(page_title="AT Control Deck", layout="wide", initial_sidebar_state="expanded")
    _inject_styles()

    st.title("AT Control Deck")
    st.caption("Research-phase operator console for paper-trading agents, strategy comparison, and historical review.")

    with st.sidebar:
        st.markdown("### Operating Context")
        st.write(f"Agent: `{settings.agent_name}`")
        st.write(f"Default symbol: `{settings.trading_symbol}`")
        st.write(f"Paper orders enabled: `{settings.enable_paper_test_order}`")
        if st.button("Refresh Data", width="stretch"):
            st.rerun()

    overview_tab, agents_tab, strategies_tab, backtests_tab, history_tab = st.tabs(
        ["Overview", "Agents", "Strategies", "Backtests", "History"]
    )

    with overview_tab:
        _render_overview(store)
    with agents_tab:
        _render_agents(store)
    with strategies_tab:
        _render_strategies(store)
    with backtests_tab:
        _render_backtests(settings, store)
    with history_tab:
        _render_history(store)


def main() -> None:
    script_path = Path(__file__).resolve()
    from streamlit.web.cli import main as streamlit_main

    sys.argv = [
        "streamlit",
        "run",
        str(script_path),
        "--server.headless",
        "true",
        "--server.address",
        get_settings().dashboard_host,
        "--server.port",
        str(get_settings().dashboard_port),
        "--browser.gatherUsageStats",
        "false",
    ]
    raise SystemExit(streamlit_main())


if _running_in_streamlit():
    render_dashboard()
