from __future__ import annotations

from dataclasses import dataclass

from control_plane.models import PolicyVersionRecord
from data.schemas import BacktestReport


@dataclass
class RefinementResult:
    policy_name: str
    version: str
    status: str
    thresholds: dict[str, object]
    risk_params: dict[str, object]
    strategy_config: dict[str, object]
    notes: str


class PolicyRefiner:
    """Deterministic threshold updates guided by backtest metrics and advisor notes."""

    def __init__(self, *, min_closed_trades_90d: int = 18) -> None:
        self._min_closed_trades_90d = min_closed_trades_90d

    def refine(
        self,
        *,
        policies: dict[str, PolicyVersionRecord],
        report: BacktestReport,
        advisor_markdown: str,
        next_version: str,
    ) -> list[RefinementResult]:
        metrics = report.candidate
        advice = advisor_markdown.lower()
        low_activity = metrics.closed_trades < self._min_closed_trades_90d or "do_nothing" in advice
        high_activity = metrics.closed_trades > max(self._min_closed_trades_90d * 8, 140)
        weak_edge = metrics.realized_pnl_bps <= 0 or metrics.average_trade_bps <= 0
        poor_win_rate = metrics.win_rate < 0.45
        decent_win_rate_but_negative = metrics.win_rate >= 0.48 and metrics.average_trade_bps <= 0
        drawdown_pressure = metrics.max_drawdown_bps > max(abs(metrics.realized_pnl_bps) * 1.25, 6.0)
        advice_wants_exit_work = "exit" in advice or "giveback" in advice or "trailing" in advice
        advice_wants_quality = "trade quality" in advice or "confirmation" in advice or "confluence" in advice

        refined: list[RefinementResult] = []
        for policy_name in ("baseline", "conservative", "aggressive"):
            policy = policies[policy_name]
            thresholds = dict(policy.thresholds)
            strategy = dict(policy.strategy_config)
            risk_params = dict(policy.risk_params)
            scale = self._scale_for_policy(policy_name)

            if low_activity:
                self._loosen_for_activity(thresholds, strategy, scale=scale)
            if weak_edge and not low_activity:
                if poor_win_rate or advice_wants_quality or high_activity:
                    self._tighten_entries(thresholds, strategy, scale=scale)
                if decent_win_rate_but_negative or advice_wants_exit_work:
                    self._improve_exit_efficiency(thresholds, strategy, scale=scale)
            if drawdown_pressure:
                self._trim_drawdown(thresholds, strategy, scale=scale)

            if low_activity and weak_edge:
                self._rebalance_for_activity_and_costs(thresholds, strategy, scale=scale)

            notes = self._build_notes(
                parent_label=policy.label,
                low_activity=low_activity,
                weak_edge=weak_edge,
                poor_win_rate=poor_win_rate,
                decent_win_rate_but_negative=decent_win_rate_but_negative,
                drawdown_pressure=drawdown_pressure,
            )
            refined.append(
                RefinementResult(
                    policy_name=policy_name,
                    version=next_version,
                    status="baseline" if policy_name == "baseline" else "candidate",
                    thresholds=thresholds,
                    risk_params=risk_params,
                    strategy_config=strategy,
                    notes=notes,
                )
            )

        return refined

    def _scale_for_policy(self, policy_name: str) -> float:
        if policy_name == "conservative":
            return 0.75
        if policy_name == "aggressive":
            return 1.2
        return 1.0

    def _loosen_for_activity(self, thresholds: dict[str, object], strategy: dict[str, object], *, scale: float) -> None:
        thresholds["min_regime_probability"] = self._bounded_float(thresholds, "min_regime_probability", -0.02 * scale, 0.5, 0.75)
        thresholds["entry_momentum_3_bps"] = self._bounded_float(thresholds, "entry_momentum_3_bps", -0.6 * scale, 3.0, 10.0)
        thresholds["entry_momentum_5_bps"] = self._bounded_float(thresholds, "entry_momentum_5_bps", -0.9 * scale, 5.0, 16.0)
        thresholds["min_trend_strength_bps"] = self._bounded_float(thresholds, "min_trend_strength_bps", -1.5 * scale, 8.0, 24.0)
        thresholds["min_volume_ratio_5_30"] = self._bounded_float(thresholds, "min_volume_ratio_5_30", -0.03 * scale, 0.95, 1.3)
        thresholds["breakout_buffer_bps"] = self._bounded_float(thresholds, "breakout_buffer_bps", -0.12 * scale, 0.15, 1.25)
        thresholds["min_entry_score"] = self._bounded_int(thresholds, "min_entry_score", -1 if scale >= 1 else 0, 3, 7)
        thresholds["min_confirmation_count"] = self._bounded_int(
            thresholds,
            "min_confirmation_count",
            -1 if scale >= 1 else 0,
            2,
            4,
        )
        thresholds["max_spread_bps"] = self._bounded_float(thresholds, "max_spread_bps", 0.8 * scale, 10.0, 20.0)
        strategy["min_expected_edge_bps"] = self._bounded_float(strategy, "min_expected_edge_bps", -0.12 * scale, 0.35, 1.5)
        strategy["min_atr_percentile_30"] = self._bounded_float(strategy, "min_atr_percentile_30", -0.03 * scale, 0.08, 0.3)
        strategy["max_atr_percentile_30"] = self._bounded_float(strategy, "max_atr_percentile_30", 0.03 * scale, 0.7, 0.95)
        strategy["time_stop_bars"] = self._bounded_int(strategy, "time_stop_bars", 1, 8, 20)

    def _tighten_entries(self, thresholds: dict[str, object], strategy: dict[str, object], *, scale: float) -> None:
        thresholds["min_regime_probability"] = self._bounded_float(thresholds, "min_regime_probability", 0.015 * scale, 0.5, 0.8)
        thresholds["entry_momentum_3_bps"] = self._bounded_float(thresholds, "entry_momentum_3_bps", 0.5 * scale, 3.0, 12.0)
        thresholds["entry_momentum_5_bps"] = self._bounded_float(thresholds, "entry_momentum_5_bps", 0.8 * scale, 5.0, 18.0)
        thresholds["min_trend_strength_bps"] = self._bounded_float(thresholds, "min_trend_strength_bps", 1.2 * scale, 8.0, 26.0)
        thresholds["min_volume_ratio_5_30"] = self._bounded_float(thresholds, "min_volume_ratio_5_30", 0.03 * scale, 0.95, 1.35)
        thresholds["breakout_buffer_bps"] = self._bounded_float(thresholds, "breakout_buffer_bps", 0.12 * scale, 0.15, 1.5)
        thresholds["min_entry_score"] = self._bounded_int(thresholds, "min_entry_score", 1 if scale <= 1 else 0, 3, 7)
        strategy["min_expected_edge_bps"] = self._bounded_float(strategy, "min_expected_edge_bps", 0.12 * scale, 0.35, 1.8)
        strategy["base_slippage_bps"] = self._bounded_float(strategy, "base_slippage_bps", -0.05 * scale, 0.7, 1.5)
        thresholds["max_spread_bps"] = self._bounded_float(thresholds, "max_spread_bps", -0.8 * scale, 10.0, 20.0)

    def _improve_exit_efficiency(self, thresholds: dict[str, object], strategy: dict[str, object], *, scale: float) -> None:
        strategy["partial_take_profit_fraction"] = self._bounded_float(
            strategy,
            "partial_take_profit_fraction",
            -0.05 * scale,
            0.25,
            0.5,
        )
        strategy["trailing_stop_multiple"] = self._bounded_float(strategy, "trailing_stop_multiple", -0.05 * scale, 0.45, 0.75)
        strategy["max_reward_multiple"] = self._bounded_float(strategy, "max_reward_multiple", 0.1 * scale, 1.8, 2.6)
        strategy["time_stop_bars"] = self._bounded_int(strategy, "time_stop_bars", -1, 6, 18)
        thresholds["hard_exit_momentum_3_bps"] = self._bounded_float(thresholds, "hard_exit_momentum_3_bps", -0.5 * scale, 5.0, 12.0)
        thresholds["hard_exit_momentum_5_bps"] = self._bounded_float(thresholds, "hard_exit_momentum_5_bps", -0.8 * scale, 8.0, 18.0)

    def _trim_drawdown(self, thresholds: dict[str, object], strategy: dict[str, object], *, scale: float) -> None:
        strategy["requested_risk_fraction"] = self._bounded_float(strategy, "requested_risk_fraction", -0.0002 * scale, 0.0012, 0.003)
        strategy["max_requested_notional_fraction_cash"] = self._bounded_float(
            strategy,
            "max_requested_notional_fraction_cash",
            -0.01 * scale,
            0.06,
            0.14,
        )
        thresholds["exit_regime_probability"] = self._bounded_float(thresholds, "exit_regime_probability", -0.01 * scale, 0.62, 0.82)

    def _rebalance_for_activity_and_costs(self, thresholds: dict[str, object], strategy: dict[str, object], *, scale: float) -> None:
        thresholds["min_confirmation_count"] = self._bounded_int(thresholds, "min_confirmation_count", 0, 2, 4)
        strategy["partial_take_profit_fraction"] = self._bounded_float(
            strategy,
            "partial_take_profit_fraction",
            0.02 * scale,
            0.25,
            0.45,
        )
        strategy["max_reward_multiple"] = self._bounded_float(strategy, "max_reward_multiple", 0.05 * scale, 1.8, 2.6)

    def _build_notes(
        self,
        *,
        parent_label: str,
        low_activity: bool,
        weak_edge: bool,
        poor_win_rate: bool,
        decent_win_rate_but_negative: bool,
        drawdown_pressure: bool,
    ) -> str:
        reasons: list[str] = [f"Derived from {parent_label}."]
        if low_activity:
            reasons.append("Loosened activity bottlenecks to avoid solving losses by not trading.")
        if weak_edge and poor_win_rate:
            reasons.append("Tightened entry quality due to weak expectancy and poor win rate.")
        if weak_edge and decent_win_rate_but_negative:
            reasons.append("Adjusted exits and cost filters because win rate held up while trade expectancy stayed negative.")
        if drawdown_pressure:
            reasons.append("Reduced requested size pressure because drawdown remained outsized.")
        return " ".join(reasons)

    def _bounded_float(
        self,
        payload: dict[str, object],
        key: str,
        delta: float,
        minimum: float,
        maximum: float,
    ) -> float:
        current = float(payload.get(key, minimum))
        updated = max(minimum, min(maximum, current + delta))
        payload[key] = round(updated, 4)
        return updated

    def _bounded_int(
        self,
        payload: dict[str, object],
        key: str,
        delta: int,
        minimum: int,
        maximum: int,
    ) -> int:
        current = int(payload.get(key, minimum))
        updated = max(minimum, min(maximum, current + delta))
        payload[key] = updated
        return updated
