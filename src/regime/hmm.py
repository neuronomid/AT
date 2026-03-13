from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

import numpy as np
from hmmlearn.hmm import GaussianHMM
from sklearn.preprocessing import StandardScaler

from data.schemas import HistoricalBar, MarketSnapshot


REGIME_LABELS: tuple[str, ...] = ("bull_trend", "bear_trend", "quiet_range", "stress")


@dataclass(slots=True)
class RegimeObservation:
    timestamp: datetime
    features: dict[str, float]
    label_metrics: dict[str, float]


@dataclass(slots=True)
class RegimeInference:
    timestamp: datetime | None = None
    regime: str | None = None
    regime_probability: float = 0.0
    regime_probabilities: dict[str, float] = field(default_factory=dict)
    continuation_probabilities: dict[str, float] = field(default_factory=dict)
    atr_14_bps: float | None = None
    atr_percentile: float | None = None
    ema_gap_bps: float | None = None
    ema_fast_slope_bps: float | None = None
    ema_slow_slope_bps: float | None = None
    htf_bullish: bool = False
    htf_bearish: bool = False
    observation_count: int = 0
    model_ready: bool = False


class MinuteBarAggregator:
    """Incrementally builds completed higher-timeframe bars from 1-minute snapshots."""

    def __init__(self, *, symbol: str, resample_minutes: int) -> None:
        self._symbol = symbol
        self._resample_minutes = resample_minutes
        self._current_bucket_start: datetime | None = None
        self._open_price: Decimal | None = None
        self._high_price: Decimal | None = None
        self._low_price: Decimal | None = None
        self._close_price: Decimal | None = None
        self._volume: Decimal = Decimal("0")

    def update(self, snapshot: MarketSnapshot) -> list[HistoricalBar]:
        completed: list[HistoricalBar] = []
        price = snapshot.last_trade_price or snapshot.ask_price or snapshot.bid_price
        if price is None:
            return completed

        bucket_start = self._bucket_start(snapshot.timestamp)
        if self._current_bucket_start is None:
            self._start_bucket(bucket_start=bucket_start, snapshot=snapshot, price=price)
            return completed

        if bucket_start != self._current_bucket_start:
            completed.append(self._build_bar(self._current_bucket_start))
            self._start_bucket(bucket_start=bucket_start, snapshot=snapshot, price=price)
            return completed

        self._update_bucket(snapshot=snapshot, price=price)
        return completed

    def _start_bucket(self, *, bucket_start: datetime, snapshot: MarketSnapshot, price: Decimal) -> None:
        self._current_bucket_start = bucket_start
        self._open_price = snapshot.open_price or price
        self._high_price = snapshot.high_price or price
        self._low_price = snapshot.low_price or price
        self._close_price = price
        self._volume = snapshot.last_trade_size or Decimal("0")

    def _update_bucket(self, *, snapshot: MarketSnapshot, price: Decimal) -> None:
        high_price = snapshot.high_price or price
        low_price = snapshot.low_price or price
        self._high_price = max(self._high_price or high_price, high_price)
        self._low_price = min(self._low_price or low_price, low_price)
        self._close_price = price
        self._volume += snapshot.last_trade_size or Decimal("0")

    def _build_bar(self, bucket_start: datetime) -> HistoricalBar:
        return HistoricalBar(
            symbol=self._symbol,
            timeframe=f"{self._resample_minutes}Min",
            location="derived",
            timestamp=bucket_start,
            open_price=self._open_price or Decimal("0"),
            high_price=self._high_price or self._close_price or Decimal("0"),
            low_price=self._low_price or self._close_price or Decimal("0"),
            close_price=self._close_price or Decimal("0"),
            volume=self._volume,
        )

    def _bucket_start(self, timestamp: datetime) -> datetime:
        normalized = timestamp.astimezone(timezone.utc).replace(second=0, microsecond=0)
        minute = normalized.minute - (normalized.minute % self._resample_minutes)
        return normalized.replace(minute=minute)


class HMMFeatureBuilder:
    """Builds stationary higher-timeframe features for HMM training and inference."""

    FEATURE_ORDER: tuple[str, ...] = (
        "log_return_1",
        "log_return_3",
        "realized_vol_6",
        "atr_14_bps",
        "atr_percentile_30",
        "range_bps",
        "ema_gap_8_21_bps",
        "ema_fast_slope_bps",
        "ema_slow_slope_bps",
        "volume_zscore_20",
    )

    def __init__(self) -> None:
        self._bars: deque[HistoricalBar] = deque(maxlen=4000)
        self._log_returns: deque[float] = deque(maxlen=4000)
        self._true_ranges_bps: deque[float] = deque(maxlen=4000)
        self._atr_history: deque[float] = deque(maxlen=4000)
        self._volumes: deque[float] = deque(maxlen=4000)
        self._ema_values: dict[int, float] = {}
        self._ema_history: dict[int, deque[float]] = {
            8: deque(maxlen=5),
            21: deque(maxlen=5),
        }

    def update(self, bar: HistoricalBar) -> RegimeObservation | None:
        previous_close = self._bars[-1].close_price if self._bars else None
        self._bars.append(bar)
        self._volumes.append(float(bar.volume))
        self._update_emas(float(bar.close_price))

        if previous_close is not None and previous_close > 0:
            self._log_returns.append(float(np.log(float(bar.close_price / previous_close))))
            self._true_ranges_bps.append(self._true_range_bps(previous_close, bar.high_price, bar.low_price))

        if len(self._true_ranges_bps) >= 14:
            self._atr_history.append(self._window_average(list(self._true_ranges_bps)[-14:]))

        features = self._build_features(bar)
        if features is None:
            return None

        label_metrics = {
            "mean_return": features["log_return_1"],
            "mean_abs_return": abs(features["log_return_1"]),
            "mean_volatility": features["realized_vol_6"],
            "mean_atr_percentile": features["atr_percentile_30"],
        }
        return RegimeObservation(timestamp=bar.timestamp, features=features, label_metrics=label_metrics)

    def _build_features(self, bar: HistoricalBar) -> dict[str, float] | None:
        if len(self._bars) < 30 or len(self._log_returns) < 6 or len(self._atr_history) < 30:
            return None

        close_values = [float(item.close_price) for item in self._bars]
        current_close = close_values[-1]
        fast_history = self._ema_history[8]
        slow_history = self._ema_history[21]
        volume_window = list(self._volumes)[-20:]
        atr_14_bps = self._atr_history[-1]
        atr_percentile = self._percentile_rank(list(self._atr_history)[-30:], atr_14_bps)
        return {
            "log_return_1": self._log_returns[-1],
            "log_return_3": float(np.log(current_close / close_values[-4])) if close_values[-4] > 0 else 0.0,
            "realized_vol_6": float(np.std(list(self._log_returns)[-6:], ddof=0)),
            "atr_14_bps": atr_14_bps,
            "atr_percentile_30": atr_percentile,
            "range_bps": self._range_bps(bar),
            "ema_gap_8_21_bps": self._bps(self._ema_values[8], self._ema_values[21]),
            "ema_fast_slope_bps": self._bps(fast_history[0], fast_history[-1]),
            "ema_slow_slope_bps": self._bps(slow_history[0], slow_history[-1]),
            "volume_zscore_20": self._zscore(volume_window),
        }

    def to_matrix(self, observations: Iterable[RegimeObservation]) -> np.ndarray:
        rows = [
            [observation.features[name] for name in self.FEATURE_ORDER]
            for observation in observations
        ]
        return np.asarray(rows, dtype=float)

    def _update_emas(self, close_price: float) -> None:
        for period in (8, 21):
            previous = self._ema_values.get(period)
            if previous is None:
                updated = close_price
            else:
                multiplier = 2.0 / (period + 1)
                updated = ((close_price - previous) * multiplier) + previous
            self._ema_values[period] = updated
            self._ema_history[period].append(updated)

    def _true_range_bps(self, previous_close: Decimal, high_price: Decimal, low_price: Decimal) -> float:
        true_range = max(
            high_price - low_price,
            abs(high_price - previous_close),
            abs(low_price - previous_close),
        )
        if previous_close <= 0:
            return 0.0
        return float((true_range / previous_close) * Decimal("10000"))

    def _range_bps(self, bar: HistoricalBar) -> float:
        if bar.close_price <= 0:
            return 0.0
        return float(((bar.high_price - bar.low_price) / bar.close_price) * Decimal("10000"))

    def _percentile_rank(self, values: list[float], target: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        less_or_equal = sum(1 for value in ordered if value <= target)
        return less_or_equal / len(ordered)

    def _zscore(self, values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        std = float(np.std(values, ddof=0))
        if std == 0:
            return 0.0
        mean = float(np.mean(values))
        return (values[-1] - mean) / std

    def _window_average(self, values: list[float]) -> float:
        if not values:
            return 0.0
        return float(np.mean(values))

    def _bps(self, start: float, end: float) -> float:
        if start <= 0:
            return 0.0
        return ((end - start) / start) * 10000.0


class RollingHMMRegimeEngine:
    """Fits and updates a Gaussian HMM on rolling higher-timeframe features."""

    def __init__(
        self,
        *,
        symbol: str,
        resample_minutes: int = 15,
        state_count: int = 4,
        train_window_bars: int = 20 * 24 * 4,
        retrain_interval_bars: int = 24 * 4,
        random_state: int = 7,
    ) -> None:
        self._aggregator = MinuteBarAggregator(symbol=symbol, resample_minutes=resample_minutes)
        self._feature_builder = HMMFeatureBuilder()
        self._state_count = state_count
        self._train_window_bars = train_window_bars
        self._retrain_interval_bars = retrain_interval_bars
        self._random_state = random_state
        self._observations: list[RegimeObservation] = []
        self._latest_inference = RegimeInference()
        self._model: GaussianHMM | None = None
        self._scaler: StandardScaler | None = None
        self._state_labels: dict[int, str] = {}
        self._last_trained_observation_count = 0

    def update(self, snapshot: MarketSnapshot) -> RegimeInference:
        completed_bars = self._aggregator.update(snapshot)
        for bar in completed_bars:
            observation = self._feature_builder.update(bar)
            if observation is None:
                continue
            self._observations.append(observation)
            if self._should_retrain():
                self._fit_model()
            if self._model is not None and self._scaler is not None:
                self._latest_inference = self._infer_latest()
        return self._latest_inference

    @property
    def latest_inference(self) -> RegimeInference:
        return self._latest_inference

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    def _should_retrain(self) -> bool:
        if len(self._observations) < self._train_window_bars:
            return False
        if self._model is None:
            return True
        return (len(self._observations) - self._last_trained_observation_count) >= self._retrain_interval_bars

    def _fit_model(self) -> None:
        training = self._observations[-self._train_window_bars :]
        raw_matrix = self._feature_builder.to_matrix(training)
        scaler = StandardScaler()
        scaled = scaler.fit_transform(raw_matrix)
        model = GaussianHMM(
            n_components=self._state_count,
            covariance_type="diag",
            random_state=self._random_state,
            n_iter=200,
            min_covar=1e-4,
        )
        model.fit(scaled)
        _, posterior = model.score_samples(scaled)
        state_metrics = self._state_metrics(training, posterior)
        self._model = model
        self._scaler = scaler
        self._state_labels = self._label_states(state_metrics)
        self._last_trained_observation_count = len(self._observations)

    def _infer_latest(self) -> RegimeInference:
        assert self._model is not None
        assert self._scaler is not None

        inference_window = self._observations[-self._train_window_bars :]
        raw_matrix = self._feature_builder.to_matrix(inference_window)
        scaled = self._scaler.transform(raw_matrix)
        _, posterior = self._model.score_samples(scaled)
        current_posterior = posterior[-1]
        continuation = current_posterior @ self._model.transmat_
        current_observation = inference_window[-1]
        regime_probabilities = {
            self._state_labels.get(state, f"state_{state}"): float(current_posterior[state])
            for state in range(self._state_count)
        }
        continuation_probabilities = {
            self._state_labels.get(state, f"state_{state}"): float(continuation[state])
            for state in range(self._state_count)
        }
        regime = max(regime_probabilities, key=regime_probabilities.get)
        ema_gap = current_observation.features["ema_gap_8_21_bps"]
        fast_slope = current_observation.features["ema_fast_slope_bps"]
        slow_slope = current_observation.features["ema_slow_slope_bps"]
        return RegimeInference(
            timestamp=current_observation.timestamp,
            regime=regime,
            regime_probability=regime_probabilities[regime],
            regime_probabilities=regime_probabilities,
            continuation_probabilities=continuation_probabilities,
            atr_14_bps=current_observation.features["atr_14_bps"],
            atr_percentile=current_observation.features["atr_percentile_30"],
            ema_gap_bps=ema_gap,
            ema_fast_slope_bps=fast_slope,
            ema_slow_slope_bps=slow_slope,
            htf_bullish=ema_gap > 0 and fast_slope > 0 and slow_slope >= 0,
            htf_bearish=ema_gap < 0 and fast_slope < 0 and slow_slope <= 0,
            observation_count=len(self._observations),
            model_ready=True,
        )

    def _state_metrics(
        self,
        observations: list[RegimeObservation],
        posterior: np.ndarray,
    ) -> dict[int, dict[str, float]]:
        metrics: dict[int, dict[str, float]] = {}
        for state in range(self._state_count):
            weights = posterior[:, state]
            weight_sum = float(np.sum(weights))
            if weight_sum <= 0:
                metrics[state] = {
                    "mean_return": 0.0,
                    "mean_abs_return": 0.0,
                    "mean_volatility": 0.0,
                    "mean_atr_percentile": 0.0,
                }
                continue
            metrics[state] = {
                "mean_return": self._weighted_average(
                    [item.label_metrics["mean_return"] for item in observations],
                    weights,
                ),
                "mean_abs_return": self._weighted_average(
                    [item.label_metrics["mean_abs_return"] for item in observations],
                    weights,
                ),
                "mean_volatility": self._weighted_average(
                    [item.label_metrics["mean_volatility"] for item in observations],
                    weights,
                ),
                "mean_atr_percentile": self._weighted_average(
                    [item.label_metrics["mean_atr_percentile"] for item in observations],
                    weights,
                ),
            }
        return metrics

    def _label_states(self, state_metrics: dict[int, dict[str, float]]) -> dict[int, str]:
        remaining = set(state_metrics.keys())
        labels: dict[int, str] = {}

        stress_state = max(
            remaining,
            key=lambda state: (
                state_metrics[state]["mean_volatility"],
                state_metrics[state]["mean_atr_percentile"],
            ),
        )
        labels[stress_state] = "stress"
        remaining.remove(stress_state)

        quiet_state = min(
            remaining,
            key=lambda state: state_metrics[state]["mean_abs_return"],
        )
        labels[quiet_state] = "quiet_range"
        remaining.remove(quiet_state)

        bull_state = max(
            remaining,
            key=lambda state: state_metrics[state]["mean_return"],
        )
        labels[bull_state] = "bull_trend"
        remaining.remove(bull_state)

        bear_state = remaining.pop()
        labels[bear_state] = "bear_trend"
        return labels

    def _weighted_average(self, values: list[float], weights: np.ndarray) -> float:
        numerator = float(np.dot(np.asarray(values, dtype=float), weights))
        denominator = float(np.sum(weights))
        if denominator <= 0:
            return 0.0
        return numerator / denominator
