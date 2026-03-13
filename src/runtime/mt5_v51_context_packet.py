from __future__ import annotations

from collections.abc import Sequence

from data.mt5_v51_schemas import MT5V51Bar, MT5V51BridgeSnapshot, MT5V51TicketRecord
from data.schemas import LessonRecord, TradeReflection
from execution.mt5_v51_ticket_registry import MT5V51TicketRegistry


class MT5V51ContextBuilder:
    def build_entry_packet(
        self,
        *,
        snapshot: MT5V51BridgeSnapshot,
        registry: MT5V51TicketRegistry,
        risk_posture: str,
        reflections: Sequence[TradeReflection],
        lessons: Sequence[LessonRecord],
    ) -> dict[str, object]:
        twenty = self._timeframe_summary(snapshot.bars_20s, label="20s")
        one = self._timeframe_summary(snapshot.bars_1m, label="1m")
        five = self._timeframe_summary(snapshot.bars_5m, label="5m")
        context_signature = self._context_signature(twenty=twenty, one=one, five=five, spread_bps=snapshot.spread_bps)
        return {
            "symbol": snapshot.symbol,
            "server_time": snapshot.server_time.isoformat(),
            "quote": {
                "bid": float(snapshot.bid),
                "ask": float(snapshot.ask),
                "spread_bps": snapshot.spread_bps,
            },
            "symbol_spec": snapshot.symbol_spec.model_dump(mode="json"),
            "account": {
                "balance": float(snapshot.account.balance),
                "equity": float(snapshot.account.equity),
                "free_margin": float(snapshot.account.free_margin),
                "account_mode": snapshot.account.account_mode,
            },
            "open_exposure": {
                "has_position": registry.has_open_position(snapshot.symbol),
                "ticket_count": len(registry.all(snapshot.symbol)),
                "open_risk_usd": float(registry.total_open_risk_usd(snapshot.symbol)),
            },
            "timeframes": {
                "20s": twenty,
                "1m": one,
                "5m": five,
            },
            "feedback": self._feedback_payload(
                reflections=reflections,
                lessons=lessons,
                context_signature=context_signature,
            ),
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

    def preflight_alignment_flipped(self, *, snapshot: MT5V51BridgeSnapshot, action: str) -> bool:
        twenty = self._timeframe_summary(snapshot.bars_20s, label="20s")
        one = self._timeframe_summary(snapshot.bars_1m, label="1m")
        twenty_gap = float(twenty.get("ema_gap_bps", 0.0))
        one_gap = float(one.get("ema_gap_bps", 0.0))
        if action == "enter_long":
            return twenty_gap < 0.0 and one_gap < 0.0
        if action == "enter_short":
            return twenty_gap > 0.0 and one_gap > 0.0
        return False

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
        recent_reflections = list(reflections)[-3:]
        avoid: list[str] = []
        reinforce: list[str] = []
        for lesson in reversed(list(lessons)[-10:]):
            lesson_signature = str(lesson.metadata.get("context_signature", "")).strip()
            if context_signature is not None:
                if not lesson_signature or lesson_signature != context_signature:
                    continue
            polarity = str(lesson.metadata.get("polarity", ""))
            if polarity == "avoid" and lesson.message not in avoid:
                avoid.append(lesson.message)
            if polarity == "reinforce" and lesson.message not in reinforce:
                reinforce.append(lesson.message)
        return {
            "recent_trades": [
                {
                    "side": reflection.side,
                    "realized_r": reflection.realized_r,
                    "exit_reason": reflection.exit_reason,
                    "thesis_tags": reflection.thesis_tags,
                }
                for reflection in recent_reflections
            ],
            "avoid": avoid[:2],
            "reinforce": reinforce[:2],
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
            "latest_close": current,
            f"return_{return_windows[0]}_bps": self._return_bps(closes, return_windows[0]),
            f"return_{return_windows[1]}_bps": self._return_bps(closes, return_windows[1]),
            f"return_{return_windows[2]}_bps": self._return_bps(closes, return_windows[2]),
            f"ema_{fast_period}": fast_ema,
            f"ema_{slow_period}": slow_ema,
            "ema_gap_bps": self._distance_bps(fast_ema, slow_ema),
            f"atr_{atr_period}_bps": self._price_distance_bps(atr_price, current),
            f"breakout_distance_{breakout_lookback}_bps": self._breakout_distance(closes, highs, lows, breakout_lookback),
            "direction": self._bar_direction(latest),
            "close_range_position": self._close_range_position(latest),
            "body_pct": self._body_pct(latest),
            "latest_range_vs_atr": self._range_vs_atr(latest, atr_price),
            "tick_volume_ratio": self._tick_volume_ratio(tick_volumes),
            "consecutive_bull_closes": self._consecutive_closes(closes, direction="bull"),
            "consecutive_bear_closes": self._consecutive_closes(closes, direction="bear"),
            "strong_bull_bars_last_3": self._strong_bar_count(closed, atr_price=atr_price, direction="bull", lookback=3),
            "strong_bear_bars_last_3": self._strong_bar_count(closed, atr_price=atr_price, direction="bear", lookback=3),
            "consecutive_strong_bull_bars": self._consecutive_strong_bars(closed, atr_price=atr_price, direction="bull"),
            "consecutive_strong_bear_bars": self._consecutive_strong_bars(closed, atr_price=atr_price, direction="bear"),
        }
        summary["long_trigger_ready"] = self._trigger_ready(summary, direction="bull")
        summary["short_trigger_ready"] = self._trigger_ready(summary, direction="bear")
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
            "latest_close": current,
            f"return_{return_windows[0]}_bps": self._return_bps(closes, return_windows[0]),
            f"return_{return_windows[1]}_bps": self._return_bps(closes, return_windows[1]),
            f"ema_{fast_period}": fast_ema,
            f"ema_{slow_period}": slow_ema,
            "ema_gap_bps": self._distance_bps(fast_ema, slow_ema),
            f"atr_{atr_period}_bps": self._price_distance_bps(atr_price, current),
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
        has_expansion = atr_price <= 0 or range_vs_atr >= 0.75
        if not has_expansion or body_pct < 0.55:
            return "flat"
        if direction == "bull" and close_range_position >= 0.7:
            return "bull"
        if direction == "bear" and close_range_position <= 0.3:
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
