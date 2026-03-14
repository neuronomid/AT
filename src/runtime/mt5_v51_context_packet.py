from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from data.mt5_v51_schemas import MT5V51Bar, MT5V51BridgeSnapshot, MT5V51TicketRecord
from data.schemas import LessonRecord, TradeReflection
from execution.mt5_v51_ticket_registry import MT5V51TicketRegistry
from runtime.mt5_v51_quote_tape import MT5V51QuoteTape


class MT5V51ContextBuilder:
    def __init__(self, *, quote_tape: MT5V51QuoteTape | None = None) -> None:
        self._quote_tape = quote_tape or MT5V51QuoteTape()

    def observe_snapshot(self, snapshot: MT5V51BridgeSnapshot) -> None:
        self._quote_tape.ingest(snapshot)

    def build_entry_packet(
        self,
        *,
        snapshot: MT5V51BridgeSnapshot,
        registry: MT5V51TicketRegistry,
        risk_posture: str,
        reflections: Sequence[TradeReflection] | None = None,
        lessons: Sequence[LessonRecord] | None = None,
    ) -> dict[str, object]:
        self.observe_snapshot(snapshot)
        twenty = self._timeframe_summary(snapshot.bars_20s, label="20s")
        one = self._timeframe_summary(snapshot.bars_1m, label="1m")
        five = self._timeframe_summary(snapshot.bars_5m, label="5m")
        context_signature = self._context_signature(twenty=twenty, one=one, five=five, spread_bps=snapshot.spread_bps)
        quote_metrics = self._quote_tape.build_payload(
            snapshot=snapshot,
            one_minute_atr_bps=self._optional_float(one.get("atr_14_bps")),
        )
        freshness = {
            "source_snapshot_age_ms": quote_metrics.pop("source_snapshot_age_ms"),
            "source_snapshot_age_bucket": quote_metrics.pop("source_snapshot_age_bucket"),
        }
        return {
            "symbol": snapshot.symbol,
            "server_time": snapshot.server_time.isoformat(),
            "position_state": ("occupied" if registry.has_open_position(snapshot.symbol) else "flat"),
            "quote": {
                "bid": self._round(float(snapshot.bid)),
                "ask": self._round(float(snapshot.ask)),
                "spread_bps": self._round(snapshot.spread_bps),
            },
            "freshness": freshness,
            "microstructure": quote_metrics,
            "timeframes": {
                "20s": twenty,
                "1m": one,
                "5m": five,
            },
            "recent_bars": {
                "20s": self._recent_bar_window(snapshot.bars_20s, limit=15),
                "1m": self._recent_bar_window(snapshot.bars_1m, limit=12),
            },
            "levels": {
                "1m": self._swing_distance_payload(snapshot.bars_1m, lookback=20, label="1m"),
                "5m": self._swing_distance_payload(snapshot.bars_5m, lookback=12, label="5m"),
            },
            "trend_regime": self._trend_regime_payload(twenty=twenty, one=one, five=five),
            "risk_posture": risk_posture,
            "context_signature": context_signature,
        }

    def build_manager_packet(
        self,
        *,
        snapshot: MT5V51BridgeSnapshot,
        registry: MT5V51TicketRegistry,
        allowed_actions: dict[str, list[str]],
        risk_posture: str,
        reflections: Sequence[TradeReflection],
        lessons: Sequence[LessonRecord],
    ) -> dict[str, object]:
        self.observe_snapshot(snapshot)
        tickets = [
            self._ticket_payload(ticket=ticket, allowed_actions=allowed_actions.get(ticket.ticket_id, ["hold"]))
            for ticket in registry.all(snapshot.symbol)
        ]
        return {
            "symbol": snapshot.symbol,
            "server_time": snapshot.server_time.isoformat(),
            "quote": {
                "bid": float(snapshot.bid),
                "ask": float(snapshot.ask),
                "spread_bps": snapshot.spread_bps,
            },
            "timeframes": {
                "20s": self._timeframe_summary(snapshot.bars_20s, label="20s"),
                "1m": self._timeframe_summary(snapshot.bars_1m, label="1m"),
                "5m": self._timeframe_summary(snapshot.bars_5m, label="5m"),
            },
            "tickets": tickets,
            "feedback": self._feedback_payload(reflections=reflections, lessons=lessons),
            "risk_posture": risk_posture,
        }

    def structure_break_detected(
        self,
        *,
        snapshot: MT5V51BridgeSnapshot,
        ticket: MT5V51TicketRecord,
    ) -> bool:
        twenty = self._timeframe_summary(snapshot.bars_20s, label="20s")
        one = self._timeframe_summary(snapshot.bars_1m, label="1m")
        five = self._timeframe_summary(snapshot.bars_5m, label="5m")
        if ticket.side == "long":
            return (
                float(twenty.get("ema_gap_bps", 0.0)) < 0.0
                and float(one.get("ema_gap_bps", 0.0)) < 0.0
                and float(five.get("ema_gap_bps", 0.0)) < 0.0
            )
        return (
            float(twenty.get("ema_gap_bps", 0.0)) > 0.0
            and float(one.get("ema_gap_bps", 0.0)) > 0.0
            and float(five.get("ema_gap_bps", 0.0)) > 0.0
        )

    def _ticket_payload(self, *, ticket: MT5V51TicketRecord, allowed_actions: list[str]) -> dict[str, object]:
        return {
            "ticket_id": ticket.ticket_id,
            "side": ticket.side,
            "current_volume_lots": float(ticket.current_volume_lots),
            "original_volume_lots": float(ticket.original_volume_lots),
            "open_price": float(ticket.open_price),
            "current_price": float(ticket.current_price),
            "stop_loss": float(ticket.stop_loss) if ticket.stop_loss is not None else None,
            "take_profit": float(ticket.take_profit) if ticket.take_profit is not None else None,
            "risk_amount_usd": float(ticket.risk_amount_usd),
            "unrealized_pnl_usd": float(ticket.unrealized_pnl_usd),
            "unrealized_r": ticket.unrealized_r,
            "partial_stage": ticket.partial_stage,
            "basket_id": ticket.basket_id,
            "allowed_actions": allowed_actions,
            "context_signature": ticket.context_signature,
            "thesis_tags": ticket.thesis_tags,
        }

    def _feedback_payload(
        self,
        *,
        reflections: Sequence[TradeReflection],
        lessons: Sequence[LessonRecord],
        context_signature: str | None = None,
    ) -> dict[str, object]:
        recent_reflections = list(reflections)[-4:]
        recent_sources = {reflection.reflection_id for reflection in recent_reflections}
        avoid: list[str] = []
        reinforce: list[str] = []
        for lesson in reversed(list(lessons)):
            if recent_sources and lesson.source not in recent_sources:
                continue
            lesson_signature = str(lesson.metadata.get("context_signature", "")).strip()
            if context_signature is not None:
                if not lesson_signature or lesson_signature != context_signature:
                    continue
            polarity = str(lesson.metadata.get("polarity", ""))
            lesson_tags = self._lesson_tags(lesson=lesson)
            if polarity == "avoid":
                for tag in lesson_tags:
                    if tag not in avoid:
                        avoid.append(tag)
            if polarity == "reinforce":
                for tag in lesson_tags:
                    if tag not in reinforce:
                        reinforce.append(tag)
            if len(avoid) >= 3 and len(reinforce) >= 3:
                break
        return {
            "recent_outcomes": [
                {
                    "side": reflection.side,
                    "outcome": self._trade_outcome(reflection),
                    "exit_reason": reflection.exit_reason,
                    "thesis_tags": list(reflection.thesis_tags[:2]),
                }
                for reflection in recent_reflections
            ],
            "avoid_tags": avoid[:3],
            "reinforce_tags": reinforce[:3],
        }

    def _timeframe_summary(self, bars: Sequence[MT5V51Bar], *, label: str) -> dict[str, object]:
        if label == "20s":
            return self._scalp_summary(
                bars,
                label=label,
                fast_period=8,
                slow_period=21,
                atr_period=10,
                breakout_lookback=12,
                return_windows=(1, 3, 6),
            )
        if label == "1m":
            return self._scalp_summary(
                bars,
                label=label,
                fast_period=9,
                slow_period=21,
                atr_period=14,
                breakout_lookback=20,
                return_windows=(1, 3, 5),
            )
        return self._trend_summary(
            bars,
            label=label,
            fast_period=9,
            slow_period=21,
            atr_period=14,
            return_windows=(1, 3),
        )

    def _scalp_summary(
        self,
        bars: Sequence[MT5V51Bar],
        *,
        label: str,
        fast_period: int,
        slow_period: int,
        atr_period: int,
        breakout_lookback: int,
        return_windows: tuple[int, int, int],
    ) -> dict[str, object]:
        closed = [bar for bar in bars if bar.complete]
        if not closed:
            return {"label": label, "samples": 0}
        closes = [float(bar.close_price) for bar in closed]
        highs = [float(bar.high_price) for bar in closed]
        lows = [float(bar.low_price) for bar in closed]
        tick_volumes = [float(bar.tick_volume) for bar in closed]
        current = closes[-1]
        fast_ema = self._ema(closes, fast_period)
        slow_ema = self._ema(closes, slow_period)
        atr_price = self._atr(closed, atr_period)
        latest = closed[-1]
        summary = {
            "label": label,
            "samples": len(closed),
            "latest_close": self._round(current),
            f"return_{return_windows[0]}_bps": self._round(self._return_bps(closes, return_windows[0])),
            f"return_{return_windows[1]}_bps": self._round(self._return_bps(closes, return_windows[1])),
            f"return_{return_windows[2]}_bps": self._round(self._return_bps(closes, return_windows[2])),
            f"ema_{fast_period}": self._round(fast_ema),
            f"ema_{slow_period}": self._round(slow_ema),
            "ema_gap_bps": self._round(self._distance_bps(fast_ema, slow_ema)),
            f"atr_{atr_period}_bps": self._round(self._price_distance_bps(atr_price, current)),
            f"breakout_distance_{breakout_lookback}_bps": self._round(
                self._breakout_distance(closes, highs, lows, breakout_lookback)
            ),
            "direction": self._bar_direction(latest),
            "close_range_position": self._round(self._close_range_position(latest)),
            "body_pct": self._round(self._body_pct(latest)),
            "latest_range_vs_atr": self._round(self._range_vs_atr(latest, atr_price)),
            "tick_volume_ratio": self._round(self._tick_volume_ratio(tick_volumes)),
            "consecutive_bull_closes": self._consecutive_closes(closes, direction="bull"),
            "consecutive_bear_closes": self._consecutive_closes(closes, direction="bear"),
            "strong_bull_bars_last_3": self._strong_bar_count(closed, atr_price=atr_price, direction="bull", lookback=3),
            "strong_bear_bars_last_3": self._strong_bar_count(closed, atr_price=atr_price, direction="bear", lookback=3),
            "consecutive_strong_bull_bars": self._consecutive_strong_bars(closed, atr_price=atr_price, direction="bull"),
            "consecutive_strong_bear_bars": self._consecutive_strong_bars(closed, atr_price=atr_price, direction="bear"),
        }
        summary["long_continuation_score"] = self._continuation_score(summary, direction="bull")
        summary["short_continuation_score"] = self._continuation_score(summary, direction="bear")
        summary["long_pause_after_impulse_ready"] = self._pause_after_impulse_ready(summary, direction="bull")
        summary["short_pause_after_impulse_ready"] = self._pause_after_impulse_ready(summary, direction="bear")
        summary["long_continuation_ready"] = self._continuation_ready(summary, direction="bull")
        summary["short_continuation_ready"] = self._continuation_ready(summary, direction="bear")
        summary["long_trigger_ready"] = self._trigger_ready(summary, direction="bull") or bool(summary["long_continuation_ready"])
        summary["short_trigger_ready"] = self._trigger_ready(summary, direction="bear") or bool(summary["short_continuation_ready"])
        return summary

    def _trend_summary(
        self,
        bars: Sequence[MT5V51Bar],
        *,
        label: str,
        fast_period: int,
        slow_period: int,
        atr_period: int,
        return_windows: tuple[int, int],
    ) -> dict[str, object]:
        closed = [bar for bar in bars if bar.complete]
        if not closed:
            return {"label": label, "samples": 0}
        closes = [float(bar.close_price) for bar in closed]
        current = closes[-1]
        fast_ema = self._ema(closes, fast_period)
        slow_ema = self._ema(closes, slow_period)
        atr_price = self._atr(closed, atr_period)
        return {
            "label": label,
            "samples": len(closed),
            "latest_close": self._round(current),
            f"return_{return_windows[0]}_bps": self._round(self._return_bps(closes, return_windows[0])),
            f"return_{return_windows[1]}_bps": self._round(self._return_bps(closes, return_windows[1])),
            f"ema_{fast_period}": self._round(fast_ema),
            f"ema_{slow_period}": self._round(slow_ema),
            "ema_gap_bps": self._round(self._distance_bps(fast_ema, slow_ema)),
            f"atr_{atr_period}_bps": self._round(self._price_distance_bps(atr_price, current)),
        }

    def _recent_bar_window(self, bars: Sequence[MT5V51Bar], *, limit: int) -> list[dict[str, object]]:
        window = [bar for bar in bars if bar.complete][-limit:]
        return [
            {
                "end_at": bar.end_at.isoformat(),
                "open": self._round(float(bar.open_price)),
                "high": self._round(float(bar.high_price)),
                "low": self._round(float(bar.low_price)),
                "close": self._round(float(bar.close_price)),
                "tick_volume": int(bar.tick_volume),
                "spread_bps": self._round(bar.spread_bps),
            }
            for bar in window
        ]

    def _swing_distance_payload(
        self,
        bars: Sequence[MT5V51Bar],
        *,
        lookback: int,
        label: str,
    ) -> dict[str, object]:
        closed = [bar for bar in bars if bar.complete]
        if not closed:
            return {
                f"distance_to_swing_high_{lookback}_bps": None,
                f"distance_to_swing_low_{lookback}_bps": None,
                "label": label,
            }
        window = closed[-lookback:]
        current = float(window[-1].close_price)
        swing_high = max(float(bar.high_price) for bar in window)
        swing_low = min(float(bar.low_price) for bar in window)
        return {
            "label": label,
            f"distance_to_swing_high_{lookback}_bps": self._round(self._distance_bps(current, swing_high)),
            f"distance_to_swing_low_{lookback}_bps": self._round(self._distance_bps(current, swing_low)),
        }

    def _context_signature(
        self,
        *,
        twenty: dict[str, object],
        one: dict[str, object],
        five: dict[str, object],
        spread_bps: float | None,
    ) -> str:
        spread_bucket = "tight" if spread_bps is None or spread_bps <= 6 else "wide"
        return "|".join(
            [
                self._trend_bucket(float(twenty.get("ema_gap_bps", 0.0))),
                self._trend_bucket(float(one.get("ema_gap_bps", 0.0))),
                self._trend_bucket(float(five.get("ema_gap_bps", 0.0))),
                spread_bucket,
            ]
        )

    def _trend_regime_payload(
        self,
        *,
        twenty: dict[str, object],
        one: dict[str, object],
        five: dict[str, object],
    ) -> dict[str, object]:
        bull_quality = self._directional_quality_score(one=one, twenty=twenty, direction="bull")
        bear_quality = self._directional_quality_score(one=one, twenty=twenty, direction="bear")
        bull_alignment = self._alignment_score(twenty=twenty, five=five, direction="bull")
        bear_alignment = self._alignment_score(twenty=twenty, five=five, direction="bear")
        chop_score = self._chop_score(one=one, twenty=twenty)
        quality_gap = abs(bull_quality - bear_quality)

        if bull_quality >= bear_quality and bull_quality >= 8 and quality_gap >= 2 and chop_score <= 3:
            market_state = "strong_bull" if bull_quality >= 11 else "bullish_continuation"
            return {
                "primary_direction": "bull",
                "market_state": market_state,
                "tradeable": True,
                "entry_style": self._entry_style(one=one, direction="bull"),
                "trend_quality_score": bull_quality,
                "alignment_score": bull_alignment,
                "chop_score": chop_score,
                "bull_quality_score": bull_quality,
                "bear_quality_score": bear_quality,
            }
        if bear_quality >= bull_quality and bear_quality >= 8 and quality_gap >= 2 and chop_score <= 3:
            market_state = "strong_bear" if bear_quality >= 11 else "bearish_continuation"
            return {
                "primary_direction": "bear",
                "market_state": market_state,
                "tradeable": True,
                "entry_style": self._entry_style(one=one, direction="bear"),
                "trend_quality_score": bear_quality,
                "alignment_score": bear_alignment,
                "chop_score": chop_score,
                "bull_quality_score": bull_quality,
                "bear_quality_score": bear_quality,
            }
        return {
            "primary_direction": "flat",
            "market_state": "choppy" if chop_score >= 4 else "mixed",
            "tradeable": False,
            "entry_style": "none",
            "trend_quality_score": max(bull_quality, bear_quality),
            "alignment_score": max(bull_alignment, bear_alignment),
            "chop_score": chop_score,
            "bull_quality_score": bull_quality,
            "bear_quality_score": bear_quality,
        }

    def _directional_quality_score(
        self,
        *,
        one: dict[str, object],
        twenty: dict[str, object],
        direction: str,
    ) -> int:
        is_bull = direction == "bull"
        prefix = "long" if is_bull else "short"
        consecutive_closes = int(one.get(f"consecutive_{'bull' if is_bull else 'bear'}_closes", 0))
        strong_last_three = int(one.get(f"strong_{'bull' if is_bull else 'bear'}_bars_last_3", 0))
        consecutive_strong = int(one.get(f"consecutive_strong_{'bull' if is_bull else 'bear'}_bars", 0))
        pause_ready = bool(one.get(f"{prefix}_pause_after_impulse_ready", False))
        score = 0
        if bool(one.get(f"{prefix}_trigger_ready", False)):
            score += 3
        if bool(one.get(f"{prefix}_continuation_ready", False)):
            score += 3
        if pause_ready:
            score += 2
        if consecutive_closes >= 2:
            score += 1
        if consecutive_closes >= 3:
            score += 2
        if strong_last_three >= 1:
            score += 1
        if consecutive_strong >= 1:
            score += 1
        if self._directional_metric(one.get("return_3_bps"), direction=direction) > 0:
            score += 1
        if self._directional_metric(one.get("return_5_bps"), direction=direction) > 0:
            score += 1
        if self._directional_metric(one.get("ema_gap_bps"), direction=direction) > 0:
            score += 1
        close_range_position = float(one.get("close_range_position", 0.5))
        if pause_ready or (is_bull and close_range_position >= 0.55) or (not is_bull and close_range_position <= 0.45):
            score += 1
        body_pct = float(one.get("body_pct", 0.0))
        if pause_ready or body_pct >= 0.35:
            score += 1
        latest_range_vs_atr = float(one.get("latest_range_vs_atr", 0.0))
        if pause_ready or latest_range_vs_atr >= 0.18:
            score += 1
        if not self._micro_aggressive_opposition(twenty=twenty, direction=direction):
            score += 1
        return score

    def _alignment_score(
        self,
        *,
        twenty: dict[str, object],
        five: dict[str, object],
        direction: str,
    ) -> int:
        is_bull = direction == "bull"
        prefix = "long" if is_bull else "short"
        score = 0
        if not self._micro_aggressive_opposition(twenty=twenty, direction=direction):
            score += 1
        twenty_direction = str(twenty.get("direction", "flat"))
        twenty_ema_gap = float(twenty.get("ema_gap_bps", 0.0))
        if (
            bool(twenty.get(f"{prefix}_trigger_ready", False))
            or bool(twenty.get(f"{prefix}_continuation_ready", False))
            or (is_bull and (twenty_direction == "bull" or twenty_ema_gap > 0))
            or ((not is_bull) and (twenty_direction == "bear" or twenty_ema_gap < 0))
        ):
            score += 2
        five_ema_gap = self._directional_metric(five.get("ema_gap_bps"), direction=direction)
        five_return_3 = self._directional_metric(five.get("return_3_bps"), direction=direction)
        if five_ema_gap > 0:
            score += 1
        if five_return_3 > 0:
            score += 1
        return score

    def _chop_score(
        self,
        *,
        one: dict[str, object],
        twenty: dict[str, object],
    ) -> int:
        score = 0
        if abs(float(one.get("ema_gap_bps", 0.0))) < 1.5:
            score += 2
        if abs(float(one.get("return_3_bps", 0.0))) < 4.0:
            score += 2
        if abs(float(one.get("return_5_bps", 0.0))) < 6.0:
            score += 1
        if max(int(one.get("consecutive_bull_closes", 0)), int(one.get("consecutive_bear_closes", 0))) < 2:
            score += 1
        if float(one.get("body_pct", 0.0)) < 0.35:
            score += 1
        if float(one.get("latest_range_vs_atr", 0.0)) < 0.18:
            score += 1
        if str(one.get("direction", "flat")) == "flat":
            score += 1
        if abs(float(twenty.get("ema_gap_bps", 0.0))) < 1.0 and str(twenty.get("direction", "flat")) == "flat":
            score += 1
        if (
            max(int(twenty.get("consecutive_bull_closes", 0)), int(twenty.get("consecutive_bear_closes", 0))) < 2
            and not bool(twenty.get("long_trigger_ready", False))
            and not bool(twenty.get("short_trigger_ready", False))
            and not bool(twenty.get("long_continuation_ready", False))
            and not bool(twenty.get("short_continuation_ready", False))
        ):
            score += 1
        return score

    def _entry_style(self, *, one: dict[str, object], direction: str) -> str:
        prefix = "long" if direction == "bull" else "short"
        if bool(one.get(f"{prefix}_pause_after_impulse_ready", False)):
            return "pause_after_impulse"
        strong_count = int(one.get(f"strong_{'bull' if direction == 'bull' else 'bear'}_bars_last_3", 0))
        consecutive_strong = int(one.get(f"consecutive_strong_{'bull' if direction == 'bull' else 'bear'}_bars", 0))
        if strong_count >= 2 or consecutive_strong >= 2:
            return "impulse_breakout"
        if bool(one.get(f"{prefix}_continuation_ready", False)):
            return "stair_step_continuation"
        if bool(one.get(f"{prefix}_trigger_ready", False)):
            return "breakout"
        return "none"

    def _micro_aggressive_opposition(self, *, twenty: dict[str, object], direction: str) -> bool:
        if direction == "bull":
            return bool(twenty.get("short_trigger_ready", False)) or (
                str(twenty.get("direction", "flat")) == "bear"
                and max(
                    int(twenty.get("consecutive_bear_closes", 0)),
                    int(twenty.get("consecutive_strong_bear_bars", 0)),
                )
                >= 2
            )
        return bool(twenty.get("long_trigger_ready", False)) or (
            str(twenty.get("direction", "flat")) == "bull"
            and max(
                int(twenty.get("consecutive_bull_closes", 0)),
                int(twenty.get("consecutive_strong_bull_bars", 0)),
            )
            >= 2
        )

    def _directional_metric(self, value: object, *, direction: str) -> float:
        numeric = float(value) if isinstance(value, (int, float)) else 0.0
        return numeric if direction == "bull" else -numeric

    def _trend_bucket(self, value: float) -> str:
        if value > 0:
            return "bull"
        if value < 0:
            return "bear"
        return "flat"

    def _ema(self, values: Sequence[float], period: int) -> float:
        if not values:
            return 0.0
        multiplier = 2.0 / (period + 1)
        ema = values[0]
        for value in values[1:]:
            ema = ((value - ema) * multiplier) + ema
        return ema

    def _atr(self, bars: Sequence[MT5V51Bar], period: int) -> float:
        window = list(bars)[-period:]
        if len(window) < 2:
            return 0.0
        true_ranges: list[float] = []
        previous_close = float(window[0].close_price)
        for bar in window[1:]:
            high = float(bar.high_price)
            low = float(bar.low_price)
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
            true_ranges.append(true_range)
            previous_close = float(bar.close_price)
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    def _breakout_distance(self, closes: Sequence[float], highs: Sequence[float], lows: Sequence[float], lookback: int) -> float:
        if len(closes) < lookback:
            return 0.0
        current = closes[-1]
        highest = max(highs[-lookback:])
        lowest = min(lows[-lookback:])
        if current >= highest:
            return self._distance_bps(current, highest)
        if current <= lowest:
            return self._distance_bps(current, lowest)
        midpoint = (highest + lowest) / 2.0
        return self._distance_bps(current, midpoint)

    def _tick_volume_ratio(self, values: Sequence[float]) -> float:
        if len(values) < 6:
            return 1.0
        baseline = sum(values[-6:-1]) / 5.0
        if baseline <= 0:
            return 1.0
        return values[-1] / baseline

    def _return_bps(self, values: Sequence[float], lookback: int) -> float:
        if len(values) <= lookback:
            return 0.0
        start = values[-(lookback + 1)]
        end = values[-1]
        return self._distance_bps(end, start)

    def _distance_bps(self, current: float, reference: float) -> float:
        if reference == 0:
            return 0.0
        return ((current - reference) / reference) * 10000.0

    def _price_distance_bps(self, distance: float, reference: float) -> float:
        if reference == 0:
            return 0.0
        return (distance / reference) * 10000.0

    def _close_range_position(self, bar: MT5V51Bar) -> float:
        high = float(bar.high_price)
        low = float(bar.low_price)
        close = float(bar.close_price)
        total_range = high - low
        if total_range <= 0:
            return 0.5
        return (close - low) / total_range

    def _body_pct(self, bar: MT5V51Bar) -> float:
        total_range = float(bar.high_price - bar.low_price)
        if total_range <= 0:
            return 0.0
        return abs(float(bar.close_price - bar.open_price)) / total_range

    def _bar_direction(self, bar: MT5V51Bar) -> str:
        close = float(bar.close_price)
        open_price = float(bar.open_price)
        if close > open_price:
            return "bull"
        if close < open_price:
            return "bear"
        return "flat"

    def _range_vs_atr(self, bar: MT5V51Bar, atr_price: float) -> float:
        if atr_price <= 0:
            return 0.0
        bar_range = float(bar.high_price - bar.low_price)
        return bar_range / atr_price

    def _consecutive_closes(self, closes: Sequence[float], *, direction: str) -> int:
        if len(closes) < 2:
            return 0
        count = 0
        for previous, current in zip(reversed(closes[:-1]), reversed(closes[1:])):
            if direction == "bull" and current > previous:
                count += 1
                continue
            if direction == "bear" and current < previous:
                count += 1
                continue
            break
        return count

    def _strong_bar_direction(self, bar: MT5V51Bar, *, atr_price: float) -> str:
        direction = self._bar_direction(bar)
        if direction == "flat":
            return "flat"
        body_pct = self._body_pct(bar)
        close_range_position = self._close_range_position(bar)
        range_vs_atr = self._range_vs_atr(bar, atr_price)
        has_expansion = atr_price <= 0 or range_vs_atr >= 0.55
        if not has_expansion or body_pct < 0.45:
            return "flat"
        if direction == "bull" and close_range_position >= 0.62:
            return "bull"
        if direction == "bear" and close_range_position <= 0.38:
            return "bear"
        return "flat"

    def _strong_bar_count(
        self,
        bars: Sequence[MT5V51Bar],
        *,
        atr_price: float,
        direction: str,
        lookback: int,
    ) -> int:
        count = 0
        for bar in list(bars)[-lookback:]:
            if self._strong_bar_direction(bar, atr_price=atr_price) == direction:
                count += 1
        return count

    def _consecutive_strong_bars(
        self,
        bars: Sequence[MT5V51Bar],
        *,
        atr_price: float,
        direction: str,
    ) -> int:
        count = 0
        for bar in reversed(list(bars)):
            if self._strong_bar_direction(bar, atr_price=atr_price) == direction:
                count += 1
                continue
            break
        return count

    def _trigger_ready(self, summary: dict[str, object], *, direction: str) -> bool:
        summary_direction = str(summary.get("direction", "flat"))
        if summary_direction != direction:
            return False
        if direction == "bull":
            strong_last_three = int(summary.get("strong_bull_bars_last_3", 0))
            consecutive_strong = int(summary.get("consecutive_strong_bull_bars", 0))
            consecutive_closes = int(summary.get("consecutive_bull_closes", 0))
            momentum_ok = float(summary.get("return_1_bps", 0.0)) > 0 and float(summary.get("return_3_bps", 0.0)) >= 0
            close_ok = float(summary.get("close_range_position", 0.5)) >= 0.65
            ema_ok = float(summary.get("ema_gap_bps", 0.0)) > -4.0
        else:
            strong_last_three = int(summary.get("strong_bear_bars_last_3", 0))
            consecutive_strong = int(summary.get("consecutive_strong_bear_bars", 0))
            consecutive_closes = int(summary.get("consecutive_bear_closes", 0))
            momentum_ok = float(summary.get("return_1_bps", 0.0)) < 0 and float(summary.get("return_3_bps", 0.0)) <= 0
            close_ok = float(summary.get("close_range_position", 0.5)) <= 0.35
            ema_ok = float(summary.get("ema_gap_bps", 0.0)) < 4.0
        latest_range_vs_atr = float(summary.get("latest_range_vs_atr", 0.0))
        return (
            strong_last_three >= 2
            or consecutive_strong >= 2
            or (
                consecutive_closes >= 2
                and latest_range_vs_atr >= 0.85
                and close_ok
                and momentum_ok
                and ema_ok
            )
        )

    def _continuation_score(self, summary: dict[str, object], *, direction: str) -> int:
        summary_direction = str(summary.get("direction", "flat"))
        if summary_direction != direction:
            return 0
        if direction == "bull":
            consecutive_closes = int(summary.get("consecutive_bull_closes", 0))
            strong_last_three = int(summary.get("strong_bull_bars_last_3", 0))
            consecutive_strong = int(summary.get("consecutive_strong_bull_bars", 0))
            return_1_bps = float(summary.get("return_1_bps", 0.0))
            return_3_bps = float(summary.get("return_3_bps", 0.0))
            return_5_bps = float(summary.get("return_5_bps", 0.0))
            ema_gap_bps = float(summary.get("ema_gap_bps", 0.0))
            close_range_position = float(summary.get("close_range_position", 0.5))
        else:
            consecutive_closes = int(summary.get("consecutive_bear_closes", 0))
            strong_last_three = int(summary.get("strong_bear_bars_last_3", 0))
            consecutive_strong = int(summary.get("consecutive_strong_bear_bars", 0))
            return_1_bps = float(summary.get("return_1_bps", 0.0))
            return_3_bps = float(summary.get("return_3_bps", 0.0))
            return_5_bps = float(summary.get("return_5_bps", 0.0))
            ema_gap_bps = float(summary.get("ema_gap_bps", 0.0))
            close_range_position = float(summary.get("close_range_position", 0.5))

        body_pct = float(summary.get("body_pct", 0.0))
        latest_range_vs_atr = float(summary.get("latest_range_vs_atr", 0.0))
        score = 0
        if consecutive_closes >= 2:
            score += 1
        if consecutive_closes >= 3:
            score += 2
        if strong_last_three >= 1:
            score += 1
        if consecutive_strong >= 1:
            score += 1
        if direction == "bull":
            if return_1_bps > 0:
                score += 1
            if return_3_bps > 0:
                score += 1
            if return_5_bps > 0:
                score += 1
            if ema_gap_bps > 0:
                score += 1
            if close_range_position >= 0.55:
                score += 1
        else:
            if return_1_bps < 0:
                score += 1
            if return_3_bps < 0:
                score += 1
            if return_5_bps < 0:
                score += 1
            if ema_gap_bps < 0:
                score += 1
            if close_range_position <= 0.45:
                score += 1
        if body_pct >= 0.45:
            score += 1
        if latest_range_vs_atr >= 0.25:
            score += 1
        return score

    def _continuation_ready(self, summary: dict[str, object], *, direction: str) -> bool:
        summary_direction = str(summary.get("direction", "flat"))
        pause_after_impulse_ready = bool(
            summary.get(f"{'long' if direction == 'bull' else 'short'}_pause_after_impulse_ready", False)
        )
        if pause_after_impulse_ready:
            return True
        if summary_direction != direction:
            return False
        score = int(summary.get(f"{'long' if direction == 'bull' else 'short'}_continuation_score", 0))
        latest_range_vs_atr = float(summary.get("latest_range_vs_atr", 0.0))
        body_pct = float(summary.get("body_pct", 0.0))
        if direction == "bull":
            consecutive_closes = int(summary.get("consecutive_bull_closes", 0))
            momentum_ok = float(summary.get("return_3_bps", 0.0)) > 0 and float(summary.get("return_5_bps", 0.0)) > 0
            ema_ok = float(summary.get("ema_gap_bps", 0.0)) > 0
            close_ok = float(summary.get("close_range_position", 0.5)) >= 0.55
        else:
            consecutive_closes = int(summary.get("consecutive_bear_closes", 0))
            momentum_ok = float(summary.get("return_3_bps", 0.0)) < 0 and float(summary.get("return_5_bps", 0.0)) < 0
            ema_ok = float(summary.get("ema_gap_bps", 0.0)) < 0
            close_ok = float(summary.get("close_range_position", 0.5)) <= 0.45
        return (
            consecutive_closes >= 3
            and momentum_ok
            and ema_ok
            and close_ok
            and body_pct >= 0.40
            and latest_range_vs_atr >= 0.20
            and score >= 7
        )

    def _pause_after_impulse_ready(self, summary: dict[str, object], *, direction: str) -> bool:
        summary_direction = str(summary.get("direction", "flat"))
        if summary_direction == direction:
            return False

        latest_range_vs_atr = float(summary.get("latest_range_vs_atr", 0.0))
        if latest_range_vs_atr < 0.0 or latest_range_vs_atr > 0.20:
            return False

        return_1_bps = float(summary.get("return_1_bps", 0.0))
        if direction == "bull":
            return (
                int(summary.get("strong_bull_bars_last_3", 0)) >= 1
                and float(summary.get("return_3_bps", 0.0)) >= 6.0
                and float(summary.get("return_5_bps", 0.0)) > 0.0
                and float(summary.get("ema_gap_bps", 0.0)) >= 2.0
                and return_1_bps >= -2.5
            )
        return (
            int(summary.get("strong_bear_bars_last_3", 0)) >= 1
            and float(summary.get("return_3_bps", 0.0)) <= -6.0
            and float(summary.get("return_5_bps", 0.0)) < 0.0
            and float(summary.get("ema_gap_bps", 0.0)) <= -2.0
            and return_1_bps <= 2.5
        )

    def _trade_outcome(self, reflection: TradeReflection) -> str:
        realized_r = float(reflection.realized_r)
        if abs(realized_r) <= 10.0:
            if realized_r > 0.1:
                return "win"
            if realized_r < -0.1:
                return "loss"
            return "scratch"
        realized_pnl = float(reflection.realized_pnl_usd)
        if realized_pnl > 0:
            return "win"
        if realized_pnl < 0:
            return "loss"
        return "scratch"

    def _lesson_tags(self, *, lesson: LessonRecord) -> list[str]:
        tags: list[str] = []
        metadata_tags = lesson.metadata.get("feedback_tags", [])
        if isinstance(metadata_tags, list):
            tags.extend(self._normalize_tag(tag) for tag in metadata_tags if isinstance(tag, str))
        thesis_tags = lesson.metadata.get("thesis_tags", [])
        if isinstance(thesis_tags, list):
            tags.extend(self._normalize_tag(tag) for tag in thesis_tags if isinstance(tag, str))
        message = lesson.message.lower()
        heuristics = {
            "invalidat": "respect_invalidation",
            "losing": "cut_loser_fast",
            "deep against": "avoid_early_heat",
            "partial": "partial_then_trail",
            "breathe": "let_winner_breathe",
            "does not reverse": "micro_confirm",
            "follow-through": "wait_for_follow_through",
            "repeat": "avoid_repeat_context",
        }
        for needle, tag in heuristics.items():
            if needle in message:
                tags.append(tag)
        if not tags:
            tags.append(self._slugify(lesson.message))
        unique_tags: list[str] = []
        for tag in tags:
            normalized = self._normalize_tag(tag)
            if not normalized or normalized in unique_tags:
                continue
            unique_tags.append(normalized)
        return unique_tags[:2]

    def _normalize_tag(self, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        normalized = re.sub(r"[^a-z0-9_]+", "", normalized)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized[:32]

    def _slugify(self, value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        if not normalized:
            return "feedback_hint"
        return normalized[:32]

    def _round(self, value: float | None, *, digits: int = 4) -> float | None:
        if value is None:
            return None
        return round(float(value), digits)

    def _optional_float(self, value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None
