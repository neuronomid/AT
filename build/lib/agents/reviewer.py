from collections import Counter
from decimal import Decimal
from uuid import uuid4

from data.schemas import (
    AccountSnapshot,
    LessonRecord,
    MarketSnapshot,
    OrderSnapshot,
    ReviewSummary,
    TradeDecision,
    TradeReview,
    TradeUpdate,
)


class ReviewerAgent:
    """Builds post-trade reviews and extracts lessons from journal history."""

    def review_execution(
        self,
        *,
        decision: TradeDecision,
        market_snapshot: MarketSnapshot,
        before_account: AccountSnapshot,
        after_account: AccountSnapshot,
        order: OrderSnapshot,
        update: TradeUpdate | None,
        spread_bps: float | None,
    ) -> TradeReview:
        cash_delta = after_account.cash - before_account.cash
        position_qty_delta = after_account.open_position_qty - before_account.open_position_qty

        outcome = "timed_out"
        summary = "Order was submitted but no trade update arrived within the timeout window."
        failure_mode: str | None = "missing_trade_update"
        lessons: list[str] = ["Do not trust execution state until the trade update stream confirms the order."]

        if update is not None:
            outcome, summary, failure_mode, lessons = self._classify_update(
                decision=decision,
                order=order,
                update=update,
                before_account=before_account,
                after_account=after_account,
            )

        if spread_bps is not None and spread_bps > 12:
            lessons.append("Entries are being considered with a double-digit spread in bps; tighten the spread filter.")

        return TradeReview(
            review_id=str(uuid4()),
            order_id=order.id,
            symbol=order.symbol,
            action=decision.action,
            outcome=outcome,
            summary=summary,
            decision_confidence=decision.confidence,
            spread_bps=spread_bps,
            failure_mode=failure_mode,
            cash_delta=cash_delta,
            position_qty_delta=position_qty_delta,
            filled_qty=(update.order.filled_qty if update is not None else order.filled_qty),
            filled_avg_price=(update.order.filled_avg_price if update is not None else order.filled_avg_price),
            lesson_candidates=self._dedupe_lessons(lessons),
        )

    def summarize_journal(self, records: list[dict[str, object]]) -> ReviewSummary:
        action_counts: Counter[str] = Counter()
        rejection_reasons: Counter[str] = Counter()
        review_outcomes: Counter[str] = Counter()
        lessons: list[LessonRecord] = []

        decision_records = 0
        trade_reviews = 0
        executable_decisions = 0
        risk_rejections = 0

        for record in records:
            record_type = record.get("record_type")
            if record_type == "decision" or (
                record_type is None and "decision" in record and "risk_decision" in record
            ):
                decision_records += 1
                decision = record.get("decision", {})
                risk_decision = record.get("risk_decision", {})
                action = decision.get("action")
                if isinstance(action, str):
                    action_counts[action] += 1
                    if action in {"buy", "sell", "exit", "reduce"}:
                        executable_decisions += 1
                approved = risk_decision.get("approved")
                if approved is False:
                    risk_rejections += 1
                    reason = risk_decision.get("reason")
                    if isinstance(reason, str):
                        rejection_reasons[reason] += 1

            if record_type == "trade_review" or (record_type is None and "review" in record):
                trade_reviews += 1
                review = record.get("review", {})
                outcome = review.get("outcome")
                if isinstance(outcome, str):
                    review_outcomes[outcome] += 1
                for lesson_message in review.get("lesson_candidates", []):
                    if isinstance(lesson_message, str):
                        lessons.append(
                            LessonRecord(
                                lesson_id=str(uuid4()),
                                category="trade_review",
                                message=lesson_message,
                                confidence=0.65,
                                source=str(review.get("order_id", "trade_review")),
                            )
                        )

        lessons.extend(self._aggregate_lessons(action_counts, rejection_reasons, review_outcomes, decision_records))
        return ReviewSummary(
            total_records=len(records),
            decision_records=decision_records,
            trade_reviews=trade_reviews,
            executable_decisions=executable_decisions,
            risk_rejections=risk_rejections,
            action_counts=dict(action_counts),
            rejection_reasons=dict(rejection_reasons),
            review_outcomes=dict(review_outcomes),
            lessons=self._dedupe_lesson_records(lessons),
        )

    def lessons_from_review(self, review: TradeReview) -> list[LessonRecord]:
        return [
            LessonRecord(
                lesson_id=str(uuid4()),
                category="trade_review",
                message=message,
                confidence=0.65,
                source=review.order_id,
            )
            for message in self._dedupe_lessons(review.lesson_candidates)
        ]

    def _classify_update(
        self,
        *,
        decision: TradeDecision,
        order: OrderSnapshot,
        update: TradeUpdate,
        before_account: AccountSnapshot,
        after_account: AccountSnapshot,
    ) -> tuple[str, str, str | None, list[str]]:
        if update.event == "fill":
            if decision.action == "buy" and after_account.open_position_qty > before_account.open_position_qty:
                return (
                    "entry_opened",
                    "Buy order filled and the ETH position increased as expected.",
                    None,
                    ["Track what happens after filled entries so the agent can separate good signals from noise."],
                )
            if decision.action == "exit" and after_account.open_position_qty < before_account.open_position_qty:
                return (
                    "position_reduced",
                    "Exit order filled and the ETH position was reduced.",
                    None,
                    ["Review whether exits happen because momentum truly broke down or because the rule is too reactive."],
                )
            if decision.action == "reduce" and after_account.open_position_qty < before_account.open_position_qty:
                return (
                    "position_reduced",
                    "Reduce order filled and the ETH position was partially reduced.",
                    None,
                    ["Review whether scale-outs improve realized R or exit winners too early."],
                )
            return (
                "state_mismatch",
                "Order filled but the resulting account state did not move in the expected direction.",
                "portfolio_reconciliation",
                ["When fills and positions disagree, add an extra reconciliation pass before trusting the result."],
            )

        if update.event in {"rejected", "canceled", "expired"}:
            return (
                update.event,
                f"Order lifecycle ended with {update.event}.",
                "execution_rejection",
                [f"Capture and review repeated {update.event} events because they indicate order-shape or broker-state issues."],
            )

        return (
            "timed_out",
            f"Received trade update event {update.event}, which is not yet mapped to a completed review state.",
            "unknown_trade_update",
            ["Expand the reviewer mapping so every trade update event is labeled explicitly."],
        )

    def _aggregate_lessons(
        self,
        action_counts: Counter[str],
        rejection_reasons: Counter[str],
        review_outcomes: Counter[str],
        decision_records: int,
    ) -> list[LessonRecord]:
        lessons: list[LessonRecord] = []
        if action_counts.get("do_nothing", 0) >= max(3, decision_records // 2 if decision_records else 3):
            lessons.append(
                LessonRecord(
                    lesson_id=str(uuid4()),
                    category="decision_loop",
                    message="The agent spends most iterations doing nothing; tune warm-up logic and thresholds before expecting trade frequency.",
                    confidence=0.6,
                    source="journal_summary",
                )
            )

        for reason, count in rejection_reasons.items():
            if count >= 2 and "spread" in reason.lower():
                lessons.append(
                    LessonRecord(
                        lesson_id=str(uuid4()),
                        category="risk_rejection",
                        message="Risk rejections are clustering around spread conditions; tighten entry timing or relax the spread threshold only with evidence.",
                        confidence=0.7,
                        source="journal_summary",
                    )
                )
            if count >= 2 and "confidence" in reason.lower():
                lessons.append(
                    LessonRecord(
                        lesson_id=str(uuid4()),
                        category="risk_rejection",
                        message="Many candidate trades fail the confidence gate; recalibrate the analyst confidence scale before enabling more execution.",
                        confidence=0.7,
                        source="journal_summary",
                    )
                )

        if review_outcomes.get("state_mismatch", 0) > 0:
            lessons.append(
                LessonRecord(
                    lesson_id=str(uuid4()),
                    category="execution_integrity",
                    message="At least one fill did not reconcile cleanly with the portfolio state; add a second account refresh before trusting position deltas.",
                    confidence=0.8,
                    source="journal_summary",
                )
            )

        return lessons

    def _dedupe_lessons(self, lessons: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for lesson in lessons:
            if lesson not in seen:
                seen.add(lesson)
                deduped.append(lesson)
        return deduped

    def _dedupe_lesson_records(self, lessons: list[LessonRecord]) -> list[LessonRecord]:
        seen: set[str] = set()
        deduped: list[LessonRecord] = []
        for lesson in lessons:
            if lesson.message in seen:
                continue
            seen.add(lesson.message)
            deduped.append(lesson)
        return deduped
