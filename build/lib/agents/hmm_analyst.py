from __future__ import annotations

from decimal import Decimal
from typing import Literal

from agents.analyst import AnalystAgent
from data.schemas import AccountSnapshot, MarketSnapshot, TradeDecision, TradePlan
from regime.hmm import RegimeInference, RollingHMMRegimeEngine


class HMMRegimeAnalystAgent(AnalystAgent):
    """HMM-led ETH/USD strategy that uses higher-timeframe regime inference as the top-level gate."""

    def __init__(
        self,
        *,
        policy_name: str = "baseline@v3.0",
        hmm_state_count: int = 4,
        hmm_resample_minutes: int = 15,
        hmm_train_window_bars: int = 20 * 24 * 4,
        hmm_retrain_interval_bars: int = 24 * 4,
        hmm_bull_entry_probability: float = 0.62,
        hmm_bull_continuation_probability: float = 0.58,
        hmm_bear_entry_probability: float = 0.62,
        hmm_bear_continuation_probability: float = 0.58,
        hmm_bull_exit_probability: float = 0.52,
        hmm_bear_exit_probability: float = 0.52,
        hmm_stress_exit_probability: float = 0.48,
        trade_direction: Literal["long", "short"] = "long",
        strategy_family: str = "hmm_regime_v3",
        regime_engine: RollingHMMRegimeEngine | None = None,
        **kwargs: float | int | bool | str,
    ) -> None:
        super().__init__(
            policy_name=policy_name,
            allow_short_entries=trade_direction == "short",
            min_sample_count=hmm_train_window_bars,
            **kwargs,
        )
        self.strategy_family = strategy_family
        self.trade_direction = trade_direction
        self.hmm_state_count = hmm_state_count
        self.hmm_resample_minutes = hmm_resample_minutes
        self.hmm_train_window_bars = hmm_train_window_bars
        self.hmm_retrain_interval_bars = hmm_retrain_interval_bars
        self.hmm_bull_entry_probability = hmm_bull_entry_probability
        self.hmm_bull_continuation_probability = hmm_bull_continuation_probability
        self.hmm_bear_entry_probability = hmm_bear_entry_probability
        self.hmm_bear_continuation_probability = hmm_bear_continuation_probability
        self.hmm_bull_exit_probability = hmm_bull_exit_probability
        self.hmm_bear_exit_probability = hmm_bear_exit_probability
        self.hmm_stress_exit_probability = hmm_stress_exit_probability
        self._regime_engine = regime_engine or RollingHMMRegimeEngine(
            symbol="ETH/USD",
            resample_minutes=hmm_resample_minutes,
            state_count=hmm_state_count,
            train_window_bars=hmm_train_window_bars,
            retrain_interval_bars=hmm_retrain_interval_bars,
        )

    def analyze(
        self,
        market_snapshot: MarketSnapshot | None,
        account_snapshot: AccountSnapshot | None,
        features: dict[str, float],
    ) -> TradeDecision:
        if market_snapshot is None or account_snapshot is None:
            return TradeDecision(
                action="do_nothing",
                confidence=0.0,
                rationale="Missing market or account context.",
                entry_blockers=["missing_market_or_account_context"],
            )

        inference = self._regime_engine.update(market_snapshot)
        spread_bps = features.get("spread_bps", 0.0)
        if spread_bps > self.max_spread_bps:
            return self._blocked_decision(
                blockers=["spread_above_limit"],
                confidence=0.0,
                regime=inference.regime,
                regime_probability=inference.regime_probability,
                regime_probabilities=inference.regime_probabilities,
                continuation_probabilities=inference.continuation_probabilities,
            )

        if not inference.model_ready:
            return self._blocked_decision(
                blockers=["insufficient_completed_15m_history_for_hmm"],
                confidence=0.0,
                regime=None,
                regime_probability=0.0,
            )

        bull_probability = inference.regime_probabilities.get("bull_trend", 0.0)
        bear_probability = inference.regime_probabilities.get("bear_trend", 0.0)
        stress_probability = inference.regime_probabilities.get("stress", 0.0)
        bull_continuation = inference.continuation_probabilities.get("bull_trend", 0.0)
        bear_continuation = inference.continuation_probabilities.get("bear_trend", 0.0)
        position_side = self._position_side(account_snapshot.open_position_qty)
        momentum_3 = features.get("return_3_bps", 0.0)
        momentum_5 = features.get("return_5_bps", 0.0)

        if self.trade_direction == "long":
            return self._analyze_long(
                market_snapshot=market_snapshot,
                account_snapshot=account_snapshot,
                features=features,
                inference=inference,
                bull_probability=bull_probability,
                bear_probability=bear_probability,
                stress_probability=stress_probability,
                bull_continuation=bull_continuation,
                position_side=position_side,
                momentum_3=momentum_3,
                momentum_5=momentum_5,
            )
        return self._analyze_short(
            market_snapshot=market_snapshot,
            account_snapshot=account_snapshot,
            features=features,
            inference=inference,
            bull_probability=bull_probability,
            bear_probability=bear_probability,
            stress_probability=stress_probability,
            bear_continuation=bear_continuation,
            position_side=position_side,
            momentum_3=momentum_3,
            momentum_5=momentum_5,
        )

    def _analyze_long(
        self,
        *,
        market_snapshot: MarketSnapshot,
        account_snapshot: AccountSnapshot,
        features: dict[str, float],
        inference: RegimeInference,
        bull_probability: float,
        bear_probability: float,
        stress_probability: float,
        bull_continuation: float,
        position_side: str,
        momentum_3: float,
        momentum_5: float,
    ) -> TradeDecision:
        if position_side == "short":
            return TradeDecision(
                action="exit",
                confidence=0.9,
                rationale="V3 is long-only and will close any residual short exposure.",
                regime=inference.regime,
                regime_probability=inference.regime_probability,
                regime_probabilities=inference.regime_probabilities,
                continuation_probabilities=inference.continuation_probabilities,
            )
        if position_side == "long":
            if stress_probability >= self.hmm_stress_exit_probability:
                return TradeDecision(
                    action="exit",
                    confidence=min(0.95, max(0.6, stress_probability)),
                    rationale="Long exit fired because the HMM shifted into a stress regime.",
                    regime=inference.regime,
                    regime_probability=inference.regime_probability,
                    regime_probabilities=inference.regime_probabilities,
                    continuation_probabilities=inference.continuation_probabilities,
                )
            if bear_probability >= self.hmm_bear_exit_probability:
                return TradeDecision(
                    action="exit",
                    confidence=min(0.95, max(0.6, bear_probability)),
                    rationale="Long exit fired because the HMM bearish regime probability crossed the configured threshold.",
                    regime=inference.regime,
                    regime_probability=inference.regime_probability,
                    regime_probabilities=inference.regime_probabilities,
                    continuation_probabilities=inference.continuation_probabilities,
                )
            if bull_continuation < (self.hmm_bull_continuation_probability * 0.85) and (
                momentum_3 <= self.exit_momentum_3_bps or momentum_5 <= self.exit_momentum_5_bps
            ):
                return TradeDecision(
                    action="exit",
                    confidence=min(0.9, max(0.6, 1.0 - bull_continuation)),
                    rationale="Long exit fired because bull-regime continuation weakened and short-term momentum rolled over.",
                    regime=inference.regime,
                    regime_probability=inference.regime_probability,
                    regime_probabilities=inference.regime_probabilities,
                    continuation_probabilities=inference.continuation_probabilities,
                )
            return TradeDecision(
                action="do_nothing",
                confidence=max(0.25, bull_probability),
                rationale="Long position remains aligned with the active HMM regime.",
                regime=inference.regime,
                regime_probability=inference.regime_probability,
                regime_probabilities=inference.regime_probabilities,
                continuation_probabilities=inference.continuation_probabilities,
                entry_blockers=["already_in_long_position"],
            )

        trade_plan = self._build_hmm_trade_plan(inference)
        expected_slippage_bps = self._expected_slippage_bps(features)
        probability = min(0.95, max(0.0, (bull_probability * 0.6) + (bull_continuation * 0.4)))
        expected_edge_bps = self._net_expected_edge_bps(
            probability,
            trade_plan,
            self._round_trip_cost_bps(expected_slippage_bps),
        )

        confirmations: list[str] = []
        blockers: list[str] = []

        if inference.regime == "bull_trend":
            confirmations.append("bull_regime")
        else:
            blockers.append("active_regime_not_bull")
        if bull_probability >= self.hmm_bull_entry_probability:
            confirmations.append("bull_regime_probability")
        else:
            blockers.append("bull_regime_probability_below_threshold")
        if bull_continuation >= self.hmm_bull_continuation_probability:
            confirmations.append("bull_continuation_probability")
        else:
            blockers.append("bull_continuation_probability_below_threshold")
        if inference.htf_bullish:
            confirmations.append("htf_ema_alignment")
        else:
            blockers.append("higher_timeframe_ema_alignment_not_bullish")
        if self._atr_is_tradeable(inference):
            confirmations.append("atr_band")
        else:
            blockers.append("atr_percentile_outside_tradeable_band")
        if momentum_3 >= self.entry_momentum_3_bps and momentum_5 >= self.entry_momentum_5_bps:
            confirmations.append("minute_momentum")
        else:
            blockers.append("minute_momentum_not_confirmed")
        if features.get("trend_strength_bps", 0.0) >= self.min_trend_strength_bps:
            confirmations.append("trend_quality")
        else:
            blockers.append("trend_strength_below_minimum")
        if (
            features.get("volume_ratio_5_30", 0.0) >= self.min_volume_ratio_5_30
            and features.get("breakout_up_20_bps", 0.0) >= self.breakout_buffer_bps
        ):
            confirmations.append("breakout_plus_participation")
        else:
            blockers.append("breakout_or_volume_confirmation_missing")
        if features.get("zscore_30", 0.0) <= self.max_abs_zscore_30:
            confirmations.append("mean_reversion_guard")
        else:
            blockers.append("price_extended_too_far_from_mean")

        score = self._entry_score(confirmations)
        if (
            bull_probability >= self.hmm_bull_entry_probability
            and bull_continuation >= self.hmm_bull_continuation_probability
            and len(confirmations) >= self.min_confirmation_count
            and score >= self.min_entry_score
            and expected_edge_bps >= self.min_expected_edge_bps
        ):
            execution_plan = self._build_execution_plan(
                market_snapshot=market_snapshot,
                account_snapshot=account_snapshot,
                trade_plan=trade_plan,
                expected_slippage_bps=expected_slippage_bps,
                direction="buy",
            )
            return TradeDecision(
                action="buy",
                confidence=self._entry_confidence(bull_probability, expected_edge_bps, score, len(confirmations)),
                rationale="Long entry approved by the HMM bull regime, continuation probability, and 1-minute execution confirmation.",
                regime=inference.regime,
                regime_probability=inference.regime_probability,
                regime_probabilities=inference.regime_probabilities,
                continuation_probabilities=inference.continuation_probabilities,
                expected_edge_bps=expected_edge_bps,
                signal_quality_score=score,
                confirmation_count=len(confirmations),
                trade_plan=trade_plan,
                execution_plan=execution_plan,
                planned_risk_usd=execution_plan.planned_risk_usd,
            )

        return self._blocked_decision(
            blockers=blockers + (["cost_adjusted_edge_below_minimum"] if expected_edge_bps < self.min_expected_edge_bps else []),
            confidence=max(0.2, bull_probability * 0.5),
            regime=inference.regime,
            regime_probability=inference.regime_probability,
            regime_probabilities=inference.regime_probabilities,
            continuation_probabilities=inference.continuation_probabilities,
            expected_edge_bps=expected_edge_bps,
            signal_quality_score=score,
            confirmation_count=len(confirmations),
        )

    def _analyze_short(
        self,
        *,
        market_snapshot: MarketSnapshot,
        account_snapshot: AccountSnapshot,
        features: dict[str, float],
        inference: RegimeInference,
        bull_probability: float,
        bear_probability: float,
        stress_probability: float,
        bear_continuation: float,
        position_side: str,
        momentum_3: float,
        momentum_5: float,
    ) -> TradeDecision:
        if position_side == "long":
            return TradeDecision(
                action="exit",
                confidence=0.9,
                rationale="V3.2 is inverse research logic and will close any residual long exposure.",
                regime=inference.regime,
                regime_probability=inference.regime_probability,
                regime_probabilities=inference.regime_probabilities,
                continuation_probabilities=inference.continuation_probabilities,
            )

        if position_side == "short":
            if stress_probability >= self.hmm_stress_exit_probability:
                return TradeDecision(
                    action="exit",
                    confidence=min(0.95, max(0.6, stress_probability)),
                    rationale="Short exit fired because the HMM shifted into a stress regime.",
                    regime=inference.regime,
                    regime_probability=inference.regime_probability,
                    regime_probabilities=inference.regime_probabilities,
                    continuation_probabilities=inference.continuation_probabilities,
                )
            if bull_probability >= self.hmm_bull_exit_probability:
                return TradeDecision(
                    action="exit",
                    confidence=min(0.95, max(0.6, bull_probability)),
                    rationale="Short exit fired because the HMM bullish regime probability crossed the configured threshold.",
                    regime=inference.regime,
                    regime_probability=inference.regime_probability,
                    regime_probabilities=inference.regime_probabilities,
                    continuation_probabilities=inference.continuation_probabilities,
                )
            if bear_continuation < (self.hmm_bear_continuation_probability * 0.85) and (
                momentum_3 >= abs(self.exit_momentum_3_bps) or momentum_5 >= abs(self.exit_momentum_5_bps)
            ):
                return TradeDecision(
                    action="exit",
                    confidence=min(0.9, max(0.6, 1.0 - bear_continuation)),
                    rationale="Short exit fired because bear-regime continuation weakened and short-term momentum turned higher.",
                    regime=inference.regime,
                    regime_probability=inference.regime_probability,
                    regime_probabilities=inference.regime_probabilities,
                    continuation_probabilities=inference.continuation_probabilities,
                )
            return TradeDecision(
                action="do_nothing",
                confidence=max(0.25, bear_probability),
                rationale="Short position remains aligned with the active inverse HMM regime.",
                regime=inference.regime,
                regime_probability=inference.regime_probability,
                regime_probabilities=inference.regime_probabilities,
                continuation_probabilities=inference.continuation_probabilities,
                entry_blockers=["already_in_short_position"],
            )

        trade_plan = self._build_hmm_trade_plan(inference)
        expected_slippage_bps = self._expected_slippage_bps(features)
        probability = min(0.95, max(0.0, (bear_probability * 0.6) + (bear_continuation * 0.4)))
        expected_edge_bps = self._net_expected_edge_bps(
            probability,
            trade_plan,
            self._round_trip_cost_bps(expected_slippage_bps),
        )

        confirmations: list[str] = []
        blockers: list[str] = []

        if inference.regime == "bear_trend":
            confirmations.append("bear_regime")
        else:
            blockers.append("active_regime_not_bear")
        if bear_probability >= self.hmm_bear_entry_probability:
            confirmations.append("bear_regime_probability")
        else:
            blockers.append("bear_regime_probability_below_threshold")
        if bear_continuation >= self.hmm_bear_continuation_probability:
            confirmations.append("bear_continuation_probability")
        else:
            blockers.append("bear_continuation_probability_below_threshold")
        if inference.htf_bearish:
            confirmations.append("htf_ema_alignment")
        else:
            blockers.append("higher_timeframe_ema_alignment_not_bearish")
        if self._atr_is_tradeable(inference):
            confirmations.append("atr_band")
        else:
            blockers.append("atr_percentile_outside_tradeable_band")
        if momentum_3 <= -self.entry_momentum_3_bps and momentum_5 <= -self.entry_momentum_5_bps:
            confirmations.append("minute_momentum")
        else:
            blockers.append("minute_momentum_not_confirmed")
        if features.get("trend_strength_bps", 0.0) >= self.min_trend_strength_bps:
            confirmations.append("trend_quality")
        else:
            blockers.append("trend_strength_below_minimum")
        if (
            features.get("volume_ratio_5_30", 0.0) >= self.min_volume_ratio_5_30
            and features.get("breakdown_20_bps", 0.0) >= self.breakout_buffer_bps
        ):
            confirmations.append("breakdown_plus_participation")
        else:
            blockers.append("breakdown_or_volume_confirmation_missing")
        if features.get("zscore_30", 0.0) >= -self.max_abs_zscore_30:
            confirmations.append("mean_reversion_guard")
        else:
            blockers.append("price_extended_too_far_below_mean")

        score = self._entry_score(confirmations)
        if (
            bear_probability >= self.hmm_bear_entry_probability
            and bear_continuation >= self.hmm_bear_continuation_probability
            and len(confirmations) >= self.min_confirmation_count
            and score >= self.min_entry_score
            and expected_edge_bps >= self.min_expected_edge_bps
        ):
            execution_plan = self._build_execution_plan(
                market_snapshot=market_snapshot,
                account_snapshot=account_snapshot,
                trade_plan=trade_plan,
                expected_slippage_bps=expected_slippage_bps,
                direction="sell",
            )
            return TradeDecision(
                action="sell",
                confidence=self._entry_confidence(bear_probability, expected_edge_bps, score, len(confirmations)),
                rationale="Short entry approved by the inverse HMM bear regime, continuation probability, and 1-minute execution confirmation.",
                regime=inference.regime,
                regime_probability=inference.regime_probability,
                regime_probabilities=inference.regime_probabilities,
                continuation_probabilities=inference.continuation_probabilities,
                expected_edge_bps=expected_edge_bps,
                signal_quality_score=score,
                confirmation_count=len(confirmations),
                trade_plan=trade_plan,
                execution_plan=execution_plan,
                planned_risk_usd=execution_plan.planned_risk_usd,
            )

        return self._blocked_decision(
            blockers=blockers + (["cost_adjusted_edge_below_minimum"] if expected_edge_bps < self.min_expected_edge_bps else []),
            confidence=max(0.2, bear_probability * 0.5),
            regime=inference.regime,
            regime_probability=inference.regime_probability,
            regime_probabilities=inference.regime_probabilities,
            continuation_probabilities=inference.continuation_probabilities,
            expected_edge_bps=expected_edge_bps,
            signal_quality_score=score,
            confirmation_count=len(confirmations),
        )

    def _build_hmm_trade_plan(self, inference: RegimeInference) -> TradePlan:
        atr_basis = inference.atr_14_bps or self.min_stop_loss_bps
        stop_loss_bps = self._clamp(
            atr_basis * self.stop_loss_vol_multiplier,
            self.min_stop_loss_bps,
            self.max_stop_loss_bps,
        )
        return TradePlan(
            stop_loss_bps=stop_loss_bps,
            take_profit_bps=stop_loss_bps * self.take_profit_multiple,
            max_take_profit_bps=stop_loss_bps * self.max_reward_multiple,
            trailing_stop_bps=stop_loss_bps * self.trailing_stop_multiple,
            time_stop_bars=self.time_stop_bars,
            partial_take_profit_fraction=self.partial_take_profit_fraction,
        )

    def _atr_is_tradeable(self, inference: RegimeInference) -> bool:
        if inference.atr_percentile is None:
            return False
        return self.min_atr_percentile_30 <= inference.atr_percentile <= self.max_atr_percentile_30
