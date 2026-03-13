from bisect import bisect_left
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from agents.analyst import AnalystAgent
from data.feature_engine import FeatureEngine
from data.schemas import (
    AccountSnapshot,
    BacktestRegimeSummary,
    BacktestTradeSummary,
    BacktestTradeRecord,
    BacktestWindowSummary,
    HistoricalBar,
    ReplayMetrics,
    TradePlan,
)
from evaluation.scorer import Scorer
from execution.order_manager import OrderManager
from risk.policy import RiskPolicy


@dataclass
class SimulationResult:
    metrics: ReplayMetrics
    trades: list[BacktestTradeRecord]
    trade_summary: BacktestTradeSummary | None = None
    regime_summary: BacktestRegimeSummary | None = None


@dataclass
class ManagedPosition:
    policy_name: str
    side: Literal["buy", "sell"]
    qty: Decimal
    remaining_qty: Decimal
    entry_price: Decimal
    entry_at: datetime
    entry_notional: Decimal
    stop_price: Decimal
    take_profit_price: Decimal
    max_take_profit_price: Decimal
    trailing_stop_bps: Decimal
    initial_stop_bps: Decimal
    time_stop_bars: int
    partial_take_profit_fraction: Decimal
    current_stop_price: Decimal
    expected_slippage_bps: float
    fill_ratio: float
    bars_held: int = 0
    partial_taken: bool = False
    partial_realized_pnl_usd: Decimal = Decimal("0")
    accumulated_fees_usd: Decimal = Decimal("0")
    cumulative_slippage_bps: float = 0.0
    trailing_anchor_price: Decimal | None = None
    entry_regime: str | None = None
    entry_regime_probability: float | None = None
    entry_regime_probabilities: dict[str, float] | None = None
    entry_continuation_probabilities: dict[str, float] | None = None
    planned_stop_loss_bps: float | None = None
    planned_take_profit_bps: float | None = None
    planned_max_take_profit_bps: float | None = None
    planned_trailing_stop_bps: float | None = None
    planned_time_stop_bars: int | None = None
    planned_risk_usd: float | None = None


class HistoricalBacktester:
    def __init__(
        self,
        *,
        symbol: str,
        starting_cash_usd: Decimal,
        risk_policy: RiskPolicy,
        scorer: Scorer | None = None,
        fee_bps_per_side: float = 1.25,
        base_slippage_bps: float = 1.0,
        max_participation_rate: float = 0.02,
        min_fill_ratio: float = 0.5,
    ) -> None:
        self._symbol = symbol
        self._starting_cash_usd = starting_cash_usd
        self._risk_policy = risk_policy
        self._scorer = scorer or Scorer()
        self._fee_bps_per_side = fee_bps_per_side
        self._base_slippage_bps = base_slippage_bps
        self._max_participation_rate = max_participation_rate
        self._min_fill_ratio = min_fill_ratio

    def simulate(
        self,
        *,
        bars: list[HistoricalBar],
        policy: AnalystAgent,
        evaluation_start_index: int = 0,
    ) -> SimulationResult:
        feature_engine = FeatureEngine(max_snapshots=480)
        order_manager = OrderManager()
        executed_trade_times: deque[datetime] = deque()
        trades: list[BacktestTradeRecord] = []
        action_counts: Counter[str] = Counter()
        regime_counts: Counter[str] = Counter()
        entry_regime_counts: Counter[str] = Counter()
        regime_probability_sums: dict[str, float] = {}

        cash = self._starting_cash_usd
        position: ManagedPosition | None = None
        trade_returns_bps: list[float] = []
        bars_in_position = 0
        equity_curve_bps: list[float] = [0.0]

        for index, bar in enumerate(bars):
            snapshot = bar.to_market_snapshot()
            features = feature_engine.build_features(snapshot)
            if index < evaluation_start_index:
                continue

            while executed_trade_times and executed_trade_times[0] < bar.timestamp - timedelta(hours=1):
                executed_trade_times.popleft()

            if position is not None:
                bars_in_position += 1
                position.bars_held += 1
                cash, closed_trade, trade_event_count = self._manage_position(position=position, bar=bar, cash=cash)
                for _ in range(trade_event_count):
                    executed_trade_times.append(bar.timestamp)
                    order_manager.last_trade_at = bar.timestamp
                if closed_trade is not None:
                    trade_returns_bps.append(closed_trade.return_bps)
                    trades.append(closed_trade)
                    position = None

            account_snapshot = AccountSnapshot(
                equity=self._equity(cash=cash, position=position, price=bar.close_price),
                cash=cash,
                buying_power=cash,
                open_position_qty=self._signed_qty(position),
                crypto_status="ACTIVE",
            )
            decision = policy.analyze(snapshot, account_snapshot, features)
            action_counts[decision.action] += 1
            if decision.regime is not None:
                regime_counts[decision.regime] += 1
                regime_probability_sums[decision.regime] = (
                    regime_probability_sums.get(decision.regime, 0.0) + decision.regime_probability
                )
            risk_decision = self._risk_policy.evaluate(
                decision=decision,
                account_snapshot=account_snapshot,
                market_snapshot=snapshot,
                order_manager=order_manager,
                trades_this_hour=len(executed_trade_times),
            )

            if decision.action in {"buy", "sell"} and risk_decision.approved and position is None:
                notional = min(risk_decision.allowed_notional_usd, cash)
                if notional > 0:
                    fill_ratio = self._fill_ratio(bar=bar, notional=notional)
                    if fill_ratio < self._min_fill_ratio:
                        equity_curve_bps.append(self._equity_to_bps(cash))
                        continue
                    trade_plan = decision.trade_plan or self._default_trade_plan()
                    expected_slippage_bps = (
                        decision.execution_plan.expected_slippage_bps
                        if decision.execution_plan is not None
                        else self._base_slippage_bps
                    )
                    filled_notional = notional * Decimal(str(fill_ratio))
                    entry_price = self._apply_slippage(
                        price=bar.close_price,
                        side=decision.action,
                        slippage_bps=expected_slippage_bps,
                    )
                    entry_fee_usd = self._fee_usd(filled_notional)
                    position = self._open_position(
                        policy_name=policy.policy_name,
                        side=decision.action,
                        entry_price=entry_price,
                        entry_at=bar.timestamp,
                        notional=filled_notional,
                        trade_plan=trade_plan,
                        expected_slippage_bps=expected_slippage_bps,
                        entry_fee_usd=entry_fee_usd,
                        fill_ratio=fill_ratio,
                        decision=decision,
                    )
                    cash -= filled_notional + entry_fee_usd
                    executed_trade_times.append(bar.timestamp)
                    order_manager.last_trade_at = bar.timestamp
                    if decision.regime is not None:
                        entry_regime_counts[decision.regime] += 1

            elif decision.action == "exit" and risk_decision.approved and position is not None:
                closed_trade = self._close_position(
                    position=position,
                    exit_price=bar.close_price,
                    exit_at=bar.timestamp,
                    exit_reason="analyst_exit",
                )
                cash += self._released_cash(
                    position=position,
                    close_qty=position.remaining_qty,
                    exit_price=closed_trade.exit_price,
                    exit_fee_usd=closed_trade.fees_usd - position.accumulated_fees_usd,
                )
                executed_trade_times.append(bar.timestamp)
                order_manager.last_trade_at = bar.timestamp
                trade_returns_bps.append(closed_trade.return_bps)
                trades.append(closed_trade)
                position = None

            equity_curve_bps.append(self._equity_to_bps(self._equity(cash=cash, position=position, price=bar.close_price)))

        if position is not None and bars:
            final_bar = bars[-1]
            forced_trade = self._close_position(
                position=position,
                exit_price=final_bar.close_price,
                exit_at=final_bar.timestamp,
                exit_reason="forced_end",
            )
            cash += self._released_cash(
                position=position,
                close_qty=position.remaining_qty,
                exit_price=forced_trade.exit_price,
                exit_fee_usd=forced_trade.fees_usd - position.accumulated_fees_usd,
            )
            trade_returns_bps.append(forced_trade.return_bps)
            trades.append(forced_trade)
            equity_curve_bps[-1] = self._equity_to_bps(cash)

        realized_pnl_bps = self._equity_to_bps(cash)
        max_drawdown_bps = self._scorer.max_drawdown(equity_curve_bps)
        exposure_ratio = (
            bars_in_position / max(1, len(bars) - evaluation_start_index)
            if len(bars) > evaluation_start_index
            else 0.0
        )
        metrics = ReplayMetrics(
            policy_name=policy.policy_name,
            samples=max(0, len(bars) - evaluation_start_index),
            executed_actions=sum(action_counts[action] for action in ("buy", "sell", "exit")),
            opened_trades=len(trades),
            closed_trades=len(trades),
            action_counts=dict(action_counts),
            win_rate=(sum(1 for value in trade_returns_bps if value > 0) / len(trade_returns_bps)) if trade_returns_bps else 0.0,
            realized_pnl_bps=realized_pnl_bps,
            average_trade_bps=self._scorer.expectancy(trade_returns_bps),
            max_drawdown_bps=max_drawdown_bps,
            exposure_ratio=exposure_ratio,
            score=self._scorer.score(
                realized_pnl_bps=realized_pnl_bps,
                trade_returns_bps=trade_returns_bps,
                max_drawdown_bps=max_drawdown_bps,
                exposure_ratio=exposure_ratio,
            ),
        )
        return SimulationResult(
            metrics=metrics,
            trades=trades,
            trade_summary=self._trade_summary(trades),
            regime_summary=self._regime_summary(
                regime_counts=regime_counts,
                entry_regime_counts=entry_regime_counts,
                regime_probability_sums=regime_probability_sums,
            ),
        )

    def aggregate(self, *, policy_name: str, results: list[SimulationResult]) -> ReplayMetrics:
        action_counts: Counter[str] = Counter()
        trade_returns_bps: list[float] = []
        realized_values: list[float] = []
        drawdowns: list[float] = []
        exposure_values: list[float] = []

        for result in results:
            action_counts.update(result.metrics.action_counts)
            trade_returns_bps.extend([trade.return_bps for trade in result.trades])
            realized_values.append(result.metrics.realized_pnl_bps)
            drawdowns.append(result.metrics.max_drawdown_bps)
            exposure_values.append(result.metrics.exposure_ratio)

        realized_pnl_bps = self._scorer.expectancy(realized_values)
        max_drawdown_bps = max(drawdowns) if drawdowns else 0.0
        exposure_ratio = self._scorer.expectancy(exposure_values)
        average_trade_bps = self._scorer.expectancy(trade_returns_bps)
        return ReplayMetrics(
            policy_name=policy_name,
            samples=sum(result.metrics.samples for result in results),
            executed_actions=sum(result.metrics.executed_actions for result in results),
            opened_trades=sum(result.metrics.opened_trades for result in results),
            closed_trades=sum(result.metrics.closed_trades for result in results),
            action_counts=dict(action_counts),
            win_rate=(sum(1 for value in trade_returns_bps if value > 0) / len(trade_returns_bps)) if trade_returns_bps else 0.0,
            realized_pnl_bps=realized_pnl_bps,
            average_trade_bps=average_trade_bps,
            max_drawdown_bps=max_drawdown_bps,
            exposure_ratio=exposure_ratio,
            score=self._scorer.score(
                realized_pnl_bps=realized_pnl_bps,
                trade_returns_bps=trade_returns_bps,
                max_drawdown_bps=max_drawdown_bps,
                exposure_ratio=exposure_ratio,
            ),
        )

    def walk_forward(
        self,
        *,
        bars: list[HistoricalBar],
        candidate_policies: dict[str, AnalystAgent],
        baseline_policy: AnalystAgent,
        train_window_days: int,
        test_window_days: int,
        step_days: int,
        warmup_bars: int,
    ) -> tuple[ReplayMetrics, ReplayMetrics, list[BacktestWindowSummary], list[BacktestTradeRecord], list[int]]:
        if not bars:
            return (
                ReplayMetrics(policy_name=baseline_policy.policy_name),
                ReplayMetrics(policy_name="walk_forward_best"),
                [],
                [],
                [],
            )

        timestamps = [bar.timestamp for bar in bars]
        baseline_results: list[SimulationResult] = []
        candidate_results: list[SimulationResult] = []
        window_summaries: list[BacktestWindowSummary] = []
        all_candidate_trades: list[BacktestTradeRecord] = []
        trade_window_indexes: list[int] = []

        train_delta = timedelta(days=train_window_days)
        test_delta = timedelta(days=test_window_days)
        step_delta = timedelta(days=step_days)
        train_start = bars[0].timestamp
        final_timestamp = bars[-1].timestamp
        window_index = 1

        while True:
            train_end = train_start + train_delta
            test_end = train_end + test_delta
            if test_end > final_timestamp:
                break

            train_start_index = bisect_left(timestamps, train_start)
            train_end_index = bisect_left(timestamps, train_end)
            test_end_index = bisect_left(timestamps, test_end)
            train_bars = bars[train_start_index:train_end_index]
            test_bars = bars[train_end_index:test_end_index]

            if len(train_bars) <= warmup_bars or not test_bars:
                train_start += step_delta
                continue

            train_scores: dict[str, float] = {}
            for policy_name, policy in candidate_policies.items():
                result = self.simulate(bars=train_bars, policy=policy)
                train_scores[policy_name] = result.metrics.score

            selected_policy_name = max(train_scores, key=train_scores.get)
            selected_policy = candidate_policies[selected_policy_name]
            warmup_slice = train_bars[-warmup_bars:]
            test_with_warmup = warmup_slice + test_bars

            baseline_test_result = self.simulate(
                bars=test_with_warmup,
                policy=baseline_policy,
                evaluation_start_index=len(warmup_slice),
            )
            selected_test_result = self.simulate(
                bars=test_with_warmup,
                policy=selected_policy,
                evaluation_start_index=len(warmup_slice),
            )

            baseline_results.append(baseline_test_result)
            candidate_results.append(selected_test_result)
            window_summaries.append(
                BacktestWindowSummary(
                    window_index=window_index,
                    selected_policy_name=selected_policy_name,
                    train_start_at=train_start,
                    train_end_at=train_end,
                    test_start_at=train_end,
                    test_end_at=test_end,
                    train_scores=train_scores,
                    baseline_test_metrics=baseline_test_result.metrics,
                    selected_test_metrics=selected_test_result.metrics,
                )
            )
            for trade in selected_test_result.trades:
                all_candidate_trades.append(trade)
                trade_window_indexes.append(window_index)

            train_start += step_delta
            window_index += 1

        baseline_metrics = self.aggregate(policy_name=baseline_policy.policy_name, results=baseline_results)
        candidate_metrics = self.aggregate(policy_name="walk_forward_best", results=candidate_results)
        return baseline_metrics, candidate_metrics, window_summaries, all_candidate_trades, trade_window_indexes

    def _manage_position(
        self,
        *,
        position: ManagedPosition,
        bar: HistoricalBar,
        cash: Decimal,
    ) -> tuple[Decimal, BacktestTradeRecord | None, int]:
        trade_events = 0

        if not position.partial_taken and self._stop_hit(position=position, bar=bar):
            trade = self._close_position(
                position=position,
                exit_price=position.current_stop_price,
                exit_at=bar.timestamp,
                exit_reason="stop_loss",
            )
            cash += self._released_cash(
                position=position,
                close_qty=position.remaining_qty,
                exit_price=trade.exit_price,
                exit_fee_usd=trade.fees_usd - position.accumulated_fees_usd,
            )
            return cash, trade, 1

        if not position.partial_taken and self._take_profit_hit(position=position, bar=bar, max_target=False):
            close_qty = position.qty * position.partial_take_profit_fraction
            close_qty = min(close_qty, position.remaining_qty)
            if close_qty > 0:
                partial_exit_price = self._apply_slippage(
                    price=position.take_profit_price,
                    side="sell" if position.side == "buy" else "buy",
                    slippage_bps=position.expected_slippage_bps,
                )
                exit_fee_usd = self._fee_usd(close_qty * partial_exit_price)
                cash += self._released_cash(
                    position=position,
                    close_qty=close_qty,
                    exit_price=partial_exit_price,
                    exit_fee_usd=exit_fee_usd,
                )
                position.partial_realized_pnl_usd += self._pnl_for_leg(
                    side=position.side,
                    entry_price=position.entry_price,
                    exit_price=partial_exit_price,
                    qty=close_qty,
                ) - exit_fee_usd
                position.remaining_qty -= close_qty
                position.partial_taken = True
                position.accumulated_fees_usd += exit_fee_usd
                position.cumulative_slippage_bps += position.expected_slippage_bps
                position.current_stop_price = self._locked_profit_stop(position)
                position.trailing_anchor_price = bar.high_price if position.side == "buy" else bar.low_price
                trade_events += 1

                if position.remaining_qty <= 0:
                    trade = self._finalize_trade(
                        position=position,
                        exit_price=partial_exit_price,
                        exit_at=bar.timestamp,
                        exit_reason="take_profit_1",
                    )
                    return cash, trade, trade_events

                if self._take_profit_hit(position=position, bar=bar, max_target=True):
                    trade = self._close_position(
                        position=position,
                        exit_price=position.max_take_profit_price,
                        exit_at=bar.timestamp,
                        exit_reason="take_profit_cap",
                    )
                    cash += self._released_cash(
                        position=position,
                        close_qty=position.remaining_qty,
                        exit_price=trade.exit_price,
                        exit_fee_usd=trade.fees_usd - position.accumulated_fees_usd,
                    )
                    return cash, trade, trade_events + 1

        if position.partial_taken:
            position.trailing_anchor_price = self._updated_trailing_anchor(position=position, bar=bar)
            position.current_stop_price = self._trailing_stop_price(position)
            if self._stop_hit(position=position, bar=bar):
                trade = self._close_position(
                    position=position,
                    exit_price=position.current_stop_price,
                    exit_at=bar.timestamp,
                    exit_reason="trailing_stop",
                )
                cash += self._released_cash(
                    position=position,
                    close_qty=position.remaining_qty,
                    exit_price=trade.exit_price,
                    exit_fee_usd=trade.fees_usd - position.accumulated_fees_usd,
                )
                return cash, trade, trade_events + 1

            if self._take_profit_hit(position=position, bar=bar, max_target=True):
                trade = self._close_position(
                    position=position,
                    exit_price=position.max_take_profit_price,
                    exit_at=bar.timestamp,
                    exit_reason="take_profit_cap",
                )
                cash += self._released_cash(
                    position=position,
                    close_qty=position.remaining_qty,
                    exit_price=trade.exit_price,
                    exit_fee_usd=trade.fees_usd - position.accumulated_fees_usd,
                )
                return cash, trade, trade_events + 1

        if position.bars_held >= position.time_stop_bars:
            trade = self._close_position(
                position=position,
                exit_price=bar.close_price,
                exit_at=bar.timestamp,
                exit_reason="time_stop",
            )
            cash += self._released_cash(
                position=position,
                close_qty=position.remaining_qty,
                exit_price=trade.exit_price,
                exit_fee_usd=trade.fees_usd - position.accumulated_fees_usd,
            )
            return cash, trade, trade_events + 1

        return cash, None, trade_events

    def _open_position(
        self,
        *,
        policy_name: str,
        side: Literal["buy", "sell"],
        entry_price: Decimal,
        entry_at: datetime,
        notional: Decimal,
        trade_plan: TradePlan,
        expected_slippage_bps: float,
        entry_fee_usd: Decimal,
        fill_ratio: float,
        decision,
    ) -> ManagedPosition:
        qty = notional / entry_price
        stop_price = self._price_from_bps(entry_price, trade_plan.stop_loss_bps, side=side, favorable=False)
        return ManagedPosition(
            policy_name=policy_name,
            side=side,
            qty=qty,
            remaining_qty=qty,
            entry_price=entry_price,
            entry_at=entry_at,
            entry_notional=notional,
            stop_price=stop_price,
            take_profit_price=self._price_from_bps(entry_price, trade_plan.take_profit_bps, side=side, favorable=True),
            max_take_profit_price=self._price_from_bps(entry_price, trade_plan.max_take_profit_bps, side=side, favorable=True),
            trailing_stop_bps=Decimal(str(trade_plan.trailing_stop_bps)),
            initial_stop_bps=Decimal(str(trade_plan.stop_loss_bps)),
            time_stop_bars=trade_plan.time_stop_bars,
            partial_take_profit_fraction=Decimal(str(trade_plan.partial_take_profit_fraction)),
            current_stop_price=stop_price,
            expected_slippage_bps=expected_slippage_bps,
            fill_ratio=fill_ratio,
            accumulated_fees_usd=entry_fee_usd,
            cumulative_slippage_bps=expected_slippage_bps,
            entry_regime=decision.regime,
            entry_regime_probability=decision.regime_probability,
            entry_regime_probabilities=dict(decision.regime_probabilities),
            entry_continuation_probabilities=dict(decision.continuation_probabilities),
            planned_stop_loss_bps=trade_plan.stop_loss_bps,
            planned_take_profit_bps=trade_plan.take_profit_bps,
            planned_max_take_profit_bps=trade_plan.max_take_profit_bps,
            planned_trailing_stop_bps=trade_plan.trailing_stop_bps,
            planned_time_stop_bars=trade_plan.time_stop_bars,
            planned_risk_usd=decision.planned_risk_usd,
        )

    def _close_position(
        self,
        *,
        position: ManagedPosition,
        exit_price: Decimal,
        exit_at: datetime,
        exit_reason: str,
    ) -> BacktestTradeRecord:
        executed_exit_price = self._apply_slippage(
            price=exit_price,
            side="sell" if position.side == "buy" else "buy",
            slippage_bps=position.expected_slippage_bps,
        )
        return self._finalize_trade(
            position=position,
            exit_price=executed_exit_price,
            exit_at=exit_at,
            exit_reason=exit_reason,
        )

    def _finalize_trade(
        self,
        *,
        position: ManagedPosition,
        exit_price: Decimal,
        exit_at: datetime,
        exit_reason: str,
    ) -> BacktestTradeRecord:
        exit_fee_usd = self._fee_usd(position.remaining_qty * exit_price)
        final_leg_pnl = self._pnl_for_leg(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            qty=position.remaining_qty,
        )
        total_fees = position.accumulated_fees_usd + exit_fee_usd
        total_pnl = position.partial_realized_pnl_usd + final_leg_pnl - exit_fee_usd
        return BacktestTradeRecord(
            policy_name=position.policy_name,
            symbol=self._symbol,
            side=position.side,
            entry_at=position.entry_at,
            exit_at=exit_at,
            entry_price=position.entry_price,
            exit_price=exit_price,
            qty=position.qty,
            notional_usd=position.entry_notional,
            pnl_usd=total_pnl,
            return_bps=float((total_pnl / position.entry_notional) * Decimal("10000")) if position.entry_notional > 0 else 0.0,
            bars_held=position.bars_held,
            exit_reason=exit_reason,
            fees_usd=total_fees,
            slippage_bps=position.cumulative_slippage_bps + position.expected_slippage_bps,
            fill_ratio=position.fill_ratio,
            entry_regime=position.entry_regime,
            entry_regime_probability=position.entry_regime_probability,
            entry_regime_probabilities=position.entry_regime_probabilities or {},
            entry_continuation_probabilities=position.entry_continuation_probabilities or {},
            planned_stop_loss_bps=position.planned_stop_loss_bps,
            planned_take_profit_bps=position.planned_take_profit_bps,
            planned_max_take_profit_bps=position.planned_max_take_profit_bps,
            planned_trailing_stop_bps=position.planned_trailing_stop_bps,
            planned_time_stop_bars=position.planned_time_stop_bars,
            planned_risk_usd=position.planned_risk_usd,
        )

    def _released_cash(
        self,
        *,
        position: ManagedPosition,
        close_qty: Decimal,
        exit_price: Decimal,
        exit_fee_usd: Decimal,
    ) -> Decimal:
        released_notional = close_qty * position.entry_price
        pnl = self._pnl_for_leg(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            qty=close_qty,
        )
        return released_notional + pnl - exit_fee_usd

    def _pnl_for_leg(
        self,
        *,
        side: Literal["buy", "sell"],
        entry_price: Decimal,
        exit_price: Decimal,
        qty: Decimal,
    ) -> Decimal:
        if side == "buy":
            return qty * (exit_price - entry_price)
        return qty * (entry_price - exit_price)

    def _equity(self, *, cash: Decimal, position: ManagedPosition | None, price: Decimal) -> Decimal:
        if position is None:
            return cash
        reserved_notional = position.remaining_qty * position.entry_price
        unrealized_pnl = self._pnl_for_leg(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=price,
            qty=position.remaining_qty,
        )
        return cash + reserved_notional + unrealized_pnl

    def _take_profit_hit(self, *, position: ManagedPosition, bar: HistoricalBar, max_target: bool) -> bool:
        target_price = position.max_take_profit_price if max_target else position.take_profit_price
        if position.side == "buy":
            return bar.high_price >= target_price
        return bar.low_price <= target_price

    def _stop_hit(self, *, position: ManagedPosition, bar: HistoricalBar) -> bool:
        if position.side == "buy":
            return bar.low_price <= position.current_stop_price
        return bar.high_price >= position.current_stop_price

    def _updated_trailing_anchor(self, *, position: ManagedPosition, bar: HistoricalBar) -> Decimal:
        if position.side == "buy":
            return max(position.trailing_anchor_price or position.entry_price, bar.high_price)
        return min(position.trailing_anchor_price or position.entry_price, bar.low_price)

    def _locked_profit_stop(self, position: ManagedPosition) -> Decimal:
        half_r = position.initial_stop_bps / Decimal("20000")
        if position.side == "buy":
            return position.entry_price * (Decimal("1") + half_r)
        return position.entry_price * (Decimal("1") - half_r)

    def _trailing_stop_price(self, position: ManagedPosition) -> Decimal:
        anchor = position.trailing_anchor_price or position.entry_price
        trailing_multiplier = position.trailing_stop_bps / Decimal("10000")
        if position.side == "buy":
            trailing_stop = anchor * (Decimal("1") - trailing_multiplier)
            return max(position.entry_price, trailing_stop)
        trailing_stop = anchor * (Decimal("1") + trailing_multiplier)
        return min(position.entry_price, trailing_stop)

    def _fee_usd(self, notional: Decimal) -> Decimal:
        return notional * Decimal(str(self._fee_bps_per_side / 10000.0))

    def _fill_ratio(self, *, bar: HistoricalBar, notional: Decimal) -> float:
        if notional <= 0:
            return 0.0
        bar_notional = bar.volume * bar.close_price
        if bar_notional <= 0:
            return 1.0
        participation_cap = bar_notional * Decimal(str(self._max_participation_rate))
        ratio = participation_cap / notional
        return float(min(Decimal("1"), max(Decimal("0"), ratio)))

    def _apply_slippage(self, *, price: Decimal, side: Literal["buy", "sell"], slippage_bps: float) -> Decimal:
        effective_bps = max(self._base_slippage_bps, slippage_bps)
        move = Decimal(str(effective_bps)) / Decimal("10000")
        if side == "buy":
            return price * (Decimal("1") + move)
        return price * (Decimal("1") - move)

    def _price_from_bps(self, price: Decimal, bps: float, *, side: Literal["buy", "sell"], favorable: bool) -> Decimal:
        move = Decimal(str(bps)) / Decimal("10000")
        if side == "buy":
            multiplier = Decimal("1") + move if favorable else Decimal("1") - move
        else:
            multiplier = Decimal("1") - move if favorable else Decimal("1") + move
        return price * multiplier

    def _default_trade_plan(self) -> TradePlan:
        return TradePlan(
            stop_loss_bps=15.0,
            take_profit_bps=15.0,
            max_take_profit_bps=30.0,
            trailing_stop_bps=11.25,
            time_stop_bars=12,
            partial_take_profit_fraction=0.35,
        )

    def _equity_to_bps(self, equity: Decimal) -> float:
        if self._starting_cash_usd <= 0:
            return 0.0
        return float(((equity - self._starting_cash_usd) / self._starting_cash_usd) * Decimal("10000"))

    def _signed_qty(self, position: ManagedPosition | None) -> Decimal:
        if position is None:
            return Decimal("0")
        return position.remaining_qty if position.side == "buy" else -position.remaining_qty

    def _trade_summary(self, trades: list[BacktestTradeRecord]) -> BacktestTradeSummary:
        if not trades:
            return BacktestTradeSummary()
        planned_risk_values = [trade.planned_risk_usd for trade in trades if trade.planned_risk_usd is not None]
        stop_values = [trade.planned_stop_loss_bps for trade in trades if trade.planned_stop_loss_bps is not None]
        take_profit_values = [trade.planned_take_profit_bps for trade in trades if trade.planned_take_profit_bps is not None]
        max_take_profit_values = [
            trade.planned_max_take_profit_bps
            for trade in trades
            if trade.planned_max_take_profit_bps is not None
        ]
        trailing_values = [
            trade.planned_trailing_stop_bps
            for trade in trades
            if trade.planned_trailing_stop_bps is not None
        ]
        exit_reason_counts = Counter(trade.exit_reason for trade in trades)
        winners = sum(1 for trade in trades if trade.pnl_usd > 0)
        losers = sum(1 for trade in trades if trade.pnl_usd < 0)
        breakeven = len(trades) - winners - losers
        return BacktestTradeSummary(
            total_trades=len(trades),
            winning_trades=winners,
            losing_trades=losers,
            breakeven_trades=breakeven,
            win_rate=(winners / len(trades)) if trades else 0.0,
            average_planned_risk_usd=(sum(planned_risk_values) / len(planned_risk_values)) if planned_risk_values else 0.0,
            average_planned_stop_loss_bps=(sum(stop_values) / len(stop_values)) if stop_values else 0.0,
            average_planned_take_profit_bps=(sum(take_profit_values) / len(take_profit_values)) if take_profit_values else 0.0,
            average_planned_max_take_profit_bps=(
                (sum(max_take_profit_values) / len(max_take_profit_values)) if max_take_profit_values else 0.0
            ),
            average_planned_trailing_stop_bps=(
                (sum(trailing_values) / len(trailing_values)) if trailing_values else 0.0
            ),
            average_bars_held=(sum(trade.bars_held for trade in trades) / len(trades)) if trades else 0.0,
            exit_reason_counts=dict(exit_reason_counts),
        )

    def _regime_summary(
        self,
        *,
        regime_counts: Counter[str],
        entry_regime_counts: Counter[str],
        regime_probability_sums: dict[str, float],
    ) -> BacktestRegimeSummary:
        average_regime_probability = {
            regime: regime_probability_sums.get(regime, 0.0) / count
            for regime, count in regime_counts.items()
            if count > 0
        }
        return BacktestRegimeSummary(
            regime_occupancy=dict(regime_counts),
            entry_regime_counts=dict(entry_regime_counts),
            average_regime_probability=average_regime_probability,
        )
