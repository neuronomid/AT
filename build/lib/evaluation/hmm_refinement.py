from __future__ import annotations

from dataclasses import dataclass

from control_plane.models import PolicyVersionRecord
from control_plane.policies import build_hmm_v3_policy
from data.schemas import BacktestReport


@dataclass
class HMMRefinementResult:
    policy: PolicyVersionRecord
    notes: str


class HMMStrategyRefiner:
    """Applies bounded, review-friendly parameter updates for the V3 HMM strategy."""

    def refine(
        self,
        *,
        base_policy: PolicyVersionRecord,
        report: BacktestReport,
        advisor_markdown: str,
        next_version: str,
    ) -> HMMRefinementResult:
        thresholds = dict(base_policy.thresholds)
        strategy_config = dict(base_policy.strategy_config)
        advice = advisor_markdown.lower()
        metrics = report.candidate

        low_activity = metrics.closed_trades < 10
        weak_expectancy = metrics.realized_pnl_bps <= 0 or metrics.average_trade_bps <= 0
        poor_win_rate = metrics.win_rate < 0.45
        drawdown_pressure = metrics.max_drawdown_bps > max(60.0, abs(metrics.realized_pnl_bps) * 1.2)

        if low_activity:
            strategy_config["hmm_bull_entry_probability"] = self._bounded_float(
                strategy_config, "hmm_bull_entry_probability", -0.02, 0.55, 0.72
            )
            strategy_config["hmm_bull_continuation_probability"] = self._bounded_float(
                strategy_config, "hmm_bull_continuation_probability", -0.02, 0.52, 0.7
            )
            thresholds["min_confirmation_count"] = self._bounded_int(
                thresholds, "min_confirmation_count", -1, 4, 7
            )
            thresholds["breakout_buffer_bps"] = self._bounded_float(
                thresholds, "breakout_buffer_bps", -0.2, 0.8, 2.0
            )

        if weak_expectancy and not low_activity:
            strategy_config["hmm_bull_entry_probability"] = self._bounded_float(
                strategy_config, "hmm_bull_entry_probability", 0.015, 0.55, 0.75
            )
            strategy_config["hmm_bull_continuation_probability"] = self._bounded_float(
                strategy_config, "hmm_bull_continuation_probability", 0.015, 0.52, 0.72
            )
            thresholds["min_volume_ratio_5_30"] = self._bounded_float(
                thresholds, "min_volume_ratio_5_30", 0.03, 1.0, 1.2
            )

        if poor_win_rate or "stress" in advice or "drawdown" in advice:
            strategy_config["hmm_bear_exit_probability"] = self._bounded_float(
                strategy_config, "hmm_bear_exit_probability", -0.03, 0.42, 0.6
            )
            strategy_config["hmm_stress_exit_probability"] = self._bounded_float(
                strategy_config, "hmm_stress_exit_probability", -0.03, 0.4, 0.58
            )
            strategy_config["time_stop_bars"] = self._bounded_int(
                strategy_config, "time_stop_bars", -30, 120, 240
            )

        if drawdown_pressure or "risk" in advice:
            strategy_config["requested_risk_fraction"] = self._bounded_float(
                strategy_config, "requested_risk_fraction", -0.0002, 0.001, 0.002
            )
            strategy_config["max_requested_notional_fraction_cash"] = self._bounded_float(
                strategy_config, "max_requested_notional_fraction_cash", -0.01, 0.05, 0.1
            )

        if "take profit" in advice or "exit" in advice:
            strategy_config["partial_take_profit_fraction"] = self._bounded_float(
                strategy_config, "partial_take_profit_fraction", 0.05, 0.3, 0.5
            )
            strategy_config["trailing_stop_multiple"] = self._bounded_float(
                strategy_config, "trailing_stop_multiple", -0.05, 0.6, 0.9
            )

        policy = build_hmm_v3_policy(
            version=next_version,
            notes="V3.1 refinement derived from the V3.0 HMM replay and offline advisor review.",
            thresholds_overrides=thresholds,
            strategy_overrides=strategy_config,
        )
        return HMMRefinementResult(
            policy=policy,
            notes="Bounded HMM threshold updates applied from the V3.0 replay and advisor markdown.",
        )

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
