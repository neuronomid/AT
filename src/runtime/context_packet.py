from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from statistics import pstdev

from data.schemas import AccountSnapshot, LessonRecord, LiveCandle, TradeReflection
from execution.position_tracker import OpenTradeState


def build_context_signature(
    *,
    ema_stack_bucket: str,
    atr_bucket: str,
    breakout_state: str,
    spread_bucket: str,
    thesis_tags: Sequence[str],
) -> str:
    tag_block = ",".join(sorted(tag.strip().lower() for tag in thesis_tags if tag.strip())) or "none"
    return "|".join([ema_stack_bucket, atr_bucket, breakout_state, spread_bucket, tag_block])


class ContextPacketBuilder:
    """Builds the bounded v4 prompt packet from recent live state."""

    def __init__(self, candle_lookback: int = 20) -> None:
        self._candle_lookback = candle_lookback

    def build(
        self,
        *,
        candles: Sequence[LiveCandle],
        account_snapshot: AccountSnapshot,
        open_trade: OpenTradeState | None,
        trades_this_hour: int,
        stale_age_seconds: float | None,
        latest_reflection: TradeReflection | None,
        lessons: Sequence[LessonRecord],
    ) -> dict[str, object]:
        recent = list(candles)[-self._candle_lookback :]
        if not recent:
            raise ValueError("At least one closed candle is required to build the context packet.")

        closes = [float(candle.close_price) for candle in recent]
        spreads = [candle.spread_bps for candle in recent if candle.spread_bps is not None]
        volumes = [float(candle.volume) for candle in recent]
        ema_5 = self._ema(closes, 5)
        ema_9 = self._ema(closes, 9)
        ema_20 = self._ema(closes, 20)
        atr_14 = self._atr(recent, 14)
        atr_values = [self._atr(recent[: index], 14) for index in range(14, len(recent) + 1)]
        atr_percentile = self._percentile_rank(atr_values, atr_14)
        rolling_vwap = self._rolling_vwap(recent)
        volume_zscore = self._zscore(volumes)
        spread_percentile = self._percentile_rank(spreads, spreads[-1] if spreads else None)
        trade_intensity = sum(candle.trade_count for candle in recent[-5:]) / max(1, len(recent[-5:]))
        latest_close = recent[-1].close_price

        timeframe_5 = self._timeframe_summary(recent, 5)
        timeframe_15 = self._timeframe_summary(recent, 15)
        ema_stack_bucket = self._ema_stack_bucket(ema_5, ema_9, ema_20)
        atr_bucket = self._atr_bucket(atr_percentile)
        breakout_state = self._breakout_state(recent)
        spread_bucket = self._spread_bucket(spreads[-1] if spreads else None)

        selected_lessons = self._select_lessons(lessons)
        long_setup_flags = self._long_setup_flags(
            ema_stack_bucket=ema_stack_bucket,
            breakout_state=breakout_state,
            timeframe_5=timeframe_5,
            timeframe_15=timeframe_15,
            rolling_vwap_distance_bps=self._distance_bps(float(latest_close), rolling_vwap),
            atr_bucket=atr_bucket,
            trade_intensity=trade_intensity,
            volume_zscore=volume_zscore,
        )
        warning_flags = self._warning_flags(
            ema_stack_bucket=ema_stack_bucket,
            breakout_state=breakout_state,
            spread_bucket=spread_bucket,
            trade_intensity=trade_intensity,
            volume_zscore=volume_zscore,
            stale_age_seconds=stale_age_seconds,
        )
        context_signature = build_context_signature(
            ema_stack_bucket=ema_stack_bucket,
            atr_bucket=atr_bucket,
            breakout_state=breakout_state,
            spread_bucket=spread_bucket,
            thesis_tags=(latest_reflection.thesis_tags if latest_reflection is not None else []),
        )

        packet = {
            "symbol": recent[-1].symbol,
            "latest_close": float(latest_close),
            "candles_1m": [
                {
                    "start_at": candle.start_at.isoformat(),
                    "end_at": candle.end_at.isoformat(),
                    "open": float(candle.open_price),
                    "high": float(candle.high_price),
                    "low": float(candle.low_price),
                    "close": float(candle.close_price),
                    "volume": float(candle.volume),
                    "trade_count": candle.trade_count,
                    "spread_bps": candle.spread_bps,
                    "body_pct": candle.body_pct,
                    "upper_wick_pct": candle.upper_wick_pct,
                    "lower_wick_pct": candle.lower_wick_pct,
                    "close_range_position": candle.close_range_position,
                }
                for candle in recent
            ],
            "indicator_snapshot": {
                "ema_5": ema_5,
                "ema_9": ema_9,
                "ema_20": ema_20,
                "atr_14": atr_14,
                "atr_percentile": atr_percentile,
                "rolling_vwap_distance_bps": self._distance_bps(float(latest_close), rolling_vwap),
                "volume_zscore": volume_zscore,
                "spread_percentile": spread_percentile,
                "trade_intensity_5": trade_intensity,
                "ema_stack_bucket": ema_stack_bucket,
                "atr_bucket": atr_bucket,
                "breakout_state": breakout_state,
                "spread_bucket": spread_bucket,
            },
            "timeframes": {
                "5m": timeframe_5,
                "15m": timeframe_15,
            },
            "microstructure": {
                "spread_bps": spreads[-1] if spreads else None,
                "spread_percentile": spread_percentile,
                "quote_imbalance": self._quote_imbalance(recent[-1]),
                "trade_intensity": trade_intensity,
            },
            "decision_support": {
                "long_setup_score": len(long_setup_flags),
                "long_setup_flags": long_setup_flags,
                "warning_flags": warning_flags,
            },
            "portfolio": {
                "equity": float(account_snapshot.equity),
                "cash": float(account_snapshot.cash),
                "buying_power": float(account_snapshot.buying_power),
                "open_qty": float(account_snapshot.open_position_qty),
                "avg_entry_price": float(account_snapshot.avg_entry_price),
                "market_value": float(account_snapshot.market_value),
                "unrealized_pl": float(account_snapshot.unrealized_pl),
                "trades_this_hour": trades_this_hour,
            },
            "open_trade": (open_trade.to_prompt_payload(float(latest_close)) if open_trade is not None else None),
            "feedback": {
                "latest_reflection": (
                    {
                        "realized_pnl_usd": float(latest_reflection.realized_pnl_usd),
                        "realized_r": latest_reflection.realized_r,
                        "exit_reason": latest_reflection.exit_reason,
                        "thesis_tags": latest_reflection.thesis_tags,
                        "avoid_lessons": latest_reflection.avoid_lessons,
                        "reinforce_lessons": latest_reflection.reinforce_lessons,
                    }
                    if latest_reflection is not None
                    else None
                ),
                "avoid": selected_lessons["avoid"],
                "reinforce": selected_lessons["reinforce"],
            },
            "state": {
                "stale_age_seconds": stale_age_seconds,
                "context_signature": context_signature,
            },
        }
        return packet

    def _ema(self, values: Sequence[float], period: int) -> float:
        if not values:
            return 0.0
        multiplier = 2.0 / (period + 1)
        ema = values[0]
        for value in values[1:]:
            ema = ((value - ema) * multiplier) + ema
        return ema

    def _atr(self, candles: Sequence[LiveCandle], period: int) -> float:
        if len(candles) < 2:
            return 0.0
        window = list(candles)[-period:]
        true_ranges: list[float] = []
        previous_close = float(window[0].close_price)
        for candle in window[1:]:
            high = float(candle.high_price)
            low = float(candle.low_price)
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
            true_ranges.append(true_range)
            previous_close = float(candle.close_price)
        if not true_ranges:
            return 0.0
        return sum(true_ranges) / len(true_ranges)

    def _rolling_vwap(self, candles: Sequence[LiveCandle]) -> float:
        numerator = 0.0
        denominator = 0.0
        for candle in candles:
            if candle.volume <= 0:
                continue
            price = float(candle.vwap or candle.close_price)
            volume = float(candle.volume)
            numerator += price * volume
            denominator += volume
        if denominator <= 0:
            return float(candles[-1].close_price)
        return numerator / denominator

    def _zscore(self, values: Sequence[float]) -> float:
        if len(values) <= 1:
            return 0.0
        mean = sum(values) / len(values)
        std = float(pstdev(values))
        if std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _percentile_rank(self, values: Sequence[float | None], target: float | None) -> float | None:
        filtered = [value for value in values if value is not None]
        if not filtered or target is None:
            return None
        less_or_equal = sum(1 for value in filtered if value <= target)
        return less_or_equal / len(filtered)

    def _timeframe_summary(self, candles: Sequence[LiveCandle], span: int) -> dict[str, object]:
        window = list(candles)[-span:]
        start = float(window[0].open_price)
        end = float(window[-1].close_price)
        atr = self._atr(window, min(14, len(window)))
        ema_slope = self._distance_bps(self._ema([float(c.close_price) for c in window], max(2, span // 2)), start)
        return {
            "return_bps": self._distance_bps(end, start),
            "ema_slope_bps": ema_slope,
            "atr": atr,
            "regime": ("expansion" if atr > 0 and atr >= max(0.0001, abs(end - start)) else "compression"),
            "breakout_state": self._breakout_state(window),
        }

    def _distance_bps(self, current: float, reference: float) -> float:
        if reference == 0:
            return 0.0
        return ((current - reference) / reference) * 10000.0

    def _quote_imbalance(self, candle: LiveCandle) -> float | None:
        if candle.bid_price is None or candle.ask_price is None:
            return None
        midpoint = (candle.bid_price + candle.ask_price) / Decimal("2")
        if midpoint <= 0:
            return None
        return float((candle.close_price - midpoint) / midpoint)

    def _breakout_state(self, candles: Sequence[LiveCandle]) -> str:
        if len(candles) < 4:
            return "forming"
        window = list(candles)
        current = float(window[-1].close_price)
        prior_high = max(float(candle.high_price) for candle in window[:-1])
        prior_low = min(float(candle.low_price) for candle in window[:-1])
        if current > prior_high:
            return "breakout_up"
        if current < prior_low:
            return "breakdown"
        return "inside"

    def _ema_stack_bucket(self, ema_5: float, ema_9: float, ema_20: float) -> str:
        if ema_5 >= ema_9 >= ema_20:
            return "bull_stack"
        if ema_5 <= ema_9 <= ema_20:
            return "bear_stack"
        return "mixed_stack"

    def _atr_bucket(self, atr_percentile: float | None) -> str:
        if atr_percentile is None:
            return "unknown_atr"
        if atr_percentile < 0.33:
            return "low_atr"
        if atr_percentile < 0.66:
            return "mid_atr"
        return "high_atr"

    def _spread_bucket(self, spread_bps: float | None) -> str:
        if spread_bps is None:
            return "unknown_spread"
        if spread_bps < 4:
            return "tight_spread"
        if spread_bps < 10:
            return "normal_spread"
        return "wide_spread"

    def _select_lessons(self, lessons: Sequence[LessonRecord]) -> dict[str, list[str]]:
        avoid: list[str] = []
        reinforce: list[str] = []
        for lesson in reversed(list(lessons)):
            polarity = str(lesson.metadata.get("polarity", ""))
            if polarity == "avoid" and lesson.message not in avoid:
                avoid.append(lesson.message)
            if polarity == "reinforce" and lesson.message not in reinforce:
                reinforce.append(lesson.message)
            if len(avoid) >= 3 and len(reinforce) >= 3:
                break
        return {
            "avoid": avoid[:3],
            "reinforce": reinforce[:3],
        }

    def _long_setup_flags(
        self,
        *,
        ema_stack_bucket: str,
        breakout_state: str,
        timeframe_5: dict[str, object],
        timeframe_15: dict[str, object],
        rolling_vwap_distance_bps: float,
        atr_bucket: str,
        trade_intensity: float,
        volume_zscore: float,
    ) -> list[str]:
        flags: list[str] = []
        if ema_stack_bucket == "bull_stack":
            flags.append("bullish_ema_stack")
        if breakout_state == "breakout_up" or timeframe_5.get("breakout_state") == "breakout_up":
            flags.append("breakout_pressure")
        if float(timeframe_5.get("return_bps", 0.0)) > 0:
            flags.append("positive_5m_return")
        if float(timeframe_15.get("return_bps", 0.0)) > 0:
            flags.append("positive_15m_return")
        if rolling_vwap_distance_bps >= 0:
            flags.append("price_above_vwap")
        if atr_bucket in {"mid_atr", "high_atr"}:
            flags.append("usable_volatility")
        if trade_intensity >= 1.0 or volume_zscore >= 0:
            flags.append("active_tape")
        return flags

    def _warning_flags(
        self,
        *,
        ema_stack_bucket: str,
        breakout_state: str,
        spread_bucket: str,
        trade_intensity: float,
        volume_zscore: float,
        stale_age_seconds: float | None,
    ) -> list[str]:
        flags: list[str] = []
        if ema_stack_bucket == "bear_stack":
            flags.append("bearish_ema_stack")
        if breakout_state == "breakdown":
            flags.append("breakdown_risk")
        if spread_bucket == "wide_spread":
            flags.append("wide_spread")
        if trade_intensity < 0.5 and volume_zscore < -0.5:
            flags.append("thin_activity")
        if stale_age_seconds is not None and stale_age_seconds > 30:
            flags.append("stale_market_state")
        return flags
