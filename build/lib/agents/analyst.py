from math import exp
from decimal import Decimal

from data.schemas import AccountSnapshot, ExecutionPlan, MarketSnapshot, TradeDecision, TradePlan


class AnalystAgent:
    """Regime-aware advisory layer for research-phase trading decisions."""

    def __init__(
        self,
        *,
        policy_name: str = "baseline@v2.2",
        min_sample_count: int = 45,
        max_spread_bps: float = 20.0,
        min_regime_probability: float = 0.58,
        regime_trend_15_bps: float = 14.0,
        regime_trend_30_bps: float = 28.0,
        htf_trend_60_bps: float = 18.0,
        htf_trend_240_bps: float = 42.0,
        entry_momentum_3_bps: float = 6.0,
        entry_momentum_5_bps: float = 10.0,
        exit_momentum_3_bps: float = -4.0,
        exit_momentum_5_bps: float = -8.0,
        max_volatility_5_bps: float = 24.0,
        chaos_volatility_5_bps: float = 40.0,
        max_abs_zscore_30: float = 2.2,
        min_trend_strength_bps: float = 16.0,
        min_volume_ratio_5_30: float = 1.1,
        min_entry_score: int = 5,
        min_confirmation_count: int = 3,
        breakout_buffer_bps: float = 0.75,
        min_atr_percentile_30: float = 0.15,
        max_atr_percentile_30: float = 0.88,
        exit_regime_probability: float = 0.72,
        hard_exit_momentum_3_bps: float = 8.0,
        hard_exit_momentum_5_bps: float = 14.0,
        min_stop_loss_bps: float = 12.0,
        max_stop_loss_bps: float = 36.0,
        stop_loss_vol_multiplier: float = 1.35,
        trailing_stop_multiple: float = 0.75,
        partial_take_profit_fraction: float = 0.5,
        take_profit_multiple: float = 1.0,
        max_reward_multiple: float = 2.0,
        time_stop_bars: int = 12,
        min_expected_edge_bps: float = 0.5,
        allow_short_entries: bool = False,
        estimated_fee_bps: float = 1.5,
        base_slippage_bps: float = 1.0,
        max_expected_slippage_bps: float = 6.0,
        requested_risk_fraction: float = 0.0025,
        max_requested_notional_fraction_cash: float = 0.12,
    ) -> None:
        self.policy_name = policy_name
        self.min_sample_count = min_sample_count
        self.max_spread_bps = max_spread_bps
        self.min_regime_probability = min_regime_probability
        self.regime_trend_15_bps = regime_trend_15_bps
        self.regime_trend_30_bps = regime_trend_30_bps
        self.htf_trend_60_bps = htf_trend_60_bps
        self.htf_trend_240_bps = htf_trend_240_bps
        self.entry_momentum_3_bps = entry_momentum_3_bps
        self.entry_momentum_5_bps = entry_momentum_5_bps
        self.exit_momentum_3_bps = exit_momentum_3_bps
        self.exit_momentum_5_bps = exit_momentum_5_bps
        self.max_volatility_5_bps = max_volatility_5_bps
        self.chaos_volatility_5_bps = chaos_volatility_5_bps
        self.max_abs_zscore_30 = max_abs_zscore_30
        self.min_trend_strength_bps = min_trend_strength_bps
        self.min_volume_ratio_5_30 = min_volume_ratio_5_30
        self.min_entry_score = min_entry_score
        self.min_confirmation_count = min_confirmation_count
        self.breakout_buffer_bps = breakout_buffer_bps
        self.min_atr_percentile_30 = min_atr_percentile_30
        self.max_atr_percentile_30 = max_atr_percentile_30
        self.exit_regime_probability = exit_regime_probability
        self.hard_exit_momentum_3_bps = hard_exit_momentum_3_bps
        self.hard_exit_momentum_5_bps = hard_exit_momentum_5_bps
        self.min_stop_loss_bps = min_stop_loss_bps
        self.max_stop_loss_bps = max_stop_loss_bps
        self.stop_loss_vol_multiplier = stop_loss_vol_multiplier
        self.trailing_stop_multiple = trailing_stop_multiple
        self.partial_take_profit_fraction = partial_take_profit_fraction
        self.take_profit_multiple = take_profit_multiple
        self.max_reward_multiple = max_reward_multiple
        self.time_stop_bars = time_stop_bars
        self.min_expected_edge_bps = min_expected_edge_bps
        self.allow_short_entries = allow_short_entries
        self.estimated_fee_bps = estimated_fee_bps
        self.base_slippage_bps = base_slippage_bps
        self.max_expected_slippage_bps = max_expected_slippage_bps
        self.requested_risk_fraction = requested_risk_fraction
        self.max_requested_notional_fraction_cash = max_requested_notional_fraction_cash

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

        blockers: list[str] = []
        sample_count = int(features.get("sample_count", 0))
        if sample_count < self.min_sample_count:
            blockers.append("insufficient_history_for_htf_and_atr")

        spread_bps = features.get("spread_bps", 0.0)
        if spread_bps > self.max_spread_bps:
            blockers.append("spread_above_limit")

        if blockers:
            return self._blocked_decision(
                blockers=blockers,
                confidence=0.0,
                regime=None,
                regime_probability=0.0,
            )

        regime, regime_probability, long_probability, short_probability = self._classify_regime(features)
        momentum_3 = features.get("return_3_bps", 0.0)
        momentum_5 = features.get("return_5_bps", 0.0)
        position_side = self._position_side(account_snapshot.open_position_qty)

        if position_side == "long":
            if regime == "chaotic" and regime_probability >= self.exit_regime_probability:
                return TradeDecision(
                    action="exit",
                    confidence=min(0.95, max(0.6, short_probability)),
                    rationale="Long position exit fired because volatility shifted into a chaotic regime.",
                    regime=regime,
                    regime_probability=regime_probability,
                )
            if (
                regime == "downtrend"
                and regime_probability >= self.exit_regime_probability
                and momentum_3 <= -self.hard_exit_momentum_3_bps
                and momentum_5 <= -self.hard_exit_momentum_5_bps
            ):
                return TradeDecision(
                    action="exit",
                    confidence=min(0.95, max(0.6, short_probability)),
                    rationale="Long position exit fired because higher-timeframe alignment and short-term momentum both broke down.",
                    regime=regime,
                    regime_probability=regime_probability,
                )
            return TradeDecision(
                action="do_nothing",
                confidence=max(0.25, regime_probability),
                rationale="Long position remains aligned with the current regime.",
                regime=regime,
                regime_probability=regime_probability,
                entry_blockers=["already_in_long_position"],
            )

        if position_side == "short":
            if regime == "chaotic" and regime_probability >= self.exit_regime_probability:
                return TradeDecision(
                    action="exit",
                    confidence=min(0.95, max(0.6, long_probability)),
                    rationale="Short position exit fired because volatility shifted into a chaotic regime.",
                    regime=regime,
                    regime_probability=regime_probability,
                )
            if (
                regime == "uptrend"
                and regime_probability >= self.exit_regime_probability
                and momentum_3 >= self.hard_exit_momentum_3_bps
                and momentum_5 >= self.hard_exit_momentum_5_bps
            ):
                return TradeDecision(
                    action="exit",
                    confidence=min(0.95, max(0.6, long_probability)),
                    rationale="Short position exit fired because higher-timeframe alignment and short-term momentum both turned higher.",
                    regime=regime,
                    regime_probability=regime_probability,
                )
            return TradeDecision(
                action="do_nothing",
                confidence=max(0.25, regime_probability),
                rationale="Short position remains aligned with the current regime.",
                regime=regime,
                regime_probability=regime_probability,
                entry_blockers=["already_in_short_position"],
            )

        if regime == "chaotic":
            return self._blocked_decision(
                blockers=["chaotic_volatility_regime"],
                confidence=regime_probability,
                regime=regime,
                regime_probability=regime_probability,
            )

        trade_plan = self._build_trade_plan(features)
        expected_slippage_bps = self._expected_slippage_bps(features)
        round_trip_cost_bps = self._round_trip_cost_bps(expected_slippage_bps)
        long_bias, short_bias = self._higher_timeframe_bias(features)
        volatility_blockers = self._volatility_blockers(features)

        long_confirmations, long_failures = self._signal_checks(
            direction="long",
            regime=regime,
            regime_probability=regime_probability,
            momentum_3=momentum_3,
            momentum_5=momentum_5,
            zscore_30=features.get("zscore_30", 0.0),
            trend_strength_bps=abs(features.get("trend_strength_bps", 0.0)),
            volume_ratio=features.get("volume_ratio_5_30", 1.0),
            breakout_bps=features.get("breakout_up_20_bps", 0.0),
            bias=long_bias,
        )
        short_confirmations, short_failures = self._signal_checks(
            direction="short",
            regime=regime,
            regime_probability=regime_probability,
            momentum_3=momentum_3,
            momentum_5=momentum_5,
            zscore_30=features.get("zscore_30", 0.0),
            trend_strength_bps=abs(features.get("trend_strength_bps", 0.0)),
            volume_ratio=features.get("volume_ratio_5_30", 1.0),
            breakout_bps=features.get("breakdown_20_bps", 0.0),
            bias=short_bias,
        )
        long_score = self._entry_score(long_confirmations)
        short_score = self._entry_score(short_confirmations)
        long_edge = self._net_expected_edge_bps(long_probability, trade_plan, round_trip_cost_bps)
        short_edge = self._net_expected_edge_bps(short_probability, trade_plan, round_trip_cost_bps)

        if long_edge > 0 and (regime == "uptrend" or long_score >= short_score):
            direction = "long"
            probability = long_probability
            confirmations = long_confirmations
            failures = volatility_blockers + long_failures
            score = long_score
            edge = long_edge
        else:
            direction = "short"
            probability = short_probability
            confirmations = short_confirmations
            failures = volatility_blockers + short_failures
            score = short_score
            edge = short_edge

        if (
            direction == "long"
            and edge >= self.min_expected_edge_bps
            and len(confirmations) >= self.min_confirmation_count
            and score >= self.min_entry_score
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
                confidence=self._entry_confidence(regime_probability, edge, score, len(confirmations)),
                rationale="Long entry approved by higher-timeframe alignment, multi-signal confirmation, and cost-adjusted positive edge.",
                regime=regime,
                regime_probability=regime_probability,
                expected_edge_bps=edge,
                signal_quality_score=score,
                confirmation_count=len(confirmations),
                trade_plan=trade_plan,
                execution_plan=execution_plan,
                planned_risk_usd=execution_plan.planned_risk_usd,
            )

        if (
            direction == "short"
            and self.allow_short_entries
            and edge >= self.min_expected_edge_bps
            and len(confirmations) >= self.min_confirmation_count
            and score >= self.min_entry_score
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
                confidence=self._entry_confidence(regime_probability, edge, score, len(confirmations)),
                rationale="Short entry approved by higher-timeframe alignment, multi-signal confirmation, and cost-adjusted positive edge.",
                regime=regime,
                regime_probability=regime_probability,
                expected_edge_bps=edge,
                signal_quality_score=score,
                confirmation_count=len(confirmations),
                trade_plan=trade_plan,
                execution_plan=execution_plan,
                planned_risk_usd=execution_plan.planned_risk_usd,
            )

        blockers = failures
        if len(confirmations) < self.min_confirmation_count:
            blockers.append("confirmation_count_below_minimum")
        if score < self.min_entry_score:
            blockers.append("signal_quality_below_minimum")
        if edge < self.min_expected_edge_bps:
            blockers.append("cost_adjusted_edge_below_minimum")
        if direction == "short" and not self.allow_short_entries:
            blockers.append("short_entries_disabled_for_spot_execution")
        if regime == "range":
            blockers.append("range_regime_without_directional_edge")

        return self._blocked_decision(
            blockers=blockers,
            confidence=max(0.2, regime_probability * 0.5),
            regime=regime,
            regime_probability=regime_probability,
            expected_edge_bps=max(long_edge, short_edge),
            signal_quality_score=max(long_score, short_score),
            confirmation_count=max(len(long_confirmations), len(short_confirmations)),
        )

    def _classify_regime(self, features: dict[str, float]) -> tuple[str, float, float, float]:
        trend_15 = features.get("return_15_bps", 0.0)
        trend_30 = features.get("return_30_bps", 0.0)
        volatility_5 = features.get("volatility_5_bps", 0.0)
        volatility_ratio = features.get("volatility_ratio_5_30", 1.0)
        zscore_30 = features.get("zscore_30", 0.0)

        trend_score = (
            (trend_15 / max(self.regime_trend_15_bps, 1.0)) * 0.45
            + (trend_30 / max(self.regime_trend_30_bps, 1.0)) * 0.55
        )
        volatility_penalty = max(0.0, (volatility_5 / max(self.max_volatility_5_bps, 1.0)) - 1.0)
        zscore_penalty = min(abs(zscore_30) / max(self.max_abs_zscore_30, 1.0), 1.5)

        long_probability = self._sigmoid((trend_score * 2.1) - (volatility_penalty * 1.1) - (zscore_penalty * 0.35))
        short_probability = self._sigmoid((-trend_score * 2.1) - (volatility_penalty * 1.1) - (zscore_penalty * 0.35))
        chaos_signal = (
            (volatility_5 - self.chaos_volatility_5_bps) / max(self.chaos_volatility_5_bps * 0.2, 1.0)
        ) + max(0.0, volatility_ratio - 1.4)
        chaos_probability = self._sigmoid(chaos_signal)

        if chaos_probability >= 0.65:
            return "chaotic", chaos_probability, long_probability, short_probability
        if max(long_probability, short_probability) < self.min_regime_probability:
            return "range", 1.0 - max(long_probability, short_probability), long_probability, short_probability
        if long_probability >= short_probability:
            return "uptrend", long_probability, long_probability, short_probability
        return "downtrend", short_probability, long_probability, short_probability

    def _higher_timeframe_bias(self, features: dict[str, float]) -> tuple[bool, bool]:
        bullish_votes = 0
        bearish_votes = 0
        if features.get("return_30_bps", 0.0) >= self.regime_trend_30_bps:
            bullish_votes += 1
        if features.get("return_30_bps", 0.0) <= -self.regime_trend_30_bps:
            bearish_votes += 1
        if features.get("return_60_bps", 0.0) >= self.htf_trend_60_bps:
            bullish_votes += 1
        if features.get("return_60_bps", 0.0) <= -self.htf_trend_60_bps:
            bearish_votes += 1
        if features.get("return_240_bps", 0.0) >= self.htf_trend_240_bps:
            bullish_votes += 1
        if features.get("return_240_bps", 0.0) <= -self.htf_trend_240_bps:
            bearish_votes += 1
        if features.get("ema_slope_20_bps", 0.0) > 0:
            bullish_votes += 1
        if features.get("ema_slope_20_bps", 0.0) < 0:
            bearish_votes += 1
        if features.get("ema_slope_60_bps", 0.0) > 0 and features.get("ema_slope_240_bps", 0.0) >= 0:
            bullish_votes += 1
        if features.get("ema_slope_60_bps", 0.0) < 0 and features.get("ema_slope_240_bps", 0.0) <= 0:
            bearish_votes += 1
        if features.get("ema_gap_60_240_bps", 0.0) > 0:
            bullish_votes += 1
        if features.get("ema_gap_60_240_bps", 0.0) < 0:
            bearish_votes += 1
        return bullish_votes >= 2 and bullish_votes > bearish_votes, bearish_votes >= 2 and bearish_votes > bullish_votes

    def _volatility_blockers(self, features: dict[str, float]) -> list[str]:
        blockers: list[str] = []
        atr_percentile = features.get("atr_30_percentile")
        if atr_percentile is not None:
            if atr_percentile < self.min_atr_percentile_30:
                blockers.append("atr_percentile_too_low")
            if atr_percentile > self.max_atr_percentile_30:
                blockers.append("atr_percentile_too_high")
        atr_30 = features.get("atr_30_bps", 0.0)
        if atr_30 and atr_30 > self.chaos_volatility_5_bps:
            blockers.append("atr_regime_matches_chaos")
        return blockers

    def _signal_checks(
        self,
        *,
        direction: str,
        regime: str,
        regime_probability: float,
        momentum_3: float,
        momentum_5: float,
        zscore_30: float,
        trend_strength_bps: float,
        volume_ratio: float,
        breakout_bps: float,
        bias: bool,
    ) -> tuple[list[str], list[str]]:
        confirmations: list[str] = []
        failures: list[str] = []

        desired_regime = "uptrend" if direction == "long" else "downtrend"
        if regime == desired_regime and regime_probability >= self.min_regime_probability:
            confirmations.append("regime")
        else:
            failures.append("regime_probability_or_direction_mismatch")

        if bias:
            confirmations.append("higher_timeframe_alignment")
        else:
            failures.append("higher_timeframe_bias_mismatch")

        if direction == "long":
            momentum_ok = momentum_3 >= self.entry_momentum_3_bps and momentum_5 >= self.entry_momentum_5_bps
            zscore_ok = zscore_30 <= self.max_abs_zscore_30
        else:
            momentum_ok = momentum_3 <= -self.entry_momentum_3_bps and momentum_5 <= -self.entry_momentum_5_bps
            zscore_ok = zscore_30 >= -self.max_abs_zscore_30

        if momentum_ok:
            confirmations.append("momentum")
        else:
            failures.append("momentum_not_confirmed")

        if trend_strength_bps >= self.min_trend_strength_bps and zscore_ok:
            confirmations.append("trend_quality")
        else:
            failures.append("trend_strength_or_mean_reversion_limit")

        if volume_ratio >= self.min_volume_ratio_5_30 and breakout_bps >= self.breakout_buffer_bps:
            confirmations.append("participation_breakout")
        else:
            failures.append("breakout_or_volume_confirmation_missing")

        return confirmations, failures

    def _build_trade_plan(self, features: dict[str, float]) -> TradePlan:
        volatility_basis = max(
            features.get("atr_14_bps", 0.0),
            features.get("atr_30_bps", 0.0),
            features.get("volatility_15_bps", 0.0),
            features.get("volatility_5_bps", 0.0),
            self.min_stop_loss_bps / max(self.stop_loss_vol_multiplier, 1.0),
        )
        stop_loss_bps = self._clamp(
            volatility_basis * self.stop_loss_vol_multiplier,
            self.min_stop_loss_bps,
            self.max_stop_loss_bps,
        )
        partial_fraction = self._clamp(self.partial_take_profit_fraction, 0.25, 0.5)
        return TradePlan(
            stop_loss_bps=stop_loss_bps,
            take_profit_bps=stop_loss_bps * self.take_profit_multiple,
            max_take_profit_bps=stop_loss_bps * self.max_reward_multiple,
            trailing_stop_bps=max(stop_loss_bps * self.trailing_stop_multiple, self.min_stop_loss_bps * 0.7),
            time_stop_bars=self.time_stop_bars,
            partial_take_profit_fraction=partial_fraction,
        )

    def _build_execution_plan(
        self,
        *,
        market_snapshot: MarketSnapshot,
        account_snapshot: AccountSnapshot,
        trade_plan: TradePlan,
        expected_slippage_bps: float,
        direction: str,
    ) -> ExecutionPlan:
        reference_price = float(
            market_snapshot.last_trade_price
            or market_snapshot.ask_price
            or market_snapshot.bid_price
            or Decimal("0")
        )
        stop_price = self._price_from_bps(reference_price, trade_plan.stop_loss_bps, side=direction, favorable=False)
        take_profit_price = self._price_from_bps(reference_price, trade_plan.take_profit_bps, side=direction, favorable=True)
        max_take_profit_price = self._price_from_bps(
            reference_price,
            trade_plan.max_take_profit_bps,
            side=direction,
            favorable=True,
        )
        requested_notional = self._requested_notional_usd(account_snapshot, trade_plan)
        planned_risk_usd = None
        if requested_notional is not None:
            planned_risk_usd = requested_notional * (trade_plan.stop_loss_bps / 10000.0)
        return ExecutionPlan(
            requested_notional_usd=requested_notional,
            order_type="market",
            time_in_force="gtc",
            entry_reference_price=reference_price if reference_price > 0 else None,
            stop_price=stop_price if stop_price > 0 else None,
            take_profit_price=take_profit_price if take_profit_price > 0 else None,
            max_take_profit_price=max_take_profit_price if max_take_profit_price > 0 else None,
            expected_slippage_bps=expected_slippage_bps,
            planned_risk_usd=planned_risk_usd,
        )

    def _requested_notional_usd(self, account_snapshot: AccountSnapshot, trade_plan: TradePlan) -> float | None:
        equity = float(account_snapshot.equity or account_snapshot.cash or Decimal("0"))
        cash = float(account_snapshot.cash or Decimal("0"))
        stop_fraction = trade_plan.stop_loss_bps / 10000.0
        if equity <= 0 or cash <= 0 or stop_fraction <= 0:
            return None
        risk_budget_usd = equity * self.requested_risk_fraction
        risk_sized_notional = risk_budget_usd / stop_fraction
        cash_fraction_cap = cash * self.max_requested_notional_fraction_cash
        requested = min(risk_sized_notional, cash_fraction_cap)
        return requested if requested > 0 else None

    def _expected_slippage_bps(self, features: dict[str, float]) -> float:
        atr_component = features.get("atr_14_bps", features.get("volatility_15_bps", 0.0)) * 0.08
        spread_component = max(features.get("spread_bps", self.base_slippage_bps) * 0.2, 0.0)
        volume_component = max(0.0, 1.1 - features.get("volume_ratio_5_30", 1.0)) * 2.5
        slippage = self.base_slippage_bps + atr_component + spread_component + volume_component
        return self._clamp(slippage, self.base_slippage_bps, self.max_expected_slippage_bps)

    def _round_trip_cost_bps(self, expected_slippage_bps: float) -> float:
        per_side_cost = self.estimated_fee_bps + expected_slippage_bps
        return per_side_cost * 2.0

    def _net_expected_edge_bps(self, probability: float, trade_plan: TradePlan, round_trip_cost_bps: float) -> float:
        raw_edge = self._expected_edge_bps(probability, trade_plan)
        return raw_edge - round_trip_cost_bps

    def _expected_edge_bps(self, probability: float, trade_plan: TradePlan) -> float:
        average_reward_bps = (
            trade_plan.take_profit_bps * trade_plan.partial_take_profit_fraction
            + trade_plan.max_take_profit_bps * (1.0 - trade_plan.partial_take_profit_fraction)
        )
        return (probability * average_reward_bps) - ((1.0 - probability) * trade_plan.stop_loss_bps)

    def _entry_confidence(
        self,
        regime_probability: float,
        expected_edge_bps: float,
        entry_score: int,
        confirmation_count: int,
    ) -> float:
        edge_bonus = min(max(expected_edge_bps, 0.0) / 35.0, 0.2)
        score_bonus = min(max(entry_score - self.min_entry_score, 0) * 0.025, 0.08)
        confirmation_bonus = min(max(confirmation_count - self.min_confirmation_count, 0) * 0.03, 0.09)
        return min(0.95, max(0.6, regime_probability + edge_bonus + score_bonus + confirmation_bonus))

    def _entry_score(self, confirmations: list[str]) -> int:
        return len(confirmations)

    def _blocked_decision(
        self,
        *,
        blockers: list[str],
        confidence: float,
        regime: str | None,
        regime_probability: float,
        regime_probabilities: dict[str, float] | None = None,
        continuation_probabilities: dict[str, float] | None = None,
        expected_edge_bps: float | None = None,
        signal_quality_score: int = 0,
        confirmation_count: int = 0,
        planned_risk_usd: float | None = None,
    ) -> TradeDecision:
        deduped = self._dedupe(blockers)
        summary = "; ".join(deduped[:4]) if deduped else "no_entry_conditions_met"
        return TradeDecision(
            action="do_nothing",
            confidence=confidence,
            rationale=f"Entry blocked: {summary}.",
            regime=regime,
            regime_probability=regime_probability,
            regime_probabilities=regime_probabilities or {},
            continuation_probabilities=continuation_probabilities or {},
            expected_edge_bps=expected_edge_bps,
            signal_quality_score=signal_quality_score,
            confirmation_count=confirmation_count,
            entry_blockers=deduped,
            planned_risk_usd=planned_risk_usd,
        )

    def _position_side(self, open_position_qty: Decimal) -> str:
        if open_position_qty > Decimal("0"):
            return "long"
        if open_position_qty < Decimal("0"):
            return "short"
        return "flat"

    def _price_from_bps(self, reference_price: float, bps: float, *, side: str, favorable: bool) -> float:
        if reference_price <= 0:
            return 0.0
        direction = 1 if side == "buy" else -1
        multiplier = 1 if favorable else -1
        return reference_price * (1.0 + ((bps * direction * multiplier) / 10000.0))

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                deduped.append(value)
        return deduped

    def _sigmoid(self, value: float) -> float:
        if value >= 0:
            exponent = exp(-value)
            return 1.0 / (1.0 + exponent)
        exponent = exp(value)
        return exponent / (1.0 + exponent)

    def _clamp(self, value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))
