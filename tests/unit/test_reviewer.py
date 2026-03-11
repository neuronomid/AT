from datetime import datetime, timezone
from decimal import Decimal

from agents.reviewer import ReviewerAgent
from data.schemas import AccountSnapshot, MarketSnapshot, OrderSnapshot, TradeDecision, TradeUpdate


def _market_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="ETH/USD",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        bid_price=Decimal("2000"),
        ask_price=Decimal("2000.5"),
        last_trade_price=Decimal("2000.2"),
    )


def test_reviewer_labels_filled_buy_as_entry_opened() -> None:
    reviewer = ReviewerAgent()
    review = reviewer.review_execution(
        decision=TradeDecision(action="buy", confidence=0.8, rationale="momentum"),
        market_snapshot=_market_snapshot(),
        before_account=AccountSnapshot(cash=Decimal("2500"), open_position_qty=Decimal("0")),
        after_account=AccountSnapshot(cash=Decimal("2490"), open_position_qty=Decimal("0.0048")),
        order=OrderSnapshot(
            id="order-1",
            client_order_id="client-1",
            symbol="ETH/USD",
            side="buy",
            type="market",
            time_in_force="gtc",
            status="filled",
        ),
        update=TradeUpdate(
            event="fill",
            order=OrderSnapshot(
                id="order-1",
                client_order_id="client-1",
                symbol="ETH/USD",
                side="buy",
                type="market",
                time_in_force="gtc",
                status="filled",
                filled_qty=Decimal("0.0048"),
                filled_avg_price=Decimal("2083.33"),
            ),
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        spread_bps=9.0,
    )

    assert review.outcome == "entry_opened"
    assert review.cash_delta == Decimal("-10")
    assert review.position_qty_delta == Decimal("0.0048")


def test_reviewer_builds_summary_lessons_from_journal_records() -> None:
    reviewer = ReviewerAgent()
    summary = reviewer.summarize_journal(
        [
            {
                "record_type": "decision",
                "decision": {"action": "do_nothing"},
                "risk_decision": {"approved": False, "reason": "Decision confidence is below the configured minimum."},
            },
            {
                "record_type": "decision",
                "decision": {"action": "do_nothing"},
                "risk_decision": {"approved": False, "reason": "Decision confidence is below the configured minimum."},
            },
            {
                "record_type": "decision",
                "decision": {"action": "do_nothing"},
                "risk_decision": {"approved": False, "reason": "Decision confidence is below the configured minimum."},
            },
        ]
    )

    assert summary.decision_records == 3
    assert summary.risk_rejections == 3
    assert any("confidence gate" in lesson.message.lower() for lesson in summary.lessons)
