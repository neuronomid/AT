from __future__ import annotations

from collections.abc import Sequence

from data.schemas import BridgeSnapshot, LessonRecord, TicketState, TradeReflection
from execution.mt5_ticket_book import MT5TicketBook


class MT5ContextBuilder:
    def build_entry_packet(
        self,
        *,
        snapshot: BridgeSnapshot,
        ticket_book: MT5TicketBook,
        risk_posture: str,
        reflections: Sequence[TradeReflection],
        lessons: Sequence[LessonRecord],
    ) -> dict[str, object]:
        five = self._timeframe_summary(snapshot.bars_5m, label="5m")
        fifteen = self._timeframe_summary(snapshot.bars_15m, label="15m")
        four_hour = self._timeframe_summary(snapshot.bars_4h, label="4h")
        context_signature = self._context_signature(five=five, fifteen=fifteen, four_hour=four_hour, spread_bps=snapshot.spread_bps)
        return {
            "symbol": snapshot.symbol,
            "server_time": snapshot.server_time.isoformat(),
            "quote": {
                "bid": float(snapshot.bid),
                "ask": float(snapshot.ask),
                "spread_bps": snapshot.spread_bps,
            },
            "account": {
                "balance": float(snapshot.account.balance),
                "equity": float(snapshot.account.equity),
                "free_margin": float(snapshot.account.free_margin),
                "account_mode": snapshot.account.account_mode,
            },
            "open_exposure": {
                "current_side": ticket_book.current_side(snapshot.symbol),
                "ticket_count": ticket_book.ticket_count(snapshot.symbol),
                "long_tickets": ticket_book.ticket_count(snapshot.symbol, "long"),
                "short_tickets": ticket_book.ticket_count(snapshot.symbol, "short"),
                "open_risk_usd": float(ticket_book.total_open_risk_usd(snapshot.symbol)),
            },
            "timeframes": {
                "5m": five,
                "15m": fifteen,
                "4h": four_hour,
            },
            "feedback": self._feedback_payload(reflections=reflections, lessons=lessons),
            "risk_posture": risk_posture,
            "context_signature": context_signature,
        }

    def build_manager_packet(
        self,
        *,
        snapshot: BridgeSnapshot,
        ticket_book: MT5TicketBook,
        allowed_actions: dict[str, list[str]],
        risk_posture: str,
        reflections: Sequence[TradeReflection],
        lessons: Sequence[LessonRecord],
    ) -> dict[str, object]:
        atr_pips = self._timeframe_summary(snapshot.bars_5m, label="5m").get("atr_14_pips", 0.0)
        tickets = [
            self._ticket_payload(ticket=ticket, allowed_actions=allowed_actions.get(ticket.ticket_id, ["hold"]), atr_pips=atr_pips)
            for ticket in ticket_book.all(snapshot.symbol)
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
                "5m": self._timeframe_summary(snapshot.bars_5m, label="5m"),
                "15m": self._timeframe_summary(snapshot.bars_15m, label="15m"),
                "4h": self._timeframe_summary(snapshot.bars_4h, label="4h"),
            },
            "tickets": tickets,
            "feedback": self._feedback_payload(reflections=reflections, lessons=lessons),
            "risk_posture": risk_posture,
        }

    def _ticket_payload(self, *, ticket: TicketState, allowed_actions: list[str], atr_pips: float) -> dict[str, object]:
        return {
            "ticket_id": ticket.ticket_id,
            "side": ticket.side,
            "volume_lots": float(ticket.volume_lots),
            "open_price": float(ticket.open_price),
            "current_price": float(ticket.current_price or ticket.open_price),
            "stop_loss": float(ticket.stop_loss) if ticket.stop_loss is not None else None,
            "take_profit": float(ticket.take_profit) if ticket.take_profit is not None else None,
            "risk_amount_usd": float(ticket.risk_amount_usd or 0),
            "unrealized_pnl_usd": float(ticket.unrealized_pnl_usd),
            "unrealized_r": ticket.unrealized_r,
            "partial_taken": ticket.partial_taken,
            "protected": ticket.protected,
            "basket_id": ticket.basket_id,
            "allowed_actions": allowed_actions,
            "atr_14_pips": atr_pips,
        }

    def _feedback_payload(
        self,
        *,
        reflections: Sequence[TradeReflection],
        lessons: Sequence[LessonRecord],
    ) -> dict[str, object]:
        recent_reflections = list(reflections)[-5:]
        avoid: list[str] = []
        reinforce: list[str] = []
        for lesson in reversed(list(lessons)[-10:]):
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
            "avoid": avoid[:3],
            "reinforce": reinforce[:3],
        }

    def _timeframe_summary(self, bars, *, label: str) -> dict[str, object]:
        if not bars:
            return {"label": label, "samples": 0}
        closes = [float(bar.close_price) for bar in bars]
        current = closes[-1]
        ema_9 = self._ema(closes, 9)
        ema_20 = self._ema(closes, 20)
        atr_14 = self._atr(bars, 14)
        return {
            "label": label,
            "samples": len(bars),
            "latest_close": current,
            "return_3_bps": self._return_bps(closes, 3),
            "return_6_bps": self._return_bps(closes, 6),
            "ema_9": ema_9,
            "ema_20": ema_20,
            "ema_gap_bps": self._distance_bps(ema_9, ema_20),
            "atr_14_pips": atr_14 * 10000.0,
        }

    def _context_signature(self, *, five: dict[str, object], fifteen: dict[str, object], four_hour: dict[str, object], spread_bps: float | None) -> str:
        spread_bucket = "tight" if spread_bps is None or spread_bps <= 12 else "wide"
        return "|".join(
            [
                self._trend_bucket(float(five.get("ema_gap_bps", 0.0))),
                self._trend_bucket(float(fifteen.get("ema_gap_bps", 0.0))),
                self._trend_bucket(float(four_hour.get("ema_gap_bps", 0.0))),
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

    def _atr(self, bars, period: int) -> float:
        if len(bars) < 2:
            return 0.0
        window = list(bars)[-period:]
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
