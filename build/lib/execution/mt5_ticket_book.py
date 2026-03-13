from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from data.schemas import TicketState


@dataclass
class TicketBookSyncResult:
    opened: list[TicketState] = field(default_factory=list)
    closed: list[TicketState] = field(default_factory=list)
    changed: list[TicketState] = field(default_factory=list)


class MT5TicketBook:
    def __init__(self) -> None:
        self._tickets: dict[str, TicketState] = {}

    def sync(self, tickets: Iterable[TicketState]) -> TicketBookSyncResult:
        incoming = {ticket.ticket_id: ticket for ticket in tickets}
        result = TicketBookSyncResult()

        for ticket_id, ticket in incoming.items():
            previous = self._tickets.get(ticket_id)
            if previous is None:
                result.opened.append(ticket)
            elif previous.model_dump(mode="json") != ticket.model_dump(mode="json"):
                result.changed.append(ticket)

        for ticket_id, ticket in list(self._tickets.items()):
            if ticket_id not in incoming:
                result.closed.append(ticket)

        self._tickets = incoming
        return result

    def all(self, symbol: str | None = None) -> list[TicketState]:
        if symbol is None:
            return list(self._tickets.values())
        normalized = symbol.strip().upper()
        return [ticket for ticket in self._tickets.values() if ticket.symbol.strip().upper() == normalized]

    def by_ticket_id(self, ticket_id: str) -> TicketState | None:
        return self._tickets.get(ticket_id)

    def ticket_count(self, symbol: str, side: str | None = None) -> int:
        return len(self._matching(symbol=symbol, side=side))

    def current_side(self, symbol: str) -> str | None:
        sides = {ticket.side for ticket in self._matching(symbol=symbol, side=None)}
        if len(sides) != 1:
            return None if not sides else "mixed"
        return next(iter(sides))

    def has_opposite_exposure(self, symbol: str, side: str) -> bool:
        opposite = "short" if side == "long" else "long"
        return self.ticket_count(symbol, opposite) > 0

    def total_open_risk_usd(self, symbol: str, side: str | None = None) -> Decimal:
        total = Decimal("0")
        for ticket in self._matching(symbol=symbol, side=side):
            total += ticket.risk_amount_usd or Decimal("0")
        return total

    def protected_count(self, symbol: str, side: str) -> int:
        return sum(1 for ticket in self._matching(symbol=symbol, side=side) if ticket.protected)

    def same_direction_basket_id(self, symbol: str, side: str) -> str | None:
        for ticket in self._matching(symbol=symbol, side=side):
            if ticket.basket_id:
                return ticket.basket_id
        return None

    def can_add_second_ticket(self, symbol: str, side: str, *, current_bar_end: datetime | None) -> bool:
        matching = self._matching(symbol=symbol, side=side)
        if len(matching) != 1:
            return False
        ticket = matching[0]
        if not ticket.protected:
            return False
        if current_bar_end is None or ticket.opened_at is None:
            return True
        return ticket.opened_at < current_bar_end

    def allowed_actions(self, ticket_id: str, *, atr_pips: float) -> list[str]:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            return ["hold"]
        actions = ["hold", "close_ticket"]
        if not ticket.protected and ticket.unrealized_r >= 0.25:
            actions.append("move_stop_to_breakeven")
        if ticket.unrealized_r >= 1.0 and not ticket.partial_taken and ticket.volume_lots >= Decimal("0.02"):
            actions.append("take_partial_50")
        if ticket.protected and ticket.unrealized_r >= 1.0 and atr_pips > 0:
            actions.append("trail_stop_to_rule")
        return actions

    def half_r_buckets(self) -> dict[str, float]:
        buckets: dict[str, float] = {}
        for ticket in self._tickets.values():
            bucket = int(ticket.unrealized_r * 2) / 2.0
            buckets[ticket.ticket_id] = bucket
        return buckets

    def signature(self) -> str:
        parts = []
        for ticket in sorted(self._tickets.values(), key=lambda item: item.ticket_id):
            parts.append(
                "|".join(
                    [
                        ticket.ticket_id,
                        ticket.side,
                        str(ticket.volume_lots),
                        str(ticket.stop_loss or ""),
                        str(ticket.take_profit or ""),
                        f"{ticket.unrealized_r:.2f}",
                    ]
                )
            )
        return ";".join(parts)

    def _matching(self, *, symbol: str, side: str | None) -> list[TicketState]:
        normalized = symbol.strip().upper()
        return [
            ticket
            for ticket in self._tickets.values()
            if ticket.symbol.strip().upper() == normalized and (side is None or ticket.side == side)
        ]
