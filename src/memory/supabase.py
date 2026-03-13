from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from control_plane.models import (
    AgentConfigRecord,
    AgentHeartbeatRecord,
    BacktestJobRequest,
    PolicyVersionRecord,
)
from data.schemas import (
    BacktestTradeRecord,
    BacktestWindowSummary,
    BridgeCommand,
    BridgeSnapshot,
    ExecutionAck,
    HistoricalBar,
    LessonRecord,
    OrderSnapshot,
    PromotionDecision,
    ReplayMetrics,
    TradeReflection,
    TradeReview,
)


class SupabaseStore:
    def __init__(self, db_url: str) -> None:
        self._db_url = db_url

    def _normalize_value(self, value: Any) -> Any:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, list):
            return [self._normalize_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._normalize_value(item) for item in value)
        if isinstance(value, dict):
            return {key: self._normalize_value(item) for key, item in value.items()}
        return value

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {key: self._normalize_value(value) for key, value in row.items()}

    def upsert_policy_version(
        self,
        *,
        policy_name: str,
        version: str,
        status: str,
        thresholds: dict[str, object],
        risk_params: dict[str, object],
        strategy_config: dict[str, object],
        notes: str,
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.policy_versions
                      (policy_name, version, status, thresholds, risk_params, strategy_config, notes)
                    values
                      (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (policy_name, version) do update
                      set status = excluded.status,
                          thresholds = excluded.thresholds,
                          risk_params = excluded.risk_params,
                          strategy_config = excluded.strategy_config,
                          notes = excluded.notes
                    returning id
                    """,
                    (
                        policy_name,
                        version,
                        status,
                        Jsonb(thresholds),
                        Jsonb(risk_params),
                        Jsonb(strategy_config),
                        notes,
                    ),
                )
                return str(cur.fetchone()["id"])

    def list_policy_versions(self) -> list[PolicyVersionRecord]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id, policy_name, version, status, thresholds, risk_params, strategy_config, notes
                    from public.policy_versions
                    order by policy_name asc, version desc
                    """
                )
                rows = cur.fetchall()
        return [PolicyVersionRecord.model_validate(self._normalize_row(row)) for row in rows]

    def get_policy_version(self, policy_version_id: str) -> PolicyVersionRecord | None:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id, policy_name, version, status, thresholds, risk_params, strategy_config, notes
                    from public.policy_versions
                    where id = %s
                    """,
                    (policy_version_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return PolicyVersionRecord.model_validate(self._normalize_row(row))

    def get_policy_versions(self, policy_version_ids: Sequence[str]) -> list[PolicyVersionRecord]:
        if not policy_version_ids:
            return []
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id, policy_name, version, status, thresholds, risk_params, strategy_config, notes
                    from public.policy_versions
                    where id = any(%s)
                    order by policy_name asc, version desc
                    """,
                    (list(policy_version_ids),),
                )
                rows = cur.fetchall()
        return [PolicyVersionRecord.model_validate(self._normalize_row(row)) for row in rows]

    def list_agent_configs(self) -> list[AgentConfigRecord]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id, agent_name, description, status, broker, mode, symbols,
                           decision_interval_seconds, max_trades_per_hour, max_risk_per_trade_pct,
                           max_daily_loss_pct, max_position_notional_usd, max_spread_bps,
                           min_decision_confidence, cooldown_seconds_after_trade, enable_agent_orders,
                           strategy_policy_version_id, risk_params, analyst_params, execution_params, notes
                    from public.agent_configs
                    order by agent_name asc
                    """
                )
                rows = cur.fetchall()
        return [AgentConfigRecord.model_validate(self._normalize_row(row)) for row in rows]

    def get_agent_config(self, agent_name: str) -> AgentConfigRecord | None:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id, agent_name, description, status, broker, mode, symbols,
                           decision_interval_seconds, max_trades_per_hour, max_risk_per_trade_pct,
                           max_daily_loss_pct, max_position_notional_usd, max_spread_bps,
                           min_decision_confidence, cooldown_seconds_after_trade, enable_agent_orders,
                           strategy_policy_version_id, risk_params, analyst_params, execution_params, notes
                    from public.agent_configs
                    where agent_name = %s
                    """,
                    (agent_name,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return AgentConfigRecord.model_validate(self._normalize_row(row))

    def get_agent_config_by_id(self, agent_config_id: str) -> AgentConfigRecord | None:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id, agent_name, description, status, broker, mode, symbols,
                           decision_interval_seconds, max_trades_per_hour, max_risk_per_trade_pct,
                           max_daily_loss_pct, max_position_notional_usd, max_spread_bps,
                           min_decision_confidence, cooldown_seconds_after_trade, enable_agent_orders,
                           strategy_policy_version_id, risk_params, analyst_params, execution_params, notes
                    from public.agent_configs
                    where id = %s
                    """,
                    (agent_config_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return AgentConfigRecord.model_validate(self._normalize_row(row))

    def upsert_agent_config(self, config: AgentConfigRecord) -> str:
        payload = config.model_dump(mode="json")
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.agent_configs (
                      agent_name, description, status, broker, mode, symbols,
                      decision_interval_seconds, max_trades_per_hour, max_risk_per_trade_pct,
                      max_daily_loss_pct, max_position_notional_usd, max_spread_bps,
                      min_decision_confidence, cooldown_seconds_after_trade, enable_agent_orders,
                      strategy_policy_version_id, risk_params, analyst_params, execution_params, notes
                    )
                    values (
                      %(agent_name)s, %(description)s, %(status)s, %(broker)s, %(mode)s, %(symbols)s,
                      %(decision_interval_seconds)s, %(max_trades_per_hour)s, %(max_risk_per_trade_pct)s,
                      %(max_daily_loss_pct)s, %(max_position_notional_usd)s, %(max_spread_bps)s,
                      %(min_decision_confidence)s, %(cooldown_seconds_after_trade)s, %(enable_agent_orders)s,
                      %(strategy_policy_version_id)s, %(risk_params)s, %(analyst_params)s, %(execution_params)s, %(notes)s
                    )
                    on conflict (agent_name) do update
                      set description = excluded.description,
                          status = excluded.status,
                          broker = excluded.broker,
                          mode = excluded.mode,
                          symbols = excluded.symbols,
                          decision_interval_seconds = excluded.decision_interval_seconds,
                          max_trades_per_hour = excluded.max_trades_per_hour,
                          max_risk_per_trade_pct = excluded.max_risk_per_trade_pct,
                          max_daily_loss_pct = excluded.max_daily_loss_pct,
                          max_position_notional_usd = excluded.max_position_notional_usd,
                          max_spread_bps = excluded.max_spread_bps,
                          min_decision_confidence = excluded.min_decision_confidence,
                          cooldown_seconds_after_trade = excluded.cooldown_seconds_after_trade,
                          enable_agent_orders = excluded.enable_agent_orders,
                          strategy_policy_version_id = excluded.strategy_policy_version_id,
                          risk_params = excluded.risk_params,
                          analyst_params = excluded.analyst_params,
                          execution_params = excluded.execution_params,
                          notes = excluded.notes
                    returning id
                    """,
                    {
                        **payload,
                        "symbols": Jsonb(payload["symbols"]),
                        "risk_params": Jsonb(payload["risk_params"]),
                        "analyst_params": Jsonb(payload["analyst_params"]),
                        "execution_params": Jsonb(payload["execution_params"]),
                    },
                )
                return str(cur.fetchone()["id"])

    def promote_strategy(
        self,
        *,
        agent_config_id: str,
        new_policy_version_id: str,
        rationale: str,
        promoted_by: str = "dashboard-ui",
        source_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select strategy_policy_version_id
                    from public.agent_configs
                    where id = %s
                    """,
                    (agent_config_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("Agent config not found.")
                previous_policy_version_id = row["strategy_policy_version_id"]

                cur.execute(
                    """
                    update public.agent_configs
                    set strategy_policy_version_id = %s
                    where id = %s
                    """,
                    (new_policy_version_id, agent_config_id),
                )
                cur.execute(
                    """
                    insert into public.agent_strategy_promotions (
                      agent_config_id, previous_policy_version_id, new_policy_version_id, source_run_id,
                      promoted_by, rationale, metadata
                    )
                    values (%s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        agent_config_id,
                        previous_policy_version_id,
                        new_policy_version_id,
                        source_run_id,
                        promoted_by,
                        rationale,
                        Jsonb(metadata or {}),
                    ),
                )
                promotion_id = cur.fetchone()["id"]
        return str(promotion_id)

    def list_strategy_promotions(
        self, limit: int = 100, agent_name: str | None = None
    ) -> list[dict[str, Any]]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                where_clause = "where agent.agent_name = %s" if agent_name is not None else ""
                params: tuple[Any, ...] = (agent_name, limit) if agent_name is not None else (limit,)
                cur.execute(
                    f"""
                    select promotion.id,
                           promotion.agent_config_id,
                           agent.agent_name,
                           promotion.previous_policy_version_id,
                           previous_policy.policy_name as previous_policy_name,
                           previous_policy.version as previous_policy_version,
                           promotion.new_policy_version_id,
                           new_policy.policy_name as new_policy_name,
                           new_policy.version as new_policy_version,
                           promotion.source_run_id,
                           run.run_name as source_run_name,
                           promotion.promoted_by,
                           promotion.rationale,
                           promotion.metadata,
                           promotion.created_at
                    from public.agent_strategy_promotions as promotion
                    join public.agent_configs as agent on agent.id = promotion.agent_config_id
                    left join public.policy_versions as previous_policy on previous_policy.id = promotion.previous_policy_version_id
                    left join public.policy_versions as new_policy on new_policy.id = promotion.new_policy_version_id
                    left join public.backtest_runs as run on run.id = promotion.source_run_id
                    {where_clause}
                    order by promotion.created_at desc
                    limit %s
                    """,
                    params,
                )
                rows = cur.fetchall()
        return [self._normalize_row(dict(row)) for row in rows]

    def list_agent_status(self) -> list[dict[str, Any]]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select *
                    from public.agent_dashboard_status
                    order by agent_name asc
                    """
                )
                rows = cur.fetchall()
        return [self._normalize_row(dict(row)) for row in rows]

    def record_agent_heartbeat(self, heartbeat: AgentHeartbeatRecord) -> None:
        payload = heartbeat.model_dump(mode="json")
        with psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.agent_heartbeats (
                      agent_config_id, runtime_id, status, current_symbol, latest_decision_action,
                      latest_decision_at, latest_order_at, open_position_qty, cash, equity, details
                    )
                    values (
                      %(agent_config_id)s, %(runtime_id)s, %(status)s, %(current_symbol)s, %(latest_decision_action)s,
                      %(latest_decision_at)s, %(latest_order_at)s, %(open_position_qty)s, %(cash)s, %(equity)s, %(details)s
                    )
                    on conflict (agent_config_id, runtime_id) do update
                      set status = excluded.status,
                          current_symbol = excluded.current_symbol,
                          latest_decision_action = excluded.latest_decision_action,
                          latest_decision_at = excluded.latest_decision_at,
                          latest_order_at = excluded.latest_order_at,
                          open_position_qty = excluded.open_position_qty,
                          cash = excluded.cash,
                          equity = excluded.equity,
                          details = excluded.details,
                          observed_at = timezone('utc', now())
                    """,
                    {
                        **payload,
                        "details": Jsonb(payload["details"]),
                    },
                )

    def insert_decision_record(
        self,
        *,
        agent_config_id: str | None,
        agent_name: str,
        symbol: str,
        action: str,
        decision_confidence: float,
        rationale: str,
        risk_approved: bool,
        risk_reason: str,
        allowed_notional_usd: Decimal,
        trades_this_hour: int,
        reference_price: Decimal | None,
        spread_bps: float | None,
        market_timestamp: datetime | None,
        policy_version_id: str | None,
        market_snapshot: dict[str, Any],
        account_snapshot: dict[str, Any],
        features: dict[str, Any],
        decision_payload: dict[str, Any],
        risk_payload: dict[str, Any],
        analyst_model: str | None = None,
        analyst_prompt_version: str | None = None,
        record_source: str = "agent",
        notes: str | None = None,
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.decisions (
                      agent_config_id, agent_name, symbol, action, decision_confidence, rationale,
                      risk_approved, risk_reason, allowed_notional_usd, trades_this_hour,
                      reference_price, spread_bps, market_timestamp, policy_version_id, analyst_model,
                      analyst_prompt_version, record_source, notes,
                      market_snapshot, account_snapshot, features, decision_payload, risk_payload
                    )
                    values (
                      %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s,
                      %s, %s, %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s, %s, %s
                    )
                    returning id
                    """,
                    (
                        agent_config_id,
                        agent_name,
                        symbol,
                        action,
                        decision_confidence,
                        rationale,
                        risk_approved,
                        risk_reason,
                        allowed_notional_usd,
                        trades_this_hour,
                        reference_price,
                        spread_bps,
                        market_timestamp,
                        policy_version_id,
                        analyst_model,
                        analyst_prompt_version,
                        record_source,
                        notes,
                        Jsonb(market_snapshot),
                        Jsonb(account_snapshot),
                        Jsonb(features),
                        Jsonb(decision_payload),
                        Jsonb(risk_payload),
                    ),
                )
                return str(cur.fetchone()["id"])

    def upsert_order_record(
        self,
        *,
        agent_config_id: str | None,
        agent_name: str,
        decision_id: str | None,
        order: OrderSnapshot,
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.orders (
                      decision_id, agent_config_id, agent_name, external_order_id, client_order_id,
                      symbol, side, order_type, time_in_force, status, requested_notional, requested_qty,
                      filled_qty, filled_avg_price, submitted_at, last_updated_at, raw_order
                    )
                    values (
                      %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s
                    )
                    on conflict (external_order_id) do update
                      set decision_id = excluded.decision_id,
                          agent_config_id = excluded.agent_config_id,
                          agent_name = excluded.agent_name,
                          status = excluded.status,
                          filled_qty = excluded.filled_qty,
                          filled_avg_price = excluded.filled_avg_price,
                          last_updated_at = excluded.last_updated_at,
                          raw_order = excluded.raw_order
                    returning id
                    """,
                    (
                        decision_id,
                        agent_config_id,
                        agent_name,
                        order.id,
                        order.client_order_id,
                        order.symbol,
                        order.side,
                        order.type,
                        order.time_in_force,
                        order.status,
                        order.notional,
                        order.qty,
                        order.filled_qty,
                        order.filled_avg_price,
                        order.created_at,
                        order.updated_at,
                        Jsonb(order.model_dump(mode="json")),
                    ),
                )
                return str(cur.fetchone()["id"])

    def insert_trade_outcome(
        self,
        *,
        agent_config_id: str | None,
        agent_name: str,
        decision_id: str | None,
        order_id: str | None,
        review: TradeReview,
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.trade_outcomes (
                      review_id, decision_id, order_id, agent_config_id, agent_name, symbol, action,
                      outcome, summary, decision_confidence, spread_bps, failure_mode, cash_delta,
                      position_qty_delta, filled_qty, filled_avg_price, lesson_candidates, raw_review
                    )
                    values (
                      %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s
                    )
                    returning id
                    """,
                    (
                        review.review_id,
                        decision_id,
                        order_id,
                        agent_config_id,
                        agent_name,
                        review.symbol,
                        review.action,
                        review.outcome,
                        review.summary,
                        review.decision_confidence,
                        review.spread_bps,
                        review.failure_mode,
                        review.cash_delta,
                        review.position_qty_delta,
                        review.filled_qty,
                        review.filled_avg_price,
                        Jsonb(review.lesson_candidates),
                        Jsonb(review.model_dump(mode="json")),
                    ),
                )
                return str(cur.fetchone()["id"])

    def upsert_lessons(self, lessons: Sequence[LessonRecord], policy_version_id: str | None = None) -> int:
        if not lessons:
            return 0
        inserted = 0
        with psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                for lesson in lessons:
                    cur.execute(
                        """
                        insert into public.lessons (
                          lesson_id, category, message, confidence, source, status, policy_version_id, metadata
                        )
                        values (%s, %s, %s, %s, %s, 'active', %s, %s)
                        on conflict (category, message, source) do update
                          set confidence = greatest(public.lessons.confidence, excluded.confidence),
                              policy_version_id = coalesce(excluded.policy_version_id, public.lessons.policy_version_id),
                              metadata = public.lessons.metadata || excluded.metadata,
                              occurrence_count = public.lessons.occurrence_count + 1,
                              last_seen_at = timezone('utc', now()),
                              updated_at = timezone('utc', now())
                        """,
                        (
                            lesson.lesson_id,
                            lesson.category,
                            lesson.message,
                            lesson.confidence,
                            lesson.source,
                            policy_version_id,
                            Jsonb(lesson.metadata),
                        ),
                    )
                    inserted += cur.rowcount
        return inserted

    def upsert_market_bars(self, bars: Sequence[HistoricalBar]) -> int:
        if not bars:
            return 0
        inserted = 0
        with psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                for start_index in range(0, len(bars), 1000):
                    batch = bars[start_index : start_index + 1000]
                    cur.executemany(
                        """
                        insert into public.market_bars
                          (symbol, timeframe, location, bar_timestamp, open_price, high_price, low_price, close_price, volume, trade_count, vwap, source, raw_bar)
                        values
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'alpaca', %s)
                        on conflict (symbol, timeframe, location, bar_timestamp) do nothing
                        """,
                        [
                            (
                                bar.symbol,
                                bar.timeframe,
                                bar.location,
                                bar.timestamp,
                                bar.open_price,
                                bar.high_price,
                                bar.low_price,
                                bar.close_price,
                                bar.volume,
                                bar.trade_count,
                                bar.vwap,
                                Jsonb(bar.raw_bar),
                            )
                            for bar in batch
                        ],
                    )
                    inserted += cur.rowcount
        return inserted

    def load_market_bars(
        self,
        *,
        symbol: str,
        timeframe: str,
        location: str,
        start: datetime,
        end: datetime,
        include_raw_bar: bool = False,
    ) -> list[HistoricalBar]:
        select_raw_bar = ", raw_bar" if include_raw_bar else ""
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("set local statement_timeout = 0")
                cur.execute(
                    f"""
                    select symbol, timeframe, location, bar_timestamp, open_price, high_price, low_price, close_price, volume, trade_count, vwap{select_raw_bar}
                    from public.market_bars
                    where symbol = %s and timeframe = %s and location = %s and bar_timestamp >= %s and bar_timestamp <= %s
                    order by bar_timestamp asc
                    """,
                    (symbol, timeframe, location, start, end),
                )
                rows = cur.fetchall()
        return [
            HistoricalBar(
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                location=row["location"],
                timestamp=row["bar_timestamp"],
                open_price=row["open_price"],
                high_price=row["high_price"],
                low_price=row["low_price"],
                close_price=row["close_price"],
                volume=row["volume"],
                trade_count=row["trade_count"],
                vwap=row["vwap"],
                raw_bar=row["raw_bar"] if include_raw_bar else {},
            )
            for row in rows
        ]

    def create_backtest_job(self, request: BacktestJobRequest, requested_by: str = "dashboard") -> str:
        payload = request.model_dump(mode="json")
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.backtest_jobs (
                      requested_by, agent_config_id, run_name, status, symbol, timeframe, location, lookback_days,
                      train_window_days, test_window_days, step_days, warmup_bars, starting_cash_usd,
                      baseline_policy_version_id, candidate_policy_version_ids, notes
                    )
                    values (
                      %s, %s, %s, 'queued', %s, %s, %s, %s,
                      %s, %s, %s, %s, %s,
                      %s, %s, %s
                    )
                    returning id
                    """,
                    (
                        requested_by,
                        payload["agent_config_id"],
                        payload["run_name"],
                        payload["symbol"],
                        payload["timeframe"],
                        payload["location"],
                        payload["lookback_days"],
                        payload["train_window_days"],
                        payload["test_window_days"],
                        payload["step_days"],
                        payload["warmup_bars"],
                        payload["starting_cash_usd"],
                        payload["baseline_policy_version_id"],
                        Jsonb(payload["candidate_policy_version_ids"]),
                        payload["notes"],
                    ),
                )
                return str(cur.fetchone()["id"])

    def update_backtest_job(
        self,
        *,
        job_id: str,
        status: str,
        run_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                started_at = datetime.utcnow() if status == "running" else None
                completed_at = datetime.utcnow() if status in {"completed", "failed"} else None
                cur.execute(
                    """
                    update public.backtest_jobs
                    set status = %s,
                        run_id = coalesce(%s, run_id),
                        error_message = %s,
                        started_at = coalesce(%s, started_at),
                        completed_at = coalesce(%s, completed_at)
                    where id = %s
                    """,
                    (status, run_id, error_message, started_at, completed_at, job_id),
                )

    def list_backtest_jobs(self, limit: int = 25) -> list[dict[str, Any]]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select job.id, job.requested_by, job.run_name, job.status, job.symbol, job.timeframe,
                           job.lookback_days, job.train_window_days, job.test_window_days, job.step_days,
                           job.warmup_bars, job.starting_cash_usd, job.notes, job.error_message,
                           job.requested_at, job.started_at, job.completed_at, job.run_id,
                           baseline.policy_name as baseline_policy_name, baseline.version as baseline_version
                    from public.backtest_jobs as job
                    left join public.policy_versions as baseline on baseline.id = job.baseline_policy_version_id
                    order by job.requested_at desc
                    limit %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
        return [self._normalize_row(dict(row)) for row in rows]

    def create_backtest_run(
        self,
        *,
        run_name: str,
        symbol: str,
        timeframe: str,
        location: str,
        start_at: datetime,
        end_at: datetime,
        train_window_days: int,
        test_window_days: int,
        step_days: int,
        warmup_bars: int,
        starting_cash_usd: float,
        bars_inserted: int,
        total_bars: int,
        baseline_policy_version_id: str,
        candidate_policy_version_id: str,
        agent_config_id: str | None = None,
        agent_name: str = "primary",
        backtest_job_id: str | None = None,
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.backtest_runs
                      (run_name, symbol, timeframe, location, start_at, end_at, train_window_days, test_window_days, step_days, warmup_bars, starting_cash_usd, bars_inserted, total_bars, baseline_policy_version_id, candidate_policy_version_id, agent_config_id, agent_name, backtest_job_id)
                    values
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        run_name,
                        symbol,
                        timeframe,
                        location,
                        start_at,
                        end_at,
                        train_window_days,
                        test_window_days,
                        step_days,
                        warmup_bars,
                        starting_cash_usd,
                        bars_inserted,
                        total_bars,
                        baseline_policy_version_id,
                        candidate_policy_version_id,
                        agent_config_id,
                        agent_name,
                        backtest_job_id,
                    ),
                )
                return str(cur.fetchone()["id"])

    def insert_backtest_window_results(
        self,
        *,
        run_id: str,
        window_summaries: Sequence[BacktestWindowSummary],
        policy_version_ids: dict[str, str],
    ) -> dict[tuple[int, str], str]:
        created: dict[tuple[int, str], str] = {}
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                for summary in window_summaries:
                    for policy_name, metrics, version_id, stored_name in (
                        ("baseline", summary.baseline_test_metrics, policy_version_ids.get("baseline"), "baseline"),
                        (
                            summary.selected_policy_name,
                            summary.selected_test_metrics,
                            policy_version_ids.get("walk_forward_best"),
                            "walk_forward_best",
                        ),
                    ):
                        cur.execute(
                            """
                            insert into public.backtest_window_results
                              (run_id, window_index, policy_version_id, policy_name, train_start_at, train_end_at, test_start_at, test_end_at, metrics)
                            values
                              (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            on conflict (run_id, window_index, policy_name) do update
                              set metrics = excluded.metrics
                            returning id
                            """,
                            (
                                run_id,
                                summary.window_index,
                                version_id,
                                stored_name,
                                summary.train_start_at,
                                summary.train_end_at,
                                summary.test_start_at,
                                summary.test_end_at,
                                Jsonb(
                                    {
                                        "train_scores": summary.train_scores,
                                        "metrics": summary.selected_test_metrics.model_dump(mode="json")
                                        if stored_name == "walk_forward_best"
                                        else metrics.model_dump(mode="json"),
                                        "selected_policy_name": summary.selected_policy_name,
                                        "baseline_metrics": summary.baseline_test_metrics.model_dump(mode="json"),
                                    }
                                ),
                            ),
                        )
                        window_id = str(cur.fetchone()["id"])
                        created[(summary.window_index, stored_name)] = window_id
                        created[(summary.window_index, policy_name)] = window_id
        return created

    def insert_backtest_trades(
        self,
        *,
        run_id: str,
        trades: Sequence[BacktestTradeRecord],
        policy_version_ids: dict[str, str],
        window_lookup: dict[tuple[int, str], str],
        window_index_by_trade: Sequence[int],
    ) -> None:
        if not trades:
            return
        with psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    insert into public.backtest_trades
                      (run_id, window_id, policy_version_id, policy_name, symbol, side, entry_at, exit_at, entry_price, exit_price, qty, notional_usd, pnl_usd, return_bps, bars_held, exit_reason)
                    values
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            run_id,
                            window_lookup.get((window_index, trade.policy_name)),
                            policy_version_ids.get(trade.policy_name),
                            trade.policy_name,
                            trade.symbol,
                            trade.side,
                            trade.entry_at,
                            trade.exit_at,
                            trade.entry_price,
                            trade.exit_price,
                            trade.qty,
                            trade.notional_usd,
                            trade.pnl_usd,
                            trade.return_bps,
                            trade.bars_held,
                            trade.exit_reason,
                        )
                        for trade, window_index in zip(trades, window_index_by_trade)
                    ],
                )

    def finalize_backtest_run(
        self,
        *,
        run_id: str,
        status: str,
        baseline: ReplayMetrics,
        candidate: ReplayMetrics,
        decision: PromotionDecision,
        notes: str,
    ) -> None:
        with psycopg.connect(self._db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update public.backtest_runs
                    set status = %s,
                        baseline_metrics = %s,
                        candidate_metrics = %s,
                        decision_payload = %s,
                        notes = %s
                    where id = %s
                    """,
                    (
                        status,
                        Jsonb(baseline.model_dump(mode="json")),
                        Jsonb(candidate.model_dump(mode="json")),
                        Jsonb(decision.model_dump(mode="json")),
                        notes,
                        run_id,
                    ),
                )

    def list_backtest_runs(self, limit: int = 25) -> list[dict[str, Any]]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select run.id, run.run_name, run.status, run.symbol, run.timeframe, run.location,
                           run.start_at, run.end_at, run.created_at, run.agent_name, run.total_bars,
                           run.baseline_metrics, run.candidate_metrics, run.decision_payload,
                           baseline.policy_name as baseline_policy_name, baseline.version as baseline_version,
                           candidate.policy_name as candidate_policy_name, candidate.version as candidate_version
                    from public.backtest_runs as run
                    left join public.policy_versions as baseline on baseline.id = run.baseline_policy_version_id
                    left join public.policy_versions as candidate on candidate.id = run.candidate_policy_version_id
                    order by run.created_at desc
                    limit %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
        return [self._normalize_row(dict(row)) for row in rows]

    def get_backtest_run_details(self, run_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select *
                    from public.backtest_runs
                    where id = %s
                    """,
                    (run_id,),
                )
                run = cur.fetchone()
                if run is None:
                    return None
                cur.execute(
                    """
                    select id, window_index, policy_name, train_start_at, train_end_at, test_start_at, test_end_at, metrics
                    from public.backtest_window_results
                    where run_id = %s
                    order by window_index asc, policy_name asc
                    """,
                    (run_id,),
                )
                windows = cur.fetchall()
                cur.execute(
                    """
                    select policy_name, symbol, side, entry_at, exit_at, entry_price, exit_price, qty,
                           notional_usd, pnl_usd, return_bps, bars_held, exit_reason
                    from public.backtest_trades
                    where run_id = %s
                    order by entry_at asc
                    """,
                    (run_id,),
                )
                trades = cur.fetchall()
        return {
            "run": self._normalize_row(dict(run)),
            "windows": [self._normalize_row(dict(row)) for row in windows],
            "trades": [self._normalize_row(dict(row)) for row in trades],
        }

    def list_recent_decisions(self, *, agent_name: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = """
            select recorded_at, agent_name, symbol, action, decision_confidence, rationale,
                   risk_approved, risk_reason, allowed_notional_usd, spread_bps
            from public.decisions
        """
        params: list[Any] = []
        if agent_name is not None:
            sql += " where agent_name = %s"
            params.append(agent_name)
        sql += " order by recorded_at desc limit %s"
        params.append(limit)
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [self._normalize_row(dict(row)) for row in rows]

    def list_recent_orders(self, *, agent_name: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = """
            select created_at, agent_name, symbol, side, status, requested_notional, requested_qty,
                   filled_qty, filled_avg_price
            from public.orders
        """
        params: list[Any] = []
        if agent_name is not None:
            sql += " where agent_name = %s"
            params.append(agent_name)
        sql += " order by created_at desc limit %s"
        params.append(limit)
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [self._normalize_row(dict(row)) for row in rows]

    def list_recent_trade_outcomes(
        self, *, agent_name: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = """
            select recorded_at, agent_name, symbol, action, outcome, summary, decision_confidence,
                   spread_bps, failure_mode, cash_delta, position_qty_delta, filled_qty, filled_avg_price
            from public.trade_outcomes
        """
        params: list[Any] = []
        if agent_name is not None:
            sql += " where agent_name = %s"
            params.append(agent_name)
        sql += " order by recorded_at desc limit %s"
        params.append(limit)
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [self._normalize_row(dict(row)) for row in rows]

    def list_recent_lessons(self, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select category, message, confidence, source, status, occurrence_count, last_seen_at
                    from public.lessons
                    order by last_seen_at desc
                    limit %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
        return [self._normalize_row(dict(row)) for row in rows]

    def insert_mt5_bridge_snapshot(self, *, agent_name: str, snapshot: BridgeSnapshot) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_bridge_snapshots (
                      bridge_id, agent_name, symbol, server_time, received_at, spread_bps,
                      snapshot_payload, health_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        snapshot.bridge_id,
                        agent_name,
                        snapshot.symbol,
                        snapshot.server_time,
                        snapshot.received_at,
                        snapshot.spread_bps,
                        Jsonb(snapshot.model_dump(mode="json")),
                        Jsonb(snapshot.health.model_dump(mode="json")),
                    ),
                )
                return str(cur.fetchone()["id"])

    def insert_mt5_runtime_decision(
        self,
        *,
        agent_name: str,
        decision_kind: str,
        symbol: str,
        action: str,
        confidence: float,
        rationale: str,
        risk_posture: str,
        risk_approved: bool | None,
        risk_reason: str | None,
        context_payload: dict[str, Any],
        decision_payload: dict[str, Any],
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_runtime_decisions (
                      agent_name, decision_kind, symbol, action, confidence, rationale,
                      risk_posture, risk_approved, risk_reason, context_payload, decision_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        agent_name,
                        decision_kind,
                        symbol,
                        action,
                        confidence,
                        rationale,
                        risk_posture,
                        risk_approved,
                        risk_reason,
                        Jsonb(context_payload),
                        Jsonb(decision_payload),
                    ),
                )
                return str(cur.fetchone()["id"])

    def insert_mt5_bridge_command(
        self,
        *,
        agent_name: str,
        command: BridgeCommand,
        status: str = "queued",
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_bridge_commands (
                      command_id, bridge_id, agent_name, symbol, command_type, status,
                      ticket_id, basket_id, created_at, expires_at, reason, command_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (command_id) do update
                      set status = excluded.status,
                          ticket_id = excluded.ticket_id,
                          basket_id = excluded.basket_id,
                          expires_at = excluded.expires_at,
                          reason = excluded.reason,
                          command_payload = excluded.command_payload
                    returning id
                    """,
                    (
                        command.command_id,
                        "mt5-local",
                        agent_name,
                        command.symbol,
                        command.command_type,
                        status,
                        command.ticket_id,
                        command.basket_id,
                        command.created_at,
                        command.expires_at,
                        command.reason,
                        Jsonb(command.model_dump(mode="json")),
                    ),
                )
                return str(cur.fetchone()["id"])

    def insert_mt5_bridge_ack(self, *, agent_name: str, ack: ExecutionAck) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_bridge_acks (
                      command_id, agent_name, ack_status, ticket_id, broker_time, message, ack_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        ack.command_id,
                        agent_name,
                        ack.status,
                        ack.ticket_id,
                        ack.broker_time,
                        ack.message,
                        Jsonb(ack.model_dump(mode="json")),
                    ),
                )
                ack_id = str(cur.fetchone()["id"])
                cur.execute(
                    """
                    update public.mt5_bridge_commands
                    set status = %s,
                        ack_payload = %s
                    where command_id = %s
                    """,
                    (
                        ack.status,
                        Jsonb(ack.model_dump(mode="json")),
                        ack.command_id,
                    ),
                )
                return ack_id

    def insert_mt5_trade_reflection(
        self,
        *,
        agent_name: str,
        reflection: TradeReflection,
        ticket_id: str | None = None,
        basket_id: str | None = None,
        risk_posture: str | None = None,
    ) -> str:
        with psycopg.connect(self._db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into public.mt5_trade_reflections (
                      reflection_id, agent_name, symbol, side, ticket_id, basket_id, risk_posture,
                      opened_at, closed_at, realized_pnl_usd, realized_r, exit_reason, reflection_payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (reflection_id) do update
                      set realized_pnl_usd = excluded.realized_pnl_usd,
                          realized_r = excluded.realized_r,
                          exit_reason = excluded.exit_reason,
                          reflection_payload = excluded.reflection_payload
                    returning id
                    """,
                    (
                        reflection.reflection_id,
                        agent_name,
                        reflection.symbol,
                        reflection.side,
                        ticket_id,
                        basket_id,
                        risk_posture,
                        reflection.opened_at,
                        reflection.closed_at,
                        reflection.realized_pnl_usd,
                        reflection.realized_r,
                        reflection.exit_reason,
                        Jsonb(reflection.model_dump(mode="json")),
                    ),
                )
                return str(cur.fetchone()["id"])
