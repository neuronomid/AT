from datetime import datetime, timedelta, timezone
from decimal import Decimal

from data.schemas import AccountSnapshot, LLMRuntimeDecision, LiveCandle
from execution.order_manager import OrderManager
from execution.position_tracker import PositionTracker
from risk.v4_policy import V4RiskPolicy


def _candles() -> list[LiveCandle]:
    base = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    candles: list[LiveCandle] = []
    for idx in range(20):
        price = Decimal("3000") + Decimal(str(idx))
        candles.append(
            LiveCandle(
                symbol="ETH/USD",
                start_at=base + timedelta(minutes=idx),
                end_at=base + timedelta(minutes=idx, seconds=59),
                open_price=price,
                high_price=price + Decimal("2"),
                low_price=price - Decimal("1"),
                close_price=price + Decimal("1"),
                volume=Decimal("1"),
                trade_count=4,
                spread_bps=2.0,
            )
        )
    return candles


def test_v4_policy_normalizes_buy_decision() -> None:
    policy = V4RiskPolicy()
    decision = policy.normalize_decision(
        runtime_decision=LLMRuntimeDecision(
            action="buy",
            confidence=0.8,
            rationale="trend",
            risk_fraction_equity=0.01,
            take_profit_r=1.5,
            thesis_tags=["trend"],
        ),
        candles=_candles(),
        account_snapshot=AccountSnapshot(
            equity=Decimal("10000"),
            cash=Decimal("8000"),
            buying_power=Decimal("8000"),
            crypto_status="ACTIVE",
        ),
        context_signature="bull_stack|mid_atr|inside|tight_spread|trend",
    )

    assert decision.action == "buy"
    assert decision.trade_plan is not None
    assert decision.execution_plan is not None
    assert decision.execution_plan.requested_notional_usd is not None
    assert decision.execution_plan.requested_notional_usd > 0


def test_v4_policy_rejects_context_churn_after_a_loss() -> None:
    policy = V4RiskPolicy()
    decision = policy.normalize_decision(
        runtime_decision=LLMRuntimeDecision(
            action="buy",
            confidence=0.8,
            rationale="retry",
            risk_fraction_equity=0.01,
            take_profit_r=1.0,
            thesis_tags=["trend"],
        ),
        candles=_candles(),
        account_snapshot=AccountSnapshot(
            equity=Decimal("10000"),
            cash=Decimal("8000"),
            buying_power=Decimal("8000"),
            crypto_status="ACTIVE",
        ),
        context_signature="bull_stack|mid_atr|inside|tight_spread|trend",
    )

    result = policy.evaluate(
        decision=decision,
        account_snapshot=AccountSnapshot(
            equity=Decimal("10000"),
            cash=Decimal("8000"),
            buying_power=Decimal("8000"),
            crypto_status="ACTIVE",
        ),
        order_manager=OrderManager(),
        position_tracker=PositionTracker(),
        trades_this_hour=0,
        spread_bps=2.0,
        stale_age_seconds=5.0,
        recent_context_signatures=["bull_stack|mid_atr|inside|tight_spread|trend"] * 3,
        last_losing_signature="bull_stack|mid_atr|inside|tight_spread|trend",
    )

    assert result.approved is False
    assert "anti-churn" in result.reason.lower()
