from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from math import ceil
from typing import Literal

import numpy as np
import pandas as pd

from control_plane.models import PolicyVersionRecord
from control_plane.policies import build_hmm_v3_policy, build_inverse_hmm_v3_policy
from data.schemas import (
    DiscoveryDatasetSummary,
    DiscoveryRegimeSummary,
    DiscoveryReport,
    DiscoveredStrategySpec,
    HistoricalBar,
    IndicatorBucketTable,
    InverseAppendixSummary,
    PatternFinding,
)
from regime.hmm import RollingHMMRegimeEngine


class DiscoveryResearcher:
    """Builds a research dataset, mines directional patterns, and synthesizes HMM policies."""

    def __init__(
        self,
        *,
        symbol: str = "ETH/USD",
        timeframe: str = "1Min",
        hmm_resample_minutes: int = 15,
        hmm_train_window_bars: int = 20 * 24 * 4,
        hmm_retrain_interval_bars: int = 24 * 4,
        estimated_fee_bps: float = 1.25,
        base_slippage_bps: float = 0.9,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.hmm_resample_minutes = hmm_resample_minutes
        self.hmm_train_window_bars = hmm_train_window_bars
        self.hmm_retrain_interval_bars = hmm_retrain_interval_bars
        self.estimated_fee_bps = estimated_fee_bps
        self.base_slippage_bps = base_slippage_bps

    @property
    def estimated_round_trip_cost_bps(self) -> float:
        return (self.estimated_fee_bps + self.base_slippage_bps) * 2.0

    def required_warmup_minutes(self) -> int:
        return max(self.hmm_train_window_bars * self.hmm_resample_minutes, 240, 60)

    def warmup_start(self, start_at: datetime) -> datetime:
        return start_at - timedelta(minutes=self.required_warmup_minutes())

    def build_research_frame(
        self,
        *,
        bars: list[HistoricalBar],
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[pd.DataFrame, DiscoveryDatasetSummary]:
        if not bars:
            raise RuntimeError("No historical bars were provided for research.")

        frame = pd.DataFrame(
            {
                "timestamp": [bar.timestamp for bar in bars],
                "open": [float(bar.open_price) for bar in bars],
                "high": [float(bar.high_price) for bar in bars],
                "low": [float(bar.low_price) for bar in bars],
                "close": [float(bar.close_price) for bar in bars],
                "volume": [float(bar.volume) for bar in bars],
            }
        )
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        frame = frame.sort_values("timestamp").reset_index(drop=True)

        self._populate_indicator_columns(frame)
        self._populate_forward_columns(frame)
        self._populate_hmm_columns(frame, bars)

        evaluation_mask = (frame["timestamp"] >= pd.Timestamp(start_at)) & (frame["timestamp"].dt.date <= end_at.date())
        evaluation_frame = frame.loc[evaluation_mask].copy().reset_index(drop=True)
        evaluation_window_close = pd.Timestamp(end_at + timedelta(days=1))
        evaluation_frame["evaluable"] = (
            evaluation_frame["model_ready"]
            & evaluation_frame["regime"].notna()
            & evaluation_frame["forward_60m_bps"].notna()
            & evaluation_frame["hmm_atr_percentile_30"].notna()
            & ((evaluation_frame["timestamp"] + pd.Timedelta(minutes=60)) <= evaluation_window_close)
        )

        dataset = DiscoveryDatasetSummary(
            symbol=self.symbol,
            timeframe=self.timeframe,
            start_at=start_at,
            end_at=end_at,
            warmup_start_at=bars[0].timestamp,
            total_bars=len(bars),
            evaluation_bars=len(evaluation_frame),
            evaluable_bars=int(evaluation_frame["evaluable"].sum()),
            estimated_round_trip_cost_bps=self.estimated_round_trip_cost_bps,
        )
        return evaluation_frame, dataset

    def discover(
        self,
        *,
        frame: pd.DataFrame,
        dataset: DiscoveryDatasetSummary,
        version: str,
        include_inverse: bool = True,
    ) -> DiscoveryReport:
        regime_summary = self._build_regime_summary(frame)
        long_patterns = self._mine_direction_patterns(frame, direction="long")
        if not long_patterns:
            raise RuntimeError("No viable bull-trend patterns passed the discovery support floor.")

        _, candidate_strategy = self.synthesize_strategy(
            selected_pattern=long_patterns[0],
            version=version,
            direction="long",
        )

        inverse_appendix: InverseAppendixSummary | None = None
        bucket_tables = self._build_indicator_bucket_tables(frame, direction="long")
        headline_findings = self._headline_findings(
            regime_summary=regime_summary,
            selected_pattern=long_patterns[0],
        )

        if include_inverse:
            short_patterns = self._mine_direction_patterns(frame, direction="short")
            bucket_tables.extend(self._build_indicator_bucket_tables(frame, direction="short"))
            if short_patterns:
                _, inverse_strategy = self.synthesize_strategy(
                    selected_pattern=short_patterns[0],
                    version=f"{version}-inverse",
                    direction="short",
                )
                inverse_appendix = InverseAppendixSummary(
                    enabled=True,
                    headline=(
                        "Bear-regime appendix indicates inverse research rules with "
                        f"{short_patterns[0].support_count} qualifying samples and "
                        f"{short_patterns[0].score_bps:.2f} bps score."
                    ),
                    selected_pattern=short_patterns[0],
                    strategy=inverse_strategy,
                )
            else:
                inverse_appendix = InverseAppendixSummary(
                    enabled=True,
                    headline="Bear-regime appendix found no short pattern that cleared the support floor.",
                )

        return DiscoveryReport(
            dataset=dataset,
            regime_summary=regime_summary,
            indicator_bucket_tables=bucket_tables,
            headline_findings=headline_findings,
            long_patterns=long_patterns,
            selected_pattern=long_patterns[0],
            candidate_strategy=candidate_strategy,
            inverse_appendix=inverse_appendix,
        )

    def synthesize_strategy(
        self,
        *,
        selected_pattern: PatternFinding,
        version: str,
        direction: Literal["long", "short"],
    ) -> tuple[PolicyVersionRecord, DiscoveredStrategySpec]:
        stop_floor_bps = max(18.0, selected_pattern.percentile_60_adverse_excursion_bps)
        take_profit_target_bps = max(stop_floor_bps, selected_pattern.percentile_60_favorable_excursion_bps)
        max_take_profit_target_bps = max(
            take_profit_target_bps * 1.1,
            selected_pattern.percentile_85_favorable_excursion_bps,
        )
        take_profit_multiple = max(1.0, take_profit_target_bps / stop_floor_bps)
        max_reward_multiple = max(take_profit_multiple + 0.2, max_take_profit_target_bps / stop_floor_bps)
        momentum_5_threshold = max(0.0, selected_pattern.thresholds.get("momentum_5_bps_min", 0.0))

        threshold_overrides = {
            "entry_momentum_3_bps": round(max(2.0, momentum_5_threshold * 0.6), 2),
            "entry_momentum_5_bps": round(momentum_5_threshold, 2),
            "min_trend_strength_bps": round(max(6.0, momentum_5_threshold * 1.15), 2),
            "min_volume_ratio_5_30": round(selected_pattern.thresholds.get("volume_ratio_min", 1.0), 4),
            "breakout_buffer_bps": round(selected_pattern.thresholds.get("breakout_bps_min", 0.0), 2),
            "max_abs_zscore_30": round(selected_pattern.thresholds.get("abs_zscore_max", 2.0), 4),
            "min_entry_score": 6,
            "min_confirmation_count": 6,
        }
        strategy_overrides = {
            "stop_loss_vol_multiplier": 1.0,
            "min_stop_loss_bps": round(stop_floor_bps, 2),
            "max_stop_loss_bps": round(max(stop_floor_bps * 2.0, max_take_profit_target_bps, 60.0), 2),
            "trailing_stop_multiple": 0.75,
            "partial_take_profit_fraction": 0.5,
            "take_profit_multiple": round(take_profit_multiple, 4),
            "max_reward_multiple": round(max_reward_multiple, 4),
            "time_stop_bars": int(max(15, min(240, selected_pattern.median_bars_to_peak_favorable or 60))),
            "min_atr_percentile_30": round(selected_pattern.atr_band[0], 4),
            "max_atr_percentile_30": round(selected_pattern.atr_band[1], 4),
        }
        if direction == "long":
            strategy_overrides["hmm_bull_entry_probability"] = round(
                selected_pattern.thresholds.get("regime_probability_min", 0.62),
                4,
            )
            strategy_overrides["hmm_bull_continuation_probability"] = round(
                selected_pattern.thresholds.get("continuation_probability_min", 0.58),
                4,
            )
            policy = build_hmm_v3_policy(
                version=version,
                notes=(
                    "Synthesized from the three-month discovery window using bull-regime "
                    "pattern mining and fixed support thresholds."
                ),
                thresholds_overrides=threshold_overrides,
                strategy_overrides=strategy_overrides,
            )
            strategy_direction = "long_flat"
        else:
            strategy_overrides["hmm_bear_entry_probability"] = round(
                selected_pattern.thresholds.get("regime_probability_min", 0.62),
                4,
            )
            strategy_overrides["hmm_bear_continuation_probability"] = round(
                selected_pattern.thresholds.get("continuation_probability_min", 0.58),
                4,
            )
            policy = build_inverse_hmm_v3_policy(
                version=version,
                notes=(
                    "Research-only inverse appendix synthesized from the same discovery "
                    "window using bear-regime pattern mining."
                ),
                thresholds_overrides=threshold_overrides,
                strategy_overrides=strategy_overrides,
            )
            strategy_direction = "inverse_research"

        spec = DiscoveredStrategySpec(
            policy_name=policy.policy_name,
            version=policy.version,
            policy_label=policy.label,
            direction=strategy_direction,
            source_regime=selected_pattern.regime,
            thresholds=policy.thresholds,
            strategy_config=policy.strategy_config,
            notes=policy.notes or "",
            selected_pattern=selected_pattern,
        )
        return policy, spec

    def _populate_indicator_columns(self, frame: pd.DataFrame) -> None:
        close = frame["close"]
        high = frame["high"]
        low = frame["low"]
        volume = frame["volume"]

        frame["return_1_bps"] = close.pct_change(1) * 10000.0
        frame["return_3_bps"] = close.pct_change(3) * 10000.0
        frame["return_5_bps"] = close.pct_change(5) * 10000.0
        frame["return_15_bps"] = close.pct_change(15) * 10000.0
        frame["return_30_bps"] = close.pct_change(30) * 10000.0
        frame["return_60_bps"] = close.pct_change(60) * 10000.0
        frame["realized_vol_5_bps"] = frame["return_1_bps"].rolling(5).std(ddof=0)
        frame["realized_vol_15_bps"] = frame["return_1_bps"].rolling(15).std(ddof=0)
        frame["realized_vol_30_bps"] = frame["return_1_bps"].rolling(30).std(ddof=0)

        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        frame["atr_14_bps"] = (true_range / previous_close.replace(0, np.nan)) * 10000.0
        frame["atr_14_bps"] = frame["atr_14_bps"].rolling(14).mean()
        atr_30 = ((true_range / previous_close.replace(0, np.nan)) * 10000.0).rolling(30).mean()
        frame["atr_percentile_30_1m"] = self._rolling_percentile(atr_30.to_numpy(dtype=float), window=30)

        for period in (20, 60, 240):
            frame[f"ema_{period}"] = close.ewm(span=period, adjust=False).mean()
        frame["ema_gap_20_60_bps"] = ((frame["ema_20"] / frame["ema_60"]) - 1.0) * 10000.0
        frame["ema_gap_60_240_bps"] = ((frame["ema_60"] / frame["ema_240"]) - 1.0) * 10000.0
        frame["ema_slope_20_bps"] = frame["ema_20"].pct_change(5) * 10000.0
        frame["ema_slope_60_bps"] = frame["ema_60"].pct_change(5) * 10000.0
        frame["ema_slope_240_bps"] = frame["ema_240"].pct_change(5) * 10000.0

        rolling_mean_20 = close.rolling(20).mean()
        rolling_std_20 = close.rolling(20).std(ddof=0)
        rolling_mean_30 = close.rolling(30).mean()
        rolling_std_30 = close.rolling(30).std(ddof=0)
        frame["bollinger_zscore_20"] = (close - rolling_mean_20) / rolling_std_20.replace(0, np.nan)
        frame["zscore_30"] = (close - rolling_mean_30) / rolling_std_30.replace(0, np.nan)

        volume_avg_5 = volume.rolling(5).mean()
        volume_avg_30 = volume.rolling(30).mean()
        frame["volume_ratio_5_30"] = volume_avg_5 / volume_avg_30.replace(0, np.nan)
        frame["volume_zscore_30"] = (volume - volume_avg_30) / volume.rolling(30).std(ddof=0).replace(0, np.nan)

        frame["recent_high_20"] = high.shift(1).rolling(20).max()
        frame["recent_low_20"] = low.shift(1).rolling(20).min()
        frame["breakout_up_20_bps"] = np.where(
            close > frame["recent_high_20"],
            ((close - frame["recent_high_20"]) / frame["recent_high_20"]) * 10000.0,
            0.0,
        )
        frame["breakdown_20_bps"] = np.where(
            close < frame["recent_low_20"],
            ((frame["recent_low_20"] - close) / frame["recent_low_20"]) * 10000.0,
            0.0,
        )

        gains = close.diff().clip(lower=0.0)
        losses = -close.diff().clip(upper=0.0)
        avg_gain = gains.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        avg_loss = losses.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        frame["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd = ema_12 - ema_26
        signal = macd.ewm(span=9, adjust=False).mean()
        frame["macd_histogram"] = macd - signal

    def _populate_forward_columns(self, frame: pd.DataFrame) -> None:
        close = frame["close"].to_numpy(dtype=float)
        high = frame["high"].to_numpy(dtype=float)
        low = frame["low"].to_numpy(dtype=float)
        row_count = len(frame)

        for horizon in (15, 30, 60):
            values = np.full(row_count, np.nan, dtype=float)
            if row_count > horizon:
                values[: row_count - horizon] = ((close[horizon:] / close[: row_count - horizon]) - 1.0) * 10000.0
            frame[f"forward_{horizon}m_bps"] = values

        long_mfe = np.full(row_count, np.nan, dtype=float)
        long_mae = np.full(row_count, np.nan, dtype=float)
        short_mfe = np.full(row_count, np.nan, dtype=float)
        short_mae = np.full(row_count, np.nan, dtype=float)
        bars_to_peak_long = np.full(row_count, np.nan, dtype=float)
        bars_to_peak_short = np.full(row_count, np.nan, dtype=float)
        horizon = 60
        for index in range(0, max(0, row_count - horizon)):
            start_price = close[index]
            if start_price <= 0:
                continue
            future_high = high[index + 1 : index + horizon + 1]
            future_low = low[index + 1 : index + horizon + 1]
            peak_long = int(np.argmax(future_high))
            trough_long = int(np.argmin(future_low))
            long_mfe[index] = ((future_high[peak_long] / start_price) - 1.0) * 10000.0
            long_mae[index] = ((start_price - future_low[trough_long]) / start_price) * 10000.0
            short_mfe[index] = ((start_price - future_low[trough_long]) / start_price) * 10000.0
            short_mae[index] = ((future_high[peak_long] - start_price) / start_price) * 10000.0
            bars_to_peak_long[index] = peak_long + 1
            bars_to_peak_short[index] = trough_long + 1

        frame["long_mfe_60m_bps"] = long_mfe
        frame["long_mae_60m_bps"] = long_mae
        frame["short_mfe_60m_bps"] = short_mfe
        frame["short_mae_60m_bps"] = short_mae
        frame["bars_to_peak_long"] = bars_to_peak_long
        frame["bars_to_peak_short"] = bars_to_peak_short

    def _populate_hmm_columns(self, frame: pd.DataFrame, bars: list[HistoricalBar]) -> None:
        engine = RollingHMMRegimeEngine(
            symbol=self.symbol,
            resample_minutes=self.hmm_resample_minutes,
            state_count=4,
            train_window_bars=self.hmm_train_window_bars,
            retrain_interval_bars=self.hmm_retrain_interval_bars,
        )
        hmm_rows: list[dict[str, object]] = []
        for bar in bars:
            inference = engine.update(bar.to_market_snapshot())
            probabilities = inference.regime_probabilities
            continuation = inference.continuation_probabilities
            hmm_rows.append(
                {
                    "timestamp": bar.timestamp,
                    "hmm_timestamp": inference.timestamp,
                    "regime": inference.regime,
                    "regime_probability": inference.regime_probability,
                    "bull_probability": probabilities.get("bull_trend", 0.0),
                    "bear_probability": probabilities.get("bear_trend", 0.0),
                    "quiet_probability": probabilities.get("quiet_range", 0.0),
                    "stress_probability": probabilities.get("stress", 0.0),
                    "bull_continuation": continuation.get("bull_trend", 0.0),
                    "bear_continuation": continuation.get("bear_trend", 0.0),
                    "hmm_atr_14_bps": inference.atr_14_bps,
                    "hmm_atr_percentile_30": inference.atr_percentile,
                    "hmm_ema_gap_bps": inference.ema_gap_bps,
                    "hmm_fast_slope_bps": inference.ema_fast_slope_bps,
                    "hmm_slow_slope_bps": inference.ema_slow_slope_bps,
                    "htf_bullish": inference.htf_bullish,
                    "htf_bearish": inference.htf_bearish,
                    "model_ready": inference.model_ready,
                }
            )
        hmm_frame = pd.DataFrame(hmm_rows)
        hmm_frame["timestamp"] = pd.to_datetime(hmm_frame["timestamp"], utc=True)
        if not hmm_frame.empty:
            value_columns = [column for column in hmm_frame.columns if column != "timestamp"]
            frame[value_columns] = hmm_frame[value_columns].values

    def _build_regime_summary(self, frame: pd.DataFrame) -> DiscoveryRegimeSummary:
        evaluable = frame.loc[frame["evaluable"] & frame["regime"].notna()].copy()
        occupancy = evaluable["regime"].value_counts().to_dict()
        average_forward_60m_bps = (
            evaluable.groupby("regime")["forward_60m_bps"].mean().fillna(0.0).round(4).to_dict()
            if not evaluable.empty
            else {}
        )
        average_probability = (
            evaluable.groupby("regime")["regime_probability"].mean().fillna(0.0).round(4).to_dict()
            if not evaluable.empty
            else {}
        )
        transitions = self._regime_transitions(evaluable)
        return DiscoveryRegimeSummary(
            regime_occupancy={str(key): int(value) for key, value in occupancy.items()},
            regime_transitions=transitions,
            average_forward_60m_bps=average_forward_60m_bps,
            average_probability=average_probability,
        )

    def _regime_transitions(self, frame: pd.DataFrame) -> dict[str, int]:
        if frame.empty:
            return {}
        complete = frame.dropna(subset=["hmm_timestamp"]).drop_duplicates("hmm_timestamp", keep="last")
        sequence = [str(regime) for regime in complete["regime"].tolist() if regime]
        counts: Counter[str] = Counter()
        for current, nxt in zip(sequence, sequence[1:]):
            if current != nxt:
                counts[f"{current}->{nxt}"] += 1
        return dict(counts)

    def _mine_direction_patterns(self, frame: pd.DataFrame, *, direction: Literal["long", "short"]) -> list[PatternFinding]:
        regime = "bull_trend" if direction == "long" else "bear_trend"
        subset = frame.loc[frame["evaluable"] & (frame["regime"] == regime)].copy()
        if subset.empty:
            return []

        subset["directional_forward_15m_bps"] = subset["forward_15m_bps"] if direction == "long" else -subset["forward_15m_bps"]
        subset["directional_forward_30m_bps"] = subset["forward_30m_bps"] if direction == "long" else -subset["forward_30m_bps"]
        subset["directional_forward_60m_bps"] = subset["forward_60m_bps"] if direction == "long" else -subset["forward_60m_bps"]
        subset["directional_momentum_5_bps"] = subset["return_5_bps"] if direction == "long" else -subset["return_5_bps"]
        subset["directional_breakout_bps"] = (
            subset["breakout_up_20_bps"] if direction == "long" else subset["breakdown_20_bps"]
        )
        subset["directional_mfe_60m_bps"] = subset["long_mfe_60m_bps"] if direction == "long" else subset["short_mfe_60m_bps"]
        subset["directional_mae_60m_bps"] = subset["long_mae_60m_bps"] if direction == "long" else subset["short_mae_60m_bps"]
        subset["bars_to_peak_directional"] = subset["bars_to_peak_long"] if direction == "long" else subset["bars_to_peak_short"]
        subset["abs_zscore_30"] = subset["zscore_30"].abs()

        probability_column = "bull_probability" if direction == "long" else "bear_probability"
        continuation_column = "bull_continuation" if direction == "long" else "bear_continuation"
        support_floor = max(80, ceil(int(frame["evaluable"].sum()) * 0.001))

        quantiles = (0.50, 0.60, 0.70, 0.80)
        bands = ((0.10, 0.80), (0.15, 0.75), (0.20, 0.70), (0.25, 0.75))
        zscore_quantiles = (0.80, 0.90)

        probability_thresholds = self._quantile_thresholds(subset[probability_column], quantiles, lower_bound=True)
        continuation_thresholds = self._quantile_thresholds(subset[continuation_column], quantiles, lower_bound=True)
        momentum_thresholds = self._quantile_thresholds(subset["directional_momentum_5_bps"], quantiles, lower_bound=True)
        volume_thresholds = self._quantile_thresholds(subset["volume_ratio_5_30"], quantiles, lower_bound=True)
        breakout_thresholds = self._quantile_thresholds(subset["directional_breakout_bps"], quantiles, lower_bound=True)
        zscore_thresholds = self._quantile_thresholds(subset["abs_zscore_30"], zscore_quantiles, lower_bound=False)

        if not all(
            (
                probability_thresholds,
                continuation_thresholds,
                momentum_thresholds,
                volume_thresholds,
                breakout_thresholds,
                zscore_thresholds,
            )
        ):
            return []

        probability_masks = {value: subset[probability_column] >= value for value in probability_thresholds}
        continuation_masks = {value: subset[continuation_column] >= value for value in continuation_thresholds}
        momentum_masks = {value: subset["directional_momentum_5_bps"] >= value for value in momentum_thresholds}
        volume_masks = {value: subset["volume_ratio_5_30"] >= value for value in volume_thresholds}
        breakout_masks = {value: subset["directional_breakout_bps"] >= value for value in breakout_thresholds}
        zscore_masks = {value: subset["abs_zscore_30"] <= value for value in zscore_thresholds}
        atr_masks = {
            band: subset["hmm_atr_percentile_30"].between(band[0], band[1], inclusive="both")
            for band in bands
        }

        findings: list[PatternFinding] = []
        for probability_min in probability_thresholds:
            for continuation_min in continuation_thresholds:
                for momentum_min in momentum_thresholds:
                    for volume_min in volume_thresholds:
                        for breakout_min in breakout_thresholds:
                            for abs_zscore_max in zscore_thresholds:
                                for atr_band in bands:
                                    mask = (
                                        probability_masks[probability_min]
                                        & continuation_masks[continuation_min]
                                        & momentum_masks[momentum_min]
                                        & volume_masks[volume_min]
                                        & breakout_masks[breakout_min]
                                        & zscore_masks[abs_zscore_max]
                                        & atr_masks[atr_band]
                                    )
                                    support_count = int(mask.sum())
                                    if support_count < support_floor:
                                        continue
                                    filtered = subset.loc[mask]
                                    if filtered.empty:
                                        continue
                                    findings.append(
                                        PatternFinding(
                                            direction=direction,
                                            regime=regime,
                                            support_count=support_count,
                                            score_bps=round(
                                                float(filtered["directional_forward_60m_bps"].mean())
                                                - self.estimated_round_trip_cost_bps,
                                                4,
                                            ),
                                            estimated_round_trip_cost_bps=self.estimated_round_trip_cost_bps,
                                            forward_15m_mean_bps=round(float(filtered["directional_forward_15m_bps"].mean()), 4),
                                            forward_30m_mean_bps=round(float(filtered["directional_forward_30m_bps"].mean()), 4),
                                            forward_60m_mean_bps=round(float(filtered["directional_forward_60m_bps"].mean()), 4),
                                            mean_favorable_excursion_bps=round(
                                                float(filtered["directional_mfe_60m_bps"].mean()),
                                                4,
                                            ),
                                            mean_adverse_excursion_bps=round(
                                                float(filtered["directional_mae_60m_bps"].mean()),
                                                4,
                                            ),
                                            percentile_60_favorable_excursion_bps=round(
                                                float(filtered["directional_mfe_60m_bps"].quantile(0.60)),
                                                4,
                                            ),
                                            percentile_60_adverse_excursion_bps=round(
                                                float(filtered["directional_mae_60m_bps"].quantile(0.60)),
                                                4,
                                            ),
                                            percentile_85_favorable_excursion_bps=round(
                                                float(filtered["directional_mfe_60m_bps"].quantile(0.85)),
                                                4,
                                            ),
                                            median_bars_to_peak_favorable=self._median_int(
                                                filtered["bars_to_peak_directional"]
                                            ),
                                            thresholds={
                                                "regime_probability_min": round(probability_min, 4),
                                                "continuation_probability_min": round(continuation_min, 4),
                                                "momentum_5_bps_min": round(momentum_min, 4),
                                                "volume_ratio_min": round(volume_min, 4),
                                                "breakout_bps_min": round(breakout_min, 4),
                                                "abs_zscore_max": round(abs_zscore_max, 4),
                                            },
                                            atr_band=[round(atr_band[0], 4), round(atr_band[1], 4)],
                                        )
                                    )
        findings.sort(
            key=lambda finding: (
                finding.score_bps,
                finding.support_count,
                finding.forward_60m_mean_bps,
            ),
            reverse=True,
        )
        unique: list[PatternFinding] = []
        seen: set[tuple[float, ...]] = set()
        for finding in findings:
            key = (
                finding.thresholds.get("regime_probability_min", 0.0),
                finding.thresholds.get("continuation_probability_min", 0.0),
                finding.thresholds.get("momentum_5_bps_min", 0.0),
                finding.thresholds.get("volume_ratio_min", 0.0),
                finding.thresholds.get("breakout_bps_min", 0.0),
                finding.thresholds.get("abs_zscore_max", 0.0),
                finding.atr_band[0],
                finding.atr_band[1],
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(finding)
            if len(unique) == 5:
                break
        return unique

    def _build_indicator_bucket_tables(
        self,
        frame: pd.DataFrame,
        *,
        direction: Literal["long", "short"],
    ) -> list[IndicatorBucketTable]:
        regime = "bull_trend" if direction == "long" else "bear_trend"
        subset = frame.loc[frame["evaluable"] & (frame["regime"] == regime)].copy()
        if subset.empty:
            return []

        subset["directional_forward_60m_bps"] = subset["forward_60m_bps"] if direction == "long" else -subset["forward_60m_bps"]
        probability_column = "bull_probability" if direction == "long" else "bear_probability"
        continuation_column = "bull_continuation" if direction == "long" else "bear_continuation"

        tables = [
            ("regime_probability", subset[probability_column]),
            ("continuation_probability", subset[continuation_column]),
            ("momentum_5_bps", subset["return_5_bps"] if direction == "long" else -subset["return_5_bps"]),
            ("volume_ratio_5_30", subset["volume_ratio_5_30"]),
            (
                "breakout_distance_bps",
                subset["breakout_up_20_bps"] if direction == "long" else subset["breakdown_20_bps"],
            ),
        ]
        rendered: list[IndicatorBucketTable] = []
        for indicator, values in tables:
            buckets = self._bucket_means(values=values, targets=subset["directional_forward_60m_bps"])
            if buckets:
                rendered.append(IndicatorBucketTable(indicator=indicator, direction=direction, buckets=buckets))
        return rendered

    def _headline_findings(
        self,
        *,
        regime_summary: DiscoveryRegimeSummary,
        selected_pattern: PatternFinding,
    ) -> list[str]:
        top_regime = None
        if regime_summary.regime_occupancy:
            top_regime = max(regime_summary.regime_occupancy, key=regime_summary.regime_occupancy.get)
        findings: list[str] = []
        if top_regime is not None:
            findings.append(
                f"Most occupied regime was {top_regime} with {regime_summary.regime_occupancy[top_regime]} evaluable minutes."
            )
        findings.append(
            "Selected bull-regime pattern scored "
            f"{selected_pattern.score_bps:.2f} bps after estimated costs across "
            f"{selected_pattern.support_count} qualifying samples."
        )
        findings.append(
            "Selected thresholds: "
            f"bull_prob>={selected_pattern.thresholds.get('regime_probability_min', 0.0):.2f}, "
            f"bull_cont>={selected_pattern.thresholds.get('continuation_probability_min', 0.0):.2f}, "
            f"momentum_5>={selected_pattern.thresholds.get('momentum_5_bps_min', 0.0):.2f} bps."
        )
        return findings

    def _bucket_means(self, *, values: pd.Series, targets: pd.Series, bucket_count: int = 4) -> dict[str, float]:
        clean = pd.DataFrame({"value": values, "target": targets}).dropna()
        if clean.empty or clean["value"].nunique() < 2:
            return {}
        ranked = clean["value"].rank(method="first")
        bucket_ids = pd.qcut(ranked, q=min(bucket_count, len(clean)), labels=False, duplicates="drop")
        buckets: dict[str, float] = {}
        for bucket_id in sorted(bucket_ids.dropna().unique()):
            mask = bucket_ids == bucket_id
            buckets[f"Q{int(bucket_id) + 1}"] = round(float(clean.loc[mask, "target"].mean()), 4)
        return buckets

    def _quantile_thresholds(
        self,
        series: pd.Series,
        quantiles: tuple[float, ...],
        *,
        lower_bound: bool,
    ) -> list[float]:
        clean = series.dropna()
        if clean.empty:
            return []
        values = sorted({round(float(clean.quantile(level)), 4) for level in quantiles})
        if not lower_bound:
            return values
        return values

    def _rolling_percentile(self, values: np.ndarray, *, window: int) -> np.ndarray:
        result = np.full(len(values), np.nan, dtype=float)
        for index in range(len(values)):
            start = max(0, index - window + 1)
            window_values = values[start : index + 1]
            finite = window_values[np.isfinite(window_values)]
            if finite.size == 0 or not np.isfinite(values[index]):
                continue
            result[index] = float(np.mean(finite <= values[index]))
        return result

    def _median_int(self, values: pd.Series, default: int = 60) -> int:
        clean = values.dropna()
        if clean.empty:
            return default
        return int(clean.median())
