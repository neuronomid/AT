from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from data.mt5_v60_schemas import (
    MT5V60Bar,
    MT5V60BridgeSnapshot,
    MT5V60ScreenshotState,
    MT5V60TicketRecord,
)
from data.schemas import LessonRecord, TradeReflection
from execution.mt5_v60_ticket_registry import MT5V60TicketRegistry
from runtime.mt5_v60_quote_tape import MT5V60QuoteTape


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MT5V60ContextBuilder:
    def __init__(self, *, quote_tape: MT5V60QuoteTape | None = None) -> None:
        self._quote_tape = quote_tape or MT5V60QuoteTape()

    def observe_snapshot(self, snapshot: MT5V60BridgeSnapshot) -> None:
        self._quote_tape.ingest(snapshot)

    def build_entry_packet(
        self,
        *,
        snapshot: MT5V60BridgeSnapshot,
        registry: MT5V60TicketRegistry,
        screenshot_state: MT5V60ScreenshotState,
        reversal_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.observe_snapshot(snapshot)
        one = self._timeframe_summary(snapshot.bars_1m, label="1m")
        two = self._timeframe_summary(snapshot.bars_2m, label="2m")
        three = self._timeframe_summary(snapshot.bars_3m, label="3m")
        five = self._timeframe_summary(snapshot.bars_5m, label="5m")
        context_signature = self._context_signature(three=three, two=two, five=five, spread_bps=snapshot.spread_bps)
        microstructure = self._quote_tape.build_payload(
            snapshot=snapshot,
            primary_atr_bps=self._optional_float(three.get("atr_14_bps")),
        )
        freshness = {
            "source_snapshot_age_ms": microstructure.pop("source_snapshot_age_ms"),
            "source_snapshot_age_bucket": microstructure.pop("source_snapshot_age_bucket"),
        }
        packet: dict[str, object] = {
            "symbol": snapshot.symbol,
            "server_time": snapshot.server_time.isoformat(),
            "position_state": ("occupied" if registry.has_open_position(snapshot.symbol) else "flat"),
            "quote": {
                "bid": self._round(float(snapshot.bid)),
                "ask": self._round(float(snapshot.ask)),
                "spread_bps": self._round(snapshot.spread_bps),
            },
            "freshness": freshness,
            "microstructure": microstructure,
            "timeframes": {
                "1m": one,
                "2m": two,
                "3m": three,
                "5m": five,
            },
            "recent_bars": {
                "1m": self._recent_bar_window(snapshot.bars_1m, limit=10),
                "2m": self._recent_bar_window(snapshot.bars_2m, limit=10),
                "3m": self._recent_bar_window(snapshot.bars_3m, limit=20),
                "5m": self._recent_bar_window(snapshot.bars_5m, limit=10),
            },
            "levels": {
                "3m": self._swing_distance_payload(snapshot.bars_3m, lookback=20, label="3m"),
                "5m": self._swing_distance_payload(snapshot.bars_5m, lookback=10, label="5m"),
            },
            "trend_regime": self._trend_regime_payload(one=one, two=two, three=three, five=five),
            "context_signature": context_signature,
            "screenshot": self._screenshot_payload(snapshot=snapshot, screenshot_state=screenshot_state, include_cached_visual=False),
        }
        if reversal_context is not None:
            packet["reversal_context"] = reversal_context
        return packet

    def build_manager_packet(
        self,
        *,
        snapshot: MT5V60BridgeSnapshot,
        registry: MT5V60TicketRegistry,
        allowed_actions: dict[str, list[str]],
        risk_posture: str,
        reflections: Sequence[TradeReflection],
        lessons: Sequence[LessonRecord],
        screenshot_state: MT5V60ScreenshotState,
        include_raw_screenshot: bool,
    ) -> dict[str, object]:
        self.observe_snapshot(snapshot)
        tickets = [
            self._ticket_payload(
                ticket=ticket,
                allowed_actions=allowed_actions.get(ticket.ticket_id, ["hold"]),
                spread_bps=snapshot.spread_bps,
            )
            for ticket in registry.all(snapshot.symbol)
        ]
        return {
            "symbol": snapshot.symbol,
            "server_time": snapshot.server_time.isoformat(),
            "quote": {
                "bid": self._round(float(snapshot.bid)),
                "ask": self._round(float(snapshot.ask)),
                "spread_bps": self._round(snapshot.spread_bps),
            },
            "account": {
                "balance": self._round(float(snapshot.account.balance)),
                "equity": self._round(float(snapshot.account.equity)),
                "free_margin": self._round(float(snapshot.account.free_margin)),
                "open_profit": self._round(float(snapshot.account.open_profit)),
            },
            "timeframes": {
                "1m": self._timeframe_summary(snapshot.bars_1m, label="1m"),
                "2m": self._timeframe_summary(snapshot.bars_2m, label="2m"),
                "3m": self._timeframe_summary(snapshot.bars_3m, label="3m"),
                "5m": self._timeframe_summary(snapshot.bars_5m, label="5m"),
            },
            "recent_bars": {
                "1m": self._recent_bar_window(snapshot.bars_1m, limit=10),
                "2m": self._recent_bar_window(snapshot.bars_2m, limit=10),
                "3m": self._recent_bar_window(snapshot.bars_3m, limit=20),
                "5m": self._recent_bar_window(snapshot.bars_5m, limit=10),
            },
            "tickets": tickets,
            "risk_posture": risk_posture,
            "feedback": self._feedback_payload(reflections=reflections, lessons=lessons),
            "manager_context": {
                "image_attached": include_raw_screenshot,
                "screenshot": self._screenshot_payload(
                    snapshot=snapshot,
                    screenshot_state=screenshot_state,
                    include_cached_visual=True,
                ),
            },
        }

    def _timeframe_summary(self, bars: Sequence[MT5V60Bar], *, label: str) -> dict[str, object]:
        closed = [bar for bar in bars if bar.complete]
        if not closed:
            return {"label": label, "samples": 0}
        closes = [float(bar.close_price) for bar in closed]
        highs = [float(bar.high_price) for bar in closed]
        lows = [float(bar.low_price) for bar in closed]
        tick_volumes = [float(bar.tick_volume) for bar in closed]
        current = closes[-1]
        fast_ema = self._ema(closes, 9)
        slow_ema = self._ema(closes, 21)
        atr_price = self._atr(closed, 14)
        latest = closed[-1]
        summary = {
            "label": label,
            "samples": len(closed),
            "latest_close": self._round(current),
            "return_1_bps": self._round(self._return_bps(closes, 1)),
            "return_3_bps": self._round(self._return_bps(closes, 3)),
            "return_5_bps": self._round(self._return_bps(closes, 5)),
            "ema_9": self._round(fast_ema),
            "ema_21": self._round(slow_ema),
            "ema_gap_bps": self._round(self._distance_bps(fast_ema, slow_ema)),
            "atr_14_bps": self._round(self._price_distance_bps(atr_price, current)),
            "direction": self._bar_direction(latest),
            "close_range_position": self._round(self._close_range_position(latest)),
            "body_pct": self._round(self._body_pct(latest)),
            "latest_range_vs_atr": self._round(self._range_vs_atr(latest, atr_price)),
            "tick_volume_ratio": self._round(self._tick_volume_ratio(tick_volumes)),
            "consecutive_bull_closes": self._consecutive_closes(closes, direction="bull"),
            "consecutive_bear_closes": self._consecutive_closes(closes, direction="bear"),
        }
        summary["chop_score"] = self._timeframe_chop_score(summary)
        return summary

    def _recent_bar_window(self, bars: Sequence[MT5V60Bar], *, limit: int) -> list[dict[str, object]]:
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

    def _swing_distance_payload(self, bars: Sequence[MT5V60Bar], *, lookback: int, label: str) -> dict[str, object]:
        closed = [bar for bar in bars if bar.complete]
        if not closed:
            return {"label": label}
        window = closed[-lookback:]
        current = float(window[-1].close_price)
        swing_high = max(float(bar.high_price) for bar in window)
        swing_low = min(float(bar.low_price) for bar in window)
        return {
            "label": label,
            f"distance_to_swing_high_{lookback}_bps": self._round(self._distance_bps(current, swing_high)),
            f"distance_to_swing_low_{lookback}_bps": self._round(self._distance_bps(current, swing_low)),
        }

    def _trend_regime_payload(
        self,
        *,
        one: dict[str, object],
        two: dict[str, object],
        three: dict[str, object],
        five: dict[str, object],
    ) -> dict[str, object]:
        bull_quality = self._directional_quality_score(primary=three, support=two, backdrop=five, direction="bull")
        bear_quality = self._directional_quality_score(primary=three, support=two, backdrop=five, direction="bear")
        alignment = self._alignment_score(one=one, two=two, three=three, five=five)
        chop_score = int(max(self._optional_float(three.get("chop_score")), self._optional_float(two.get("chop_score"))))
        if bull_quality >= 7 and bull_quality > bear_quality + 1 and chop_score <= 3:
            return {
                "primary_direction": "bull",
                "market_state": "bullish_trend" if bull_quality >= 9 else "bullish_continuation",
                "tradeable": True,
                "entry_style": self._entry_style(three=three, support=two, direction="bull"),
                "trend_quality_score": bull_quality,
                "alignment_score": alignment,
                "chop_score": chop_score,
            }
        if bear_quality >= 7 and bear_quality > bull_quality + 1 and chop_score <= 3:
            return {
                "primary_direction": "bear",
                "market_state": "bearish_trend" if bear_quality >= 9 else "bearish_continuation",
                "tradeable": True,
                "entry_style": self._entry_style(three=three, support=two, direction="bear"),
                "trend_quality_score": bear_quality,
                "alignment_score": alignment,
                "chop_score": chop_score,
            }
        return {
            "primary_direction": "flat",
            "market_state": "choppy" if chop_score >= 4 else "mixed",
            "tradeable": False,
            "entry_style": "none",
            "trend_quality_score": max(bull_quality, bear_quality),
            "alignment_score": alignment,
            "chop_score": chop_score,
        }

    def _directional_quality_score(
        self,
        *,
        primary: dict[str, object],
        support: dict[str, object],
        backdrop: dict[str, object],
        direction: str,
    ) -> int:
        score = 0
        if self._directional_metric(primary.get("ema_gap_bps"), direction=direction) > 0:
            score += 2
        if self._directional_metric(primary.get("return_3_bps"), direction=direction) > 0:
            score += 2
        if self._directional_metric(primary.get("return_5_bps"), direction=direction) > 0:
            score += 1
        if self._directional_metric(support.get("ema_gap_bps"), direction=direction) > 0:
            score += 1
        if self._directional_metric(support.get("return_3_bps"), direction=direction) > 0:
            score += 1
        if self._directional_metric(backdrop.get("ema_gap_bps"), direction=direction) > 0:
            score += 1
        if str(primary.get("direction")) == ("bull" if direction == "bull" else "bear"):
            score += 1
        closes_key = "consecutive_bull_closes" if direction == "bull" else "consecutive_bear_closes"
        if int(primary.get(closes_key, 0)) >= 2:
            score += 1
        return score

    def _alignment_score(
        self,
        *,
        one: dict[str, object],
        two: dict[str, object],
        three: dict[str, object],
        five: dict[str, object],
    ) -> int:
        score = 0
        signs = []
        for payload in (one, two, three, five):
            gap = self._optional_float(payload.get("ema_gap_bps"))
            if gap > 0:
                signs.append(1)
            elif gap < 0:
                signs.append(-1)
            else:
                signs.append(0)
        if signs.count(1) >= 3 or signs.count(-1) >= 3:
            score += 2
        if signs[2] == signs[3] and signs[2] != 0:
            score += 2
        if signs[1] == signs[2] and signs[2] != 0:
            score += 1
        return score

    def _entry_style(self, *, three: dict[str, object], support: dict[str, object], direction: str) -> str:
        crp = self._optional_float(three.get("close_range_position"), 0.5)
        body = self._optional_float(three.get("body_pct"))
        support_return = self._directional_metric(support.get("return_1_bps"), direction=direction)
        if body >= 0.6 and ((direction == "bull" and crp >= 0.65) or (direction == "bear" and crp <= 0.35)):
            return "impulse"
        if support_return > 0:
            return "continuation"
        return "pullback"

    def _ticket_payload(self, *, ticket: MT5V60TicketRecord, allowed_actions: list[str], spread_bps: float | None) -> dict[str, object]:
        current_reward_to_tp_r: float | None = None
        current_risk_to_sl_r: float | None = None
        max_favorable_r: float | None = None
        drawdown_from_peak_r: float | None = None
        volume_remaining_fraction: float | None = None
        stop_at_or_better_than_breakeven: bool | None = None
        if ticket.take_profit is not None and ticket.r_distance_price > 0:
            if ticket.side == "long":
                current_reward_to_tp_r = max(float((ticket.take_profit - ticket.current_price) / ticket.r_distance_price), 0.0)
            else:
                current_reward_to_tp_r = max(float((ticket.current_price - ticket.take_profit) / ticket.r_distance_price), 0.0)
        if ticket.stop_loss is not None and ticket.r_distance_price > 0:
            if ticket.side == "long":
                current_risk_to_sl_r = max(float((ticket.current_price - ticket.stop_loss) / ticket.r_distance_price), 0.0)
            else:
                current_risk_to_sl_r = max(float((ticket.stop_loss - ticket.current_price) / ticket.r_distance_price), 0.0)
            stop_at_or_better_than_breakeven = (
                ticket.stop_loss >= ticket.open_price if ticket.side == "long" else ticket.stop_loss <= ticket.open_price
            )
        if ticket.r_distance_price > 0:
            if ticket.side == "long":
                max_favorable_r = max(float((ticket.highest_favorable_close - ticket.open_price) / ticket.r_distance_price), 0.0)
            else:
                max_favorable_r = max(float((ticket.open_price - ticket.lowest_favorable_close) / ticket.r_distance_price), 0.0)
            drawdown_from_peak_r = max(self._optional_float(max_favorable_r) - ticket.unrealized_r, 0.0)
        if ticket.original_volume_lots > 0:
            volume_remaining_fraction = float(ticket.current_volume_lots / ticket.original_volume_lots)
        return {
            "ticket_id": ticket.ticket_id,
            "side": ticket.side,
            "current_volume_lots": float(ticket.current_volume_lots),
            "original_volume_lots": float(ticket.original_volume_lots),
            "volume_remaining_fraction": self._round(volume_remaining_fraction),
            "open_price": float(ticket.open_price),
            "current_price": float(ticket.current_price),
            "stop_loss": float(ticket.stop_loss) if ticket.stop_loss is not None else None,
            "take_profit": float(ticket.take_profit) if ticket.take_profit is not None else None,
            "initial_stop_loss": float(ticket.initial_stop_loss),
            "initial_take_profit": float(ticket.hard_take_profit),
            "risk_amount_usd": float(ticket.risk_amount_usd),
            "unrealized_pnl_usd": float(ticket.unrealized_pnl_usd),
            "unrealized_r": ticket.unrealized_r,
            "max_favorable_r": self._round(max_favorable_r),
            "drawdown_from_peak_r": self._round(drawdown_from_peak_r),
            "analysis_mode": ticket.analysis_mode,
            "spread_bps": self._round(spread_bps),
            "current_reward_to_tp_r": self._round(current_reward_to_tp_r),
            "current_risk_to_sl_r": self._round(current_risk_to_sl_r),
            "highest_favorable_close": float(ticket.highest_favorable_close),
            "lowest_favorable_close": float(ticket.lowest_favorable_close),
            "partial_stage": ticket.partial_stage,
            "entry_submitted_without_broker_protection": bool(ticket.metadata.get("entry_submitted_without_broker_protection")),
            "first_protection_attached": ticket.first_protection_attached,
            "first_protection_review_pending": ticket.first_protection_review_pending,
            "stop_at_or_better_than_breakeven": stop_at_or_better_than_breakeven,
            "basket_id": ticket.basket_id,
            "allowed_actions": allowed_actions,
            "context_signature": ticket.context_signature,
            "thesis_tags": ticket.thesis_tags,
        }

    def _feedback_payload(self, *, reflections: Sequence[TradeReflection], lessons: Sequence[LessonRecord]) -> dict[str, object]:
        recent_reflections = list(reflections)[-4:]
        return {
            "recent_outcomes": [
                {
                    "side": reflection.side,
                    "outcome": "win" if reflection.realized_pnl_usd > 0 else "loss" if reflection.realized_pnl_usd < 0 else "flat",
                    "exit_reason": reflection.exit_reason,
                    "thesis_tags": list(reflection.thesis_tags[:2]),
                }
                for reflection in recent_reflections
            ],
            "recent_lesson_tags": [str(lesson.metadata.get("feedback_tags", [])) for lesson in list(lessons)[-3:]],
        }

    def _screenshot_payload(
        self,
        *,
        snapshot: MT5V60BridgeSnapshot,
        screenshot_state: MT5V60ScreenshotState,
        include_cached_visual: bool,
    ) -> dict[str, object]:
        capture_time = _ensure_utc(snapshot.chart_screenshot.captured_at)
        server_time = _ensure_utc(snapshot.server_time)
        age_seconds = None
        if capture_time is not None and server_time is not None:
            age_seconds = max(0.0, (server_time - capture_time).total_seconds())
        payload: dict[str, object] = {
            "relative_path": snapshot.chart_screenshot.relative_path,
            "absolute_path": screenshot_state.absolute_path,
            "fingerprint": snapshot.chart_screenshot.fingerprint,
            "captured_at": snapshot.chart_screenshot.captured_at.isoformat() if snapshot.chart_screenshot.captured_at is not None else None,
            "chart_timeframe": snapshot.chart_screenshot.chart_timeframe,
            "capture_ok": snapshot.chart_screenshot.capture_ok,
            "message": snapshot.chart_screenshot.message,
            "age_seconds": self._round(age_seconds),
        }
        if include_cached_visual:
            cached_capture_time = _ensure_utc(screenshot_state.cached_visual_context_capture_ts)
            cached_age_seconds = None
            if cached_capture_time is not None and server_time is not None:
                cached_age_seconds = max(0.0, (server_time - cached_capture_time).total_seconds())
            payload["cached_visual_context"] = screenshot_state.cached_visual_context
            payload["cached_visual_context_age_seconds"] = self._round(cached_age_seconds)
            payload["last_manager_image_sent_fingerprint"] = screenshot_state.last_manager_image_sent_fingerprint
        return payload

    def _context_signature(self, *, three: dict[str, object], two: dict[str, object], five: dict[str, object], spread_bps: float | None) -> str:
        spread_bucket = "tight" if spread_bps is None or spread_bps <= 8 else "wide"
        return "|".join(
            [
                self._trend_bucket(self._optional_float(three.get("ema_gap_bps"))),
                self._trend_bucket(self._optional_float(two.get("ema_gap_bps"))),
                self._trend_bucket(self._optional_float(five.get("ema_gap_bps"))),
                spread_bucket,
            ]
        )

    def _timeframe_chop_score(self, summary: dict[str, object]) -> int:
        score = 0
        if abs(self._optional_float(summary.get("ema_gap_bps"))) < 1.5:
            score += 2
        if abs(self._optional_float(summary.get("return_3_bps"))) < 4.0:
            score += 1
        if self._optional_float(summary.get("body_pct")) < 0.35:
            score += 1
        if self._optional_float(summary.get("latest_range_vs_atr")) < 0.18:
            score += 1
        if str(summary.get("direction")) == "flat":
            score += 1
        return score

    def _ema(self, values: Sequence[float], period: int) -> float:
        if not values:
            return 0.0
        multiplier = 2.0 / (period + 1)
        ema = values[0]
        for value in values[1:]:
            ema = ((value - ema) * multiplier) + ema
        return ema

    def _atr(self, bars: Sequence[MT5V60Bar], period: int) -> float:
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

    def _price_distance_bps(self, distance: float, current: float) -> float:
        if current == 0:
            return 0.0
        return (distance / current) * 10000.0

    def _close_range_position(self, bar: MT5V60Bar) -> float:
        low = float(bar.low_price)
        high = float(bar.high_price)
        close = float(bar.close_price)
        if high <= low:
            return 0.5
        return min(max((close - low) / (high - low), 0.0), 1.0)

    def _body_pct(self, bar: MT5V60Bar) -> float:
        high = float(bar.high_price)
        low = float(bar.low_price)
        if high <= low:
            return 0.0
        return abs(float(bar.close_price) - float(bar.open_price)) / (high - low)

    def _range_vs_atr(self, bar: MT5V60Bar, atr_price: float) -> float:
        if atr_price <= 0:
            return 0.0
        return (float(bar.high_price) - float(bar.low_price)) / atr_price

    def _tick_volume_ratio(self, values: Sequence[float]) -> float:
        if len(values) < 6:
            return 1.0
        baseline = sum(values[-6:-1]) / 5.0
        if baseline <= 0:
            return 1.0
        return values[-1] / baseline

    def _bar_direction(self, bar: MT5V60Bar) -> str:
        if bar.close_price > bar.open_price:
            return "bull"
        if bar.close_price < bar.open_price:
            return "bear"
        return "flat"

    def _consecutive_closes(self, closes: Sequence[float], *, direction: str) -> int:
        count = 0
        for index in range(len(closes) - 1, 0, -1):
            current = closes[index]
            previous = closes[index - 1]
            if direction == "bull" and current > previous:
                count += 1
                continue
            if direction == "bear" and current < previous:
                count += 1
                continue
            break
        return count

    def _directional_metric(self, value: object, *, direction: str) -> float:
        numeric = self._optional_float(value)
        return numeric if direction == "bull" else -numeric

    def _trend_bucket(self, value: float) -> str:
        if value > 0:
            return "bull"
        if value < 0:
            return "bear"
        return "flat"

    def _optional_float(self, value: object, default: float = 0.0) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return default

    def _round(self, value: float | None, digits: int = 4) -> float | None:
        if value is None:
            return None
        return round(value, digits)
