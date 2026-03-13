from __future__ import annotations

from collections import Counter

from data.schemas import BacktestReport


def render_backtest_report_markdown(
    report: BacktestReport,
    *,
    baseline_label: str,
    candidate_label: str,
) -> str:
    lines: list[str] = [
        f"# Backtest Report: {candidate_label}",
        "",
        "## Window",
        f"- Symbol: {report.symbol}",
        f"- Timeframe: {report.timeframe}",
        f"- Range: {report.start_at.isoformat()} to {report.end_at.isoformat()}",
        f"- Total bars: {report.total_bars}",
        "",
        "## Scorecard",
        f"- Baseline ({baseline_label}) score: {report.baseline.score:.2f}",
        f"- Candidate ({candidate_label}) score: {report.candidate.score:.2f}",
        f"- Decision: {report.decision.status}",
        "",
        "## Candidate Metrics",
        f"- Closed trades: {report.candidate.closed_trades}",
        f"- Win rate: {report.candidate.win_rate:.2%}",
        f"- Realized PnL: {report.candidate.realized_pnl_bps:.2f} bps",
        f"- Average trade: {report.candidate.average_trade_bps:.2f} bps",
        f"- Max drawdown: {report.candidate.max_drawdown_bps:.2f} bps",
        f"- Exposure ratio: {report.candidate.exposure_ratio:.2%}",
    ]

    if report.trade_summary is not None:
        trade_summary = report.trade_summary
        lines.extend(
            [
                "",
                "## Trade Summary",
                f"- Total trades: {trade_summary.total_trades}",
                f"- Winning trades: {trade_summary.winning_trades}",
                f"- Losing trades: {trade_summary.losing_trades}",
                f"- Breakeven trades: {trade_summary.breakeven_trades}",
                f"- Average planned risk: ${trade_summary.average_planned_risk_usd:.2f}",
                f"- Average planned SL: {trade_summary.average_planned_stop_loss_bps:.2f} bps",
                f"- Average planned TP1: {trade_summary.average_planned_take_profit_bps:.2f} bps",
                f"- Average planned max TP: {trade_summary.average_planned_max_take_profit_bps:.2f} bps",
                f"- Average planned trailing stop: {trade_summary.average_planned_trailing_stop_bps:.2f} bps",
                f"- Average bars held: {trade_summary.average_bars_held:.2f}",
            ]
        )
        if trade_summary.exit_reason_counts:
            lines.extend(["", "## Exit Reasons"])
            for reason, count in sorted(trade_summary.exit_reason_counts.items()):
                lines.append(f"- {reason}: {count}")

    if report.regime_summary is not None:
        regime_summary = report.regime_summary
        lines.extend(["", "## Regime Summary"])
        for regime, count in sorted(regime_summary.regime_occupancy.items()):
            average_probability = regime_summary.average_regime_probability.get(regime, 0.0)
            entry_count = regime_summary.entry_regime_counts.get(regime, 0)
            lines.append(
                f"- {regime}: occupancy={count}, entry_count={entry_count}, avg_probability={average_probability:.2%}"
            )

    return "\n".join(lines)


def render_comparison_markdown(
    *,
    baseline_report: BacktestReport,
    candidate_report: BacktestReport,
    baseline_label: str,
    candidate_label: str,
) -> str:
    lines = [
        f"# Comparison: {candidate_label} vs {baseline_label}",
        "",
        f"- Baseline score: {baseline_report.candidate.score:.2f}",
        f"- Candidate score: {candidate_report.candidate.score:.2f}",
        f"- Baseline realized PnL: {baseline_report.candidate.realized_pnl_bps:.2f} bps",
        f"- Candidate realized PnL: {candidate_report.candidate.realized_pnl_bps:.2f} bps",
        f"- Baseline trades: {baseline_report.candidate.closed_trades}",
        f"- Candidate trades: {candidate_report.candidate.closed_trades}",
        f"- Baseline win rate: {baseline_report.candidate.win_rate:.2%}",
        f"- Candidate win rate: {candidate_report.candidate.win_rate:.2%}",
        f"- Baseline max drawdown: {baseline_report.candidate.max_drawdown_bps:.2f} bps",
        f"- Candidate max drawdown: {candidate_report.candidate.max_drawdown_bps:.2f} bps",
    ]
    return "\n".join(lines)
