from collections import Counter
from decimal import Decimal
from typing import Any

from agents.analyst import AnalystAgent
from data.schemas import AccountSnapshot, MarketSnapshot, ReplayMetrics
from evaluation.scorer import Scorer


class ReplayEngine:
    """Replays recorded decision snapshots against a candidate policy."""

    def __init__(self, scorer: Scorer | None = None) -> None:
        self._scorer = scorer or Scorer()

    def run(self, records: list[dict[str, Any]], policy: AnalystAgent) -> ReplayMetrics:
        decision_records = [record for record in records if self._is_decision_record(record)]
        action_counts: Counter[str] = Counter()
        equity_curve = [0.0]
        trade_returns_bps: list[float] = []
        realized_pnl_bps = 0.0
        executed_actions = 0
        opened_trades = 0
        closed_trades = 0
        bars_in_position = 0
        position_open = False
        entry_price: float | None = None

        for index, record in enumerate(decision_records):
            market_snapshot = MarketSnapshot.model_validate(record["market_snapshot"])
            account_snapshot = self._simulated_account(position_open)
            features = self._normalize_features(record.get("features", {}))
            decision = policy.analyze(market_snapshot, account_snapshot, features)
            action_counts[decision.action] += 1

            price = self._reference_price(record)
            if price is None:
                equity_curve.append(realized_pnl_bps)
                continue

            if decision.action == "buy" and not position_open:
                position_open = True
                entry_price = price
                executed_actions += 1
                opened_trades += 1
            elif decision.action == "exit" and position_open and entry_price is not None:
                trade_return = self._trade_return_bps(entry_price, price)
                trade_returns_bps.append(trade_return)
                realized_pnl_bps += trade_return
                executed_actions += 1
                closed_trades += 1
                position_open = False
                entry_price = None

            mark_to_market = realized_pnl_bps
            if position_open and entry_price is not None:
                bars_in_position += 1
                mark_to_market += self._trade_return_bps(entry_price, price)
            equity_curve.append(mark_to_market)

            if index == len(decision_records) - 1 and position_open and entry_price is not None:
                forced_return = self._trade_return_bps(entry_price, price)
                trade_returns_bps.append(forced_return)
                realized_pnl_bps += forced_return
                closed_trades += 1
                position_open = False
                entry_price = None
                equity_curve[-1] = realized_pnl_bps

        exposure_ratio = (bars_in_position / len(decision_records)) if decision_records else 0.0
        win_rate = (
            sum(1 for value in trade_returns_bps if value > 0) / len(trade_returns_bps) if trade_returns_bps else 0.0
        )
        average_trade_bps = self._scorer.expectancy(trade_returns_bps)
        max_drawdown_bps = self._scorer.max_drawdown(equity_curve)
        score = self._scorer.score(
            realized_pnl_bps=realized_pnl_bps,
            trade_returns_bps=trade_returns_bps,
            max_drawdown_bps=max_drawdown_bps,
            exposure_ratio=exposure_ratio,
        )
        return ReplayMetrics(
            policy_name=policy.policy_name,
            samples=len(decision_records),
            executed_actions=executed_actions,
            opened_trades=opened_trades,
            closed_trades=closed_trades,
            action_counts=dict(action_counts),
            win_rate=win_rate,
            realized_pnl_bps=realized_pnl_bps,
            average_trade_bps=average_trade_bps,
            max_drawdown_bps=max_drawdown_bps,
            exposure_ratio=exposure_ratio,
            score=score,
        )

    def _is_decision_record(self, record: dict[str, Any]) -> bool:
        return record.get("record_type") == "decision" or (
            record.get("record_type") is None and "decision" in record and "market_snapshot" in record
        )

    def _normalize_features(self, features: Any) -> dict[str, float]:
        if not isinstance(features, dict):
            return {}
        normalized: dict[str, float] = {}
        for key, value in features.items():
            if isinstance(value, (int, float)):
                normalized[str(key)] = float(value)
        return normalized

    def _reference_price(self, record: dict[str, Any]) -> float | None:
        features = record.get("features", {})
        if isinstance(features, dict):
            reference = features.get("reference_price") or features.get("mid_price")
            if isinstance(reference, (int, float)):
                return float(reference)
        market = record.get("market_snapshot", {})
        if isinstance(market, dict):
            for field in ("last_trade_price", "bid_price", "ask_price"):
                value = market.get(field)
                if value not in (None, ""):
                    return float(Decimal(str(value)))
        return None

    def _simulated_account(self, position_open: bool) -> AccountSnapshot:
        return AccountSnapshot(
            cash=Decimal("2500"),
            buying_power=Decimal("5000"),
            open_position_qty=Decimal("1") if position_open else Decimal("0"),
            crypto_status="ACTIVE",
        )

    def _trade_return_bps(self, entry_price: float, exit_price: float) -> float:
        if entry_price <= 0:
            return 0.0
        return ((exit_price - entry_price) / entry_price) * 10000
