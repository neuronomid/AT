from datetime import datetime, timezone
from decimal import Decimal

from data.schemas import AccountSnapshot, MarketSnapshot, TradeDecision
from execution.order_manager import OrderManager
from risk.policy import RiskPolicy


def _policy() -> RiskPolicy:
    return RiskPolicy(
        min_confidence=0.6,
        max_risk_fraction=Decimal("0.01"),
        max_position_notional_usd=Decimal("100"),
        max_spread_bps=Decimal("20"),
        max_trades_per_hour=6,
        cooldown_seconds=60,
    )


def test_risk_policy_approves_safe_trade() -> None:
    result = _policy().evaluate(
        decision=TradeDecision(action="buy", confidence=0.8, rationale="safe setup"),
        account_snapshot=AccountSnapshot(cash=Decimal("2500"), buying_power=Decimal("5000"), crypto_status="ACTIVE"),
        market_snapshot=MarketSnapshot(
            symbol="ETH/USD",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            bid_price=Decimal("2000"),
            ask_price=Decimal("2000.5"),
        ),
        order_manager=OrderManager(),
        trades_this_hour=0,
    )

    assert result.approved is True
    assert result.allowed_notional_usd == Decimal("25.00")


def test_risk_policy_rejects_wide_spread() -> None:
    result = _policy().evaluate(
        decision=TradeDecision(action="buy", confidence=0.8, rationale="safe setup"),
        account_snapshot=AccountSnapshot(cash=Decimal("2500"), buying_power=Decimal("5000"), crypto_status="ACTIVE"),
        market_snapshot=MarketSnapshot(
            symbol="ETH/USD",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            bid_price=Decimal("2000"),
            ask_price=Decimal("2010"),
        ),
        order_manager=OrderManager(),
        trades_this_hour=0,
    )

    assert result.approved is False
    assert "spread" in result.reason.lower()
