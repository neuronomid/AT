from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from data.mt5_v51_schemas import MT5V51EntryDecision
from infra.openrouter import OpenRouterChatClient


SYSTEM_PROMPT = """
You are the entry analyst for a demo-paper MT5 BTCUSD scalping system.

Rules:
- Output JSON only.
- Use exactly these keys: action, confidence, rationale, thesis_tags, requested_risk_fraction, context_signature
- Valid actions: enter_long, enter_short, hold
- This is a fast 1-minute scalp system that wants only obvious directional bursts, not chop.
- Read trend_regime first, recent_bars.1m second, timeframes.1m third.
- Use recent_bars.20s only for timing and micro confirmation.
- Use timeframes.5m and levels.5m only as background context, not as a hard gate.
- Be willing to trade clean 1m momentum or shallow-pullback continuation when the move still has follow-through.
- If trend_regime.tradeable is false or trend_regime.market_state is choppy, prefer hold unless the packet shows a truly exceptional fresh burst.
- If trend_regime.tradeable is true and trend_regime.primary_direction matches the setup, respond decisively.
- Treat timeframes.1m.long_trigger_ready or short_trigger_ready as serious scalp opportunities unless freshness or microstructure is poor.
- Treat timeframes.1m.long_continuation_ready or short_continuation_ready as serious opportunities for orderly stair-step continuation, even when the latest candle is not a huge ATR-expansion bar.
- Treat timeframes.1m.long_pause_after_impulse_ready or short_pause_after_impulse_ready as serious opportunities. One tiny pause or counter candle after a strong directional burst does not cancel the scalp.
- A clean sequence of 3 to 6 same-direction 1m candles with positive EMA separation is actionable momentum, not a reason to wait for a pullback.
- Do not let one tiny opposite or flat 1m candle invalidate a clean directional burst when the 1m EMA gap and 3-bar versus 5-bar returns still agree with the move.
- The execution model exits the full position at the first setup-quality target: strong = 0.70R, normal = 0.50R, weak = 0.30R. Because there is no runner, prefer the earliest clean continuation entry instead of waiting for extra confirmation.
- Mild 20s disagreement should not veto a clean 1m continuation. Only aggressive opposite 20s structure should veto it.
- Treat freshness.source_snapshot_age_bucket = aging as acceptable for a scalp when the move is clean. Only stale_soon or stale should materially veto an otherwise valid setup.
- Prefer hold when freshness is not fresh, spread cost is expensive, trend_regime is mixed or choppy, the 1m impulse is already stalling, or the 20s tape is clearly and aggressively opposite.
- Use this risk matrix when entering:
  - strongest clear setups: request 0.004
  - normal setups: request 0.002 to 0.003
  - weak but acceptable setups: request 0.001 to 0.002
  - clearly choppy setups: hold
- Strongest clear setup means trend_regime.tradeable = true, trend_quality_score >= 11, alignment_score >= 3, chop_score <= 1, and the direction is cleanly supported.
- Normal setup means trend_regime.tradeable = true, trend_quality_score >= 8, alignment_score >= 2, chop_score <= 2, and the direction is still supported.
- Weak but acceptable setup means the regime is still tradeable but less clean, often with chop_score = 3 or weaker alignment; size down instead of forcing full size.
- If trend_regime.market_state is choppy or chop_score >= 4, prefer hold.
- Treat feedback avoid_tags and reinforce_tags as weak hints only. Never let feedback alone veto a clean 1m momentum scalp.
- Never emit prices, stop losses, take profits, lot sizes, or broker commands.
- Requested risk fraction must stay between 0.001 and 0.004 when present.
- Thesis tags must be short and concrete.
- Respond decisively when the 1m tape is clean.
""".strip()


@dataclass
class MT5V51EntryAnalysisResult:
    decision: MT5V51EntryDecision
    prompt: str
    raw_response: str
    latency_ms: int


class MT5V51EntryAnalystAgent:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        reasoning_enabled: bool = True,
        prompt_version: str = "v5.1_fast_trend_v3",
    ) -> None:
        self._client = OpenRouterChatClient(api_key=api_key, base_url=base_url, app_name="AT V5.1 Entry")
        self._model = model
        self._reasoning_enabled = reasoning_enabled
        self._prompt_version = prompt_version

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def analyze(self, context_packet: dict[str, object]) -> MT5V51EntryAnalysisResult:
        prompt = self.build_prompt(context_packet)
        started = perf_counter()
        raw_response = await self._call_model(prompt)
        latency_ms = int((perf_counter() - started) * 1000)
        return MT5V51EntryAnalysisResult(
            decision=self._parse_decision(raw_response),
            prompt=prompt,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )

    def build_prompt(self, context_packet: dict[str, object]) -> str:
        return (
            "Return only JSON for the entry decision.\n"
            'Schema: {"action","confidence","rationale","thesis_tags","requested_risk_fraction","context_signature"}.\n'
            'Read trend_regime first, recent_bars.1m as the primary tape, recent_bars.20s as timing, and timeframes.5m plus levels as backdrop.\n'
            'This strategy wants only strong clean trend legs. If trend_regime.tradeable is false or trend_regime.market_state is choppy, default to hold.\n'
            'Use microstructure for spread cost and short bid/ask drift. Use freshness.source_snapshot_age_bucket to avoid stale reads, but treat aging as acceptable when the move is otherwise clean.\n'
            'The position exits fully at the first setup-quality target: strong = 0.70R, normal = 0.50R, weak = 0.30R. Because there is no runner, prefer the earliest clean continuation or pause-after-impulse entry instead of waiting for more candles.\n'
            'If timeframes.1m.long_trigger_ready, short_trigger_ready, long_continuation_ready, short_continuation_ready, long_pause_after_impulse_ready, or short_pause_after_impulse_ready is true, treat it as a serious scalp opportunity unless freshness or spread cost is poor.\n'
            'One tiny opposite or flat 1m candle after a strong directional burst does not cancel the continuation by itself when EMA gap and 3-bar versus 5-bar returns still agree.\n'
            'Do not wait for a giant breakout candle if recent_bars.1m already show a clean 3 to 6 candle stair-step continuation with positive EMA gap.\n'
            'Risk matrix: strongest clear setups request 0.004; normal setups request 0.002 to 0.003; weak but acceptable setups request 0.001 to 0.002; clearly choppy setups should hold.\n'
            'Treat trend_quality_score >= 11 with alignment_score >= 3 and chop_score <= 1 as strongest-clear territory. Treat trend_quality_score >= 8 with alignment_score >= 2 and chop_score <= 2 as normal. Treat tradeable but less clean regimes, especially chop_score = 3, as weak-only.\n'
            'Example hold: {"action":"hold","confidence":0.31,"rationale":"trend_regime is choppy, 1m is stalling, and the 20s tape is not supporting a fresh entry","thesis_tags":["chop","stall"],"requested_risk_fraction":null,"context_signature":"..."}\n'
            'Example long: {"action":"enter_long","confidence":0.76,"rationale":"trend_regime is a tradeable bullish continuation and recent_bars.1m show a clean bullish stair-step with positive EMA gap while the 20s tape is not aggressively opposing the move","thesis_tags":["momentum","continuation"],"requested_risk_fraction":0.004,"context_signature":"..."}\n'
            'Example short: {"action":"enter_short","confidence":0.74,"rationale":"trend_regime is a tradeable bearish burst, recent_bars.1m show orderly downside follow-through, and the 20s tape is confirming or neutral","thesis_tags":["momentum","breakdown"],"requested_risk_fraction":0.0025,"context_signature":"..."}\n'
            f"Context packet:\n{json.dumps(context_packet, default=str, separators=(',', ':'))}"
        )

    def fallback_decision(self, rationale: str) -> MT5V51EntryDecision:
        return MT5V51EntryDecision(action="hold", confidence=0.0, rationale=rationale, thesis_tags=[])

    async def _call_model(self, prompt: str) -> str:
        return await self._client.complete_json(
            model=self._model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            reasoning_enabled=self._reasoning_enabled,
        )

    def _parse_decision(self, raw_response: str) -> MT5V51EntryDecision:
        candidate = raw_response.strip()
        if candidate.startswith("```"):
            candidate = "\n".join(line for line in candidate.splitlines() if not line.startswith("```")).strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return self.fallback_decision("Model returned invalid JSON.")
        if isinstance(payload, dict):
            payload = self._normalize_payload(payload)
        try:
            return MT5V51EntryDecision.model_validate(payload)
        except Exception:
            return self.fallback_decision("Model returned a JSON payload that failed validation.")

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        action = normalized.get("action", normalized.get("decision"))
        if isinstance(action, str):
            mapped = action.strip().lower()
            aliases = {
                "buy": "enter_long",
                "long": "enter_long",
                "enter_buy": "enter_long",
                "sell": "enter_short",
                "short": "enter_short",
                "enter_sell": "enter_short",
                "hold": "hold",
                "wait": "hold",
                "no_trade": "hold",
                "do_nothing": "hold",
            }
            normalized["action"] = aliases.get(mapped, mapped)
        thesis_tags = normalized.get("thesis_tags")
        if isinstance(thesis_tags, str):
            normalized["thesis_tags"] = [thesis_tags]
        if "rationale" not in normalized:
            rationale = normalized.get("reason")
            if isinstance(rationale, str) and rationale.strip():
                normalized["rationale"] = rationale.strip()
        requested_risk_fraction = normalized.get("requested_risk_fraction", normalized.get("risk_fraction"))
        if isinstance(requested_risk_fraction, (int, float)):
            normalized["requested_risk_fraction"] = requested_risk_fraction
        return normalized
