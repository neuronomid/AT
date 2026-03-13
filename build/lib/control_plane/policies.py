from decimal import Decimal

from agents.analyst import AnalystAgent
from agents.hmm_analyst import HMMRegimeAnalystAgent
from agents.llm_live_analyst import LLMLiveAnalystAgent
from app.config import Settings
from control_plane.models import AgentConfigRecord, PolicyVersionRecord
from risk.policy import RiskPolicy
from risk.v4_policy import V4RiskPolicy


DEFAULT_POLICY_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "policy_name": "baseline",
        "version": "v2.2",
        "status": "baseline",
        "thresholds": {
            "entry_momentum_3_bps": 7.0,
            "entry_momentum_5_bps": 11.0,
            "exit_momentum_3_bps": -4.0,
            "exit_momentum_5_bps": -8.0,
            "max_spread_bps": 12.0,
            "min_regime_probability": 0.65,
            "regime_trend_15_bps": 16.0,
            "regime_trend_30_bps": 30.0,
            "htf_trend_60_bps": 14.0,
            "htf_trend_240_bps": 34.0,
            "max_abs_zscore_30": 1.8,
            "min_trend_strength_bps": 16.0,
            "min_volume_ratio_5_30": 1.08,
            "min_entry_score": 5,
            "min_confirmation_count": 4,
            "breakout_buffer_bps": 0.7,
            "exit_regime_probability": 0.72,
            "hard_exit_momentum_3_bps": 7.0,
            "hard_exit_momentum_5_bps": 12.0,
        },
        "strategy_config": {
            "min_sample_count": 60,
            "max_volatility_5_bps": 22.0,
            "chaos_volatility_5_bps": 34.0,
            "min_stop_loss_bps": 10.0,
            "max_stop_loss_bps": 20.0,
            "stop_loss_vol_multiplier": 0.95,
            "trailing_stop_multiple": 0.5,
            "partial_take_profit_fraction": 0.3,
            "max_reward_multiple": 2.4,
            "time_stop_bars": 9,
            "min_expected_edge_bps": 1.15,
            "min_atr_percentile_30": 0.2,
            "max_atr_percentile_30": 0.72,
            "estimated_fee_bps": 1.25,
            "base_slippage_bps": 0.9,
            "max_expected_slippage_bps": 3.8,
            "requested_risk_fraction": 0.0018,
            "max_requested_notional_fraction_cash": 0.08,
        },
        "notes": "V2.2 baseline tightened after the first 90-day review to cut low-edge churn and improve trade quality.",
    },
    {
        "policy_name": "conservative",
        "version": "v2.2",
        "status": "candidate",
        "thresholds": {
            "entry_momentum_3_bps": 7.0,
            "entry_momentum_5_bps": 10.5,
            "exit_momentum_3_bps": -3.5,
            "exit_momentum_5_bps": -7.5,
            "max_spread_bps": 11.5,
            "min_regime_probability": 0.66,
            "regime_trend_15_bps": 16.0,
            "regime_trend_30_bps": 30.0,
            "htf_trend_60_bps": 14.0,
            "htf_trend_240_bps": 32.0,
            "max_abs_zscore_30": 1.75,
            "min_trend_strength_bps": 16.0,
            "min_volume_ratio_5_30": 1.07,
            "min_entry_score": 5,
            "min_confirmation_count": 4,
            "breakout_buffer_bps": 0.6,
            "exit_regime_probability": 0.78,
            "hard_exit_momentum_3_bps": 9.0,
            "hard_exit_momentum_5_bps": 15.0,
        },
        "strategy_config": {
            "min_sample_count": 60,
            "max_volatility_5_bps": 21.0,
            "chaos_volatility_5_bps": 31.0,
            "min_stop_loss_bps": 10.0,
            "max_stop_loss_bps": 20.0,
            "stop_loss_vol_multiplier": 1.0,
            "trailing_stop_multiple": 0.5,
            "partial_take_profit_fraction": 0.3,
            "max_reward_multiple": 2.35,
            "time_stop_bars": 9,
            "min_expected_edge_bps": 1.0,
            "min_atr_percentile_30": 0.18,
            "max_atr_percentile_30": 0.75,
            "estimated_fee_bps": 1.25,
            "base_slippage_bps": 0.9,
            "max_expected_slippage_bps": 3.8,
            "requested_risk_fraction": 0.0017,
            "max_requested_notional_fraction_cash": 0.07,
        },
        "notes": "Conservative v2.2 candidate rebalanced after the second review to restore trade flow without reverting to churn.",
    },
    {
        "policy_name": "aggressive",
        "version": "v2.2",
        "status": "candidate",
        "thresholds": {
            "entry_momentum_3_bps": 6.5,
            "entry_momentum_5_bps": 10.0,
            "exit_momentum_3_bps": -5.0,
            "exit_momentum_5_bps": -9.0,
            "max_spread_bps": 12.5,
            "min_regime_probability": 0.63,
            "regime_trend_15_bps": 15.0,
            "regime_trend_30_bps": 27.0,
            "htf_trend_60_bps": 13.0,
            "htf_trend_240_bps": 30.0,
            "max_abs_zscore_30": 1.9,
            "min_trend_strength_bps": 14.0,
            "min_volume_ratio_5_30": 1.04,
            "min_entry_score": 5,
            "min_confirmation_count": 4,
            "breakout_buffer_bps": 0.5,
            "exit_regime_probability": 0.72,
            "hard_exit_momentum_3_bps": 7.0,
            "hard_exit_momentum_5_bps": 12.0,
        },
        "strategy_config": {
            "min_sample_count": 55,
            "max_volatility_5_bps": 22.0,
            "chaos_volatility_5_bps": 34.0,
            "min_stop_loss_bps": 10.0,
            "max_stop_loss_bps": 22.0,
            "stop_loss_vol_multiplier": 0.95,
            "trailing_stop_multiple": 0.5,
            "partial_take_profit_fraction": 0.3,
            "max_reward_multiple": 2.3,
            "time_stop_bars": 9,
            "min_expected_edge_bps": 0.95,
            "min_atr_percentile_30": 0.18,
            "max_atr_percentile_30": 0.78,
            "estimated_fee_bps": 1.25,
            "base_slippage_bps": 0.9,
            "max_expected_slippage_bps": 3.8,
            "requested_risk_fraction": 0.0018,
            "max_requested_notional_fraction_cash": 0.08,
        },
        "notes": "Active v2.2 candidate tightened after the second review to cut microstructure churn while keeping a meaningful trade count.",
    },
)

HMM_V3_THRESHOLDS: dict[str, object] = {
    "entry_momentum_3_bps": 4.0,
    "entry_momentum_5_bps": 7.0,
    "exit_momentum_3_bps": -3.0,
    "exit_momentum_5_bps": -6.0,
    "max_spread_bps": 12.0,
    "min_trend_strength_bps": 8.0,
    "min_volume_ratio_5_30": 1.05,
    "min_entry_score": 6,
    "min_confirmation_count": 6,
    "breakout_buffer_bps": 1.2,
    "max_abs_zscore_30": 2.0,
}

HMM_V3_STRATEGY_CONFIG: dict[str, object] = {
    "strategy_family": "hmm_regime_v3",
    "hmm_state_count": 4,
    "hmm_resample_minutes": 15,
    "hmm_train_window_bars": 20 * 24 * 4,
    "hmm_retrain_interval_bars": 24 * 4,
    "hmm_bull_entry_probability": 0.62,
    "hmm_bull_continuation_probability": 0.58,
    "hmm_bear_exit_probability": 0.52,
    "hmm_stress_exit_probability": 0.48,
    "min_stop_loss_bps": 18.0,
    "max_stop_loss_bps": 60.0,
    "stop_loss_vol_multiplier": 1.25,
    "trailing_stop_multiple": 0.75,
    "partial_take_profit_fraction": 0.4,
    "take_profit_multiple": 1.25,
    "max_reward_multiple": 2.4,
    "time_stop_bars": 180,
    "min_expected_edge_bps": 1.2,
    "min_atr_percentile_30": 0.18,
    "max_atr_percentile_30": 0.82,
    "estimated_fee_bps": 1.25,
    "base_slippage_bps": 0.9,
    "max_expected_slippage_bps": 4.5,
    "requested_risk_fraction": 0.0015,
    "max_requested_notional_fraction_cash": 0.08,
}

INVERSE_HMM_V3_STRATEGY_CONFIG: dict[str, object] = {
    **HMM_V3_STRATEGY_CONFIG,
    "strategy_family": "inverse_hmm_regime_v3",
    "hmm_bear_entry_probability": 0.62,
    "hmm_bear_continuation_probability": 0.58,
    "hmm_bull_exit_probability": 0.52,
}

V4_LIVE_THRESHOLDS: dict[str, object] = {
    "max_spread_bps": 20.0,
}

V4_LIVE_STRATEGY_CONFIG: dict[str, object] = {
    "strategy_family": "llm_live_v4",
    "decision_timeframe": "1m",
    "candle_lookback": 20,
    "min_confidence": 0.60,
    "max_trades_per_hour": 10,
    "risk_fraction_min": 0.0025,
    "risk_fraction_max": 0.015,
    "take_profit_r_min": 0.5,
    "take_profit_r_max": 2.0,
    "stop_loss_r": 1.0,
    "cooldown_seconds_after_trade": 0,
    "stale_after_seconds": 90,
    "max_bars_in_trade": 20,
    "daily_loss_pct": 0.02,
}


def ensure_default_policies(store, settings: Settings) -> dict[str, str]:
    policy_ids: dict[str, str] = {}
    for definition in DEFAULT_POLICY_DEFINITIONS:
        policy_ids[str(definition["policy_name"])] = store.upsert_policy_version(
            policy_name=str(definition["policy_name"]),
            version=str(definition["version"]),
            status=str(definition["status"]),
            thresholds=dict(definition["thresholds"]),
            risk_params={"max_position_notional_usd": str(settings.max_position_notional_usd)},
            strategy_config=dict(definition["strategy_config"]),
            notes=str(definition["notes"]),
        )
    policy_ids["v4_live"] = ensure_v4_live_policy(store)
    return policy_ids


def build_v4_live_policy(version: str = "v4.0") -> PolicyVersionRecord:
    return PolicyVersionRecord(
        id=f"v4-live-{version}",
        policy_name="v4_live",
        version=version,
        status="candidate",
        thresholds=dict(V4_LIVE_THRESHOLDS),
        risk_params={},
        strategy_config=dict(V4_LIVE_STRATEGY_CONFIG),
        notes="Adaptive live-only paper-trading LLM strategy for ETH/USD.",
    )


def ensure_v4_live_policy(store, version: str = "v4.0") -> str:
    policy = build_v4_live_policy(version=version)
    return store.upsert_policy_version(
        policy_name=policy.policy_name,
        version=policy.version,
        status=policy.status,
        thresholds=policy.thresholds,
        risk_params=policy.risk_params,
        strategy_config=policy.strategy_config,
        notes=policy.notes or "",
    )


def build_v4_runtime_components(
    *,
    policy: PolicyVersionRecord,
    settings: Settings,
) -> tuple[LLMLiveAnalystAgent, V4RiskPolicy]:
    strategy_config = policy.strategy_config
    thresholds = policy.thresholds
    analyst = LLMLiveAnalystAgent(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        prompt_version=policy.version,
    )
    risk = V4RiskPolicy(
        min_confidence=float(strategy_config.get("min_confidence", 0.60)),
        max_trades_per_hour=int(strategy_config.get("max_trades_per_hour", 10)),
        risk_fraction_min=float(strategy_config.get("risk_fraction_min", 0.0025)),
        risk_fraction_max=float(strategy_config.get("risk_fraction_max", 0.015)),
        take_profit_r_min=float(strategy_config.get("take_profit_r_min", 0.5)),
        take_profit_r_max=float(strategy_config.get("take_profit_r_max", 2.0)),
        max_spread_bps=float(thresholds.get("max_spread_bps", 20.0)),
        stale_after_seconds=int(strategy_config.get("stale_after_seconds", 90)),
        max_bars_in_trade=int(strategy_config.get("max_bars_in_trade", 20)),
        daily_loss_pct=float(strategy_config.get("daily_loss_pct", 0.02)),
    )
    return analyst, risk


def build_analyst_agent(policy: PolicyVersionRecord) -> AnalystAgent:
    thresholds = policy.thresholds
    strategy_config = policy.strategy_config
    strategy_family = str(strategy_config.get("strategy_family", "threshold_regime_v2"))
    if strategy_family in {"hmm_regime_v3", "inverse_hmm_regime_v3"}:
        return HMMRegimeAnalystAgent(
            policy_name=policy.label,
            hmm_state_count=int(strategy_config.get("hmm_state_count", 4)),
            hmm_resample_minutes=int(strategy_config.get("hmm_resample_minutes", 15)),
            hmm_train_window_bars=int(strategy_config.get("hmm_train_window_bars", 20 * 24 * 4)),
            hmm_retrain_interval_bars=int(strategy_config.get("hmm_retrain_interval_bars", 24 * 4)),
            hmm_bull_entry_probability=float(strategy_config.get("hmm_bull_entry_probability", 0.62)),
            hmm_bull_continuation_probability=float(strategy_config.get("hmm_bull_continuation_probability", 0.58)),
            hmm_bear_entry_probability=float(
                strategy_config.get("hmm_bear_entry_probability", strategy_config.get("hmm_bull_entry_probability", 0.62))
            ),
            hmm_bear_continuation_probability=float(
                strategy_config.get(
                    "hmm_bear_continuation_probability",
                    strategy_config.get("hmm_bull_continuation_probability", 0.58),
                )
            ),
            hmm_bull_exit_probability=float(
                strategy_config.get("hmm_bull_exit_probability", strategy_config.get("hmm_bear_exit_probability", 0.52))
            ),
            hmm_bear_exit_probability=float(strategy_config.get("hmm_bear_exit_probability", 0.52)),
            hmm_stress_exit_probability=float(strategy_config.get("hmm_stress_exit_probability", 0.48)),
            trade_direction="short" if strategy_family == "inverse_hmm_regime_v3" else "long",
            strategy_family=strategy_family,
            max_spread_bps=float(thresholds.get("max_spread_bps", 12.0)),
            exit_momentum_3_bps=float(thresholds.get("exit_momentum_3_bps", -3.0)),
            exit_momentum_5_bps=float(thresholds.get("exit_momentum_5_bps", -6.0)),
            entry_momentum_3_bps=float(thresholds.get("entry_momentum_3_bps", 4.0)),
            entry_momentum_5_bps=float(thresholds.get("entry_momentum_5_bps", 7.0)),
            max_abs_zscore_30=float(thresholds.get("max_abs_zscore_30", 2.0)),
            min_trend_strength_bps=float(thresholds.get("min_trend_strength_bps", 8.0)),
            min_volume_ratio_5_30=float(thresholds.get("min_volume_ratio_5_30", 1.05)),
            min_entry_score=int(thresholds.get("min_entry_score", 6)),
            min_confirmation_count=int(thresholds.get("min_confirmation_count", 6)),
            breakout_buffer_bps=float(thresholds.get("breakout_buffer_bps", 1.2)),
            min_stop_loss_bps=float(strategy_config.get("min_stop_loss_bps", 18.0)),
            max_stop_loss_bps=float(strategy_config.get("max_stop_loss_bps", 60.0)),
            stop_loss_vol_multiplier=float(strategy_config.get("stop_loss_vol_multiplier", 1.25)),
            trailing_stop_multiple=float(strategy_config.get("trailing_stop_multiple", 0.75)),
            partial_take_profit_fraction=float(strategy_config.get("partial_take_profit_fraction", 0.4)),
            take_profit_multiple=float(strategy_config.get("take_profit_multiple", 1.25)),
            max_reward_multiple=float(strategy_config.get("max_reward_multiple", 2.4)),
            time_stop_bars=int(strategy_config.get("time_stop_bars", 180)),
            min_expected_edge_bps=float(strategy_config.get("min_expected_edge_bps", 1.2)),
            min_atr_percentile_30=float(strategy_config.get("min_atr_percentile_30", 0.18)),
            max_atr_percentile_30=float(strategy_config.get("max_atr_percentile_30", 0.82)),
            estimated_fee_bps=float(strategy_config.get("estimated_fee_bps", 1.25)),
            base_slippage_bps=float(strategy_config.get("base_slippage_bps", 0.9)),
            max_expected_slippage_bps=float(strategy_config.get("max_expected_slippage_bps", 4.5)),
            requested_risk_fraction=float(strategy_config.get("requested_risk_fraction", 0.0015)),
            max_requested_notional_fraction_cash=float(
                strategy_config.get("max_requested_notional_fraction_cash", 0.08)
            ),
        )
    return AnalystAgent(
        policy_name=policy.label,
        min_sample_count=int(strategy_config.get("min_sample_count", 30)),
        max_spread_bps=float(thresholds.get("max_spread_bps", 20.0)),
        min_regime_probability=float(thresholds.get("min_regime_probability", 0.58)),
        regime_trend_15_bps=float(thresholds.get("regime_trend_15_bps", 14.0)),
        regime_trend_30_bps=float(thresholds.get("regime_trend_30_bps", 28.0)),
        htf_trend_60_bps=float(thresholds.get("htf_trend_60_bps", 18.0)),
        htf_trend_240_bps=float(thresholds.get("htf_trend_240_bps", 42.0)),
        exit_momentum_3_bps=float(thresholds.get("exit_momentum_3_bps", -8.0)),
        exit_momentum_5_bps=float(thresholds.get("exit_momentum_5_bps", -12.0)),
        entry_momentum_3_bps=float(thresholds.get("entry_momentum_3_bps", 8.0)),
        entry_momentum_5_bps=float(thresholds.get("entry_momentum_5_bps", 12.0)),
        max_volatility_5_bps=float(strategy_config.get("max_volatility_5_bps", 24.0)),
        chaos_volatility_5_bps=float(strategy_config.get("chaos_volatility_5_bps", 40.0)),
        max_abs_zscore_30=float(thresholds.get("max_abs_zscore_30", 2.2)),
        min_trend_strength_bps=float(thresholds.get("min_trend_strength_bps", 16.0)),
        min_volume_ratio_5_30=float(thresholds.get("min_volume_ratio_5_30", 1.1)),
        min_entry_score=int(thresholds.get("min_entry_score", 5)),
        min_confirmation_count=int(thresholds.get("min_confirmation_count", 3)),
        breakout_buffer_bps=float(thresholds.get("breakout_buffer_bps", 0.75)),
        exit_regime_probability=float(thresholds.get("exit_regime_probability", 0.72)),
        hard_exit_momentum_3_bps=float(thresholds.get("hard_exit_momentum_3_bps", 8.0)),
        hard_exit_momentum_5_bps=float(thresholds.get("hard_exit_momentum_5_bps", 14.0)),
        min_stop_loss_bps=float(strategy_config.get("min_stop_loss_bps", 12.0)),
        max_stop_loss_bps=float(strategy_config.get("max_stop_loss_bps", 36.0)),
        stop_loss_vol_multiplier=float(strategy_config.get("stop_loss_vol_multiplier", 1.35)),
        trailing_stop_multiple=float(strategy_config.get("trailing_stop_multiple", 0.75)),
        partial_take_profit_fraction=float(strategy_config.get("partial_take_profit_fraction", 0.5)),
        take_profit_multiple=float(strategy_config.get("take_profit_multiple", 1.0)),
        max_reward_multiple=float(strategy_config.get("max_reward_multiple", 2.0)),
        time_stop_bars=int(strategy_config.get("time_stop_bars", 12)),
        min_expected_edge_bps=float(strategy_config.get("min_expected_edge_bps", 0.5)),
        allow_short_entries=bool(strategy_config.get("allow_short_entries", False)),
        min_atr_percentile_30=float(strategy_config.get("min_atr_percentile_30", 0.15)),
        max_atr_percentile_30=float(strategy_config.get("max_atr_percentile_30", 0.88)),
        estimated_fee_bps=float(strategy_config.get("estimated_fee_bps", 1.5)),
        base_slippage_bps=float(strategy_config.get("base_slippage_bps", 1.0)),
        max_expected_slippage_bps=float(strategy_config.get("max_expected_slippage_bps", 6.0)),
        requested_risk_fraction=float(strategy_config.get("requested_risk_fraction", 0.0025)),
        max_requested_notional_fraction_cash=float(
            strategy_config.get("max_requested_notional_fraction_cash", 0.12)
        ),
    )


def build_hmm_v3_policy(
    *,
    version: str,
    notes: str,
    thresholds_overrides: dict[str, object] | None = None,
    strategy_overrides: dict[str, object] | None = None,
) -> PolicyVersionRecord:
    thresholds = dict(HMM_V3_THRESHOLDS)
    strategy_config = dict(HMM_V3_STRATEGY_CONFIG)
    if thresholds_overrides:
        thresholds.update(thresholds_overrides)
    if strategy_overrides:
        strategy_config.update(strategy_overrides)
    return PolicyVersionRecord(
        id=f"hmm-v3-{version}",
        policy_name="baseline",
        version=version,
        status="candidate",
        thresholds=thresholds,
        risk_params={},
        strategy_config=strategy_config,
        notes=notes,
    )


def build_inverse_hmm_v3_policy(
    *,
    version: str,
    notes: str,
    thresholds_overrides: dict[str, object] | None = None,
    strategy_overrides: dict[str, object] | None = None,
) -> PolicyVersionRecord:
    thresholds = dict(HMM_V3_THRESHOLDS)
    strategy_config = dict(INVERSE_HMM_V3_STRATEGY_CONFIG)
    if thresholds_overrides:
        thresholds.update(thresholds_overrides)
    if strategy_overrides:
        strategy_config.update(strategy_overrides)
    return PolicyVersionRecord(
        id=f"inverse-hmm-v3-{version}",
        policy_name="inverse",
        version=version,
        status="candidate",
        thresholds=thresholds,
        risk_params={},
        strategy_config=strategy_config,
        notes=notes,
    )


def build_risk_policy(agent: AgentConfigRecord) -> RiskPolicy:
    risk_overrides = agent.risk_params
    max_position_notional = Decimal(
        str(risk_overrides.get("max_position_notional_usd", agent.max_position_notional_usd))
    )
    max_spread_bps = Decimal(str(risk_overrides.get("max_spread_bps", agent.max_spread_bps)))
    min_confidence = float(risk_overrides.get("min_decision_confidence", agent.min_decision_confidence))

    return RiskPolicy(
        min_confidence=min_confidence,
        max_risk_fraction=Decimal(str(risk_overrides.get("max_risk_per_trade_pct", agent.max_risk_per_trade_pct))),
        max_position_notional_usd=max_position_notional,
        max_spread_bps=max_spread_bps,
        max_trades_per_hour=int(risk_overrides.get("max_trades_per_hour", agent.max_trades_per_hour)),
        cooldown_seconds=int(
            risk_overrides.get("cooldown_seconds_after_trade", agent.cooldown_seconds_after_trade)
        ),
        min_expected_edge_bps=float(risk_overrides.get("min_expected_edge_bps", 0.0)),
    )
