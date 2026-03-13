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
- This strategy is a 1-minute scalper. The 1m chart is the primary decision timeframe.
- The 20s chart is for timing and micro confirmation. The 5m chart is only a background hint, not a hard gate.
- Be willing to trade strong 1m momentum. One, two, or three back-to-back strong same-direction 1m candles are enough to justify a scalp when the move still has follow-through.
- Prefer continuation or shallow-pullback entries when 1m is moving cleanly and 20s is supportive or at least not aggressively opposite.
- Do not reject a trade only because momentum is strong, price is extended, or the 5m hint lags the 1m move.
- Prefer hold when the setup is choppy, spread is poor, the 1m impulse is already stalling, or the 20s tape is clearly and aggressively opposite the 1m setup.
- Treat recent feedback as a weak hint only. Never let feedback alone veto a clean 1m momentum scalp.
- Never emit prices, stop losses, take profits, lot sizes, or broker commands.
- Requested risk fraction must stay between 0.002 and 0.005 when present.
- Thesis tags must be short and concrete.
- Be responsive and willing to trade when the 1m tape shows obvious momentum.
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
        prompt_version: str = "v5.1",
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
            'Treat 1m as primary, 20s as timing, and 5m as a hint.\n'
            'If the 1m packet shows long_trigger_ready or short_trigger_ready, treat that as a serious scalp opportunity.\n'
            'Example hold: {"action":"hold","confidence":0.30,"rationale":"1m is choppy and the 20s tape is not supporting either side","thesis_tags":["chop"],"requested_risk_fraction":null,"context_signature":"..."}\n'
            'Example long: {"action":"enter_long","confidence":0.72,"rationale":"1m shows back-to-back bullish impulse candles and the 20s tape is not opposing the move, so the scalp long is still actionable even though 5m is only a mild hint","thesis_tags":["impulse","continuation"],"requested_risk_fraction":0.004,"context_signature":"..."}\n'
            'Example short: {"action":"enter_short","confidence":0.70,"rationale":"1m shows heavy bearish candles with short_trigger_ready and the 20s tape is pressing lower, so the short scalp is actionable without waiting for perfect 5m agreement","thesis_tags":["impulse","breakdown"],"requested_risk_fraction":0.003,"context_signature":"..."}\n'
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
