from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from data.schemas import LLMRuntimeDecision


SYSTEM_PROMPT = """
You are the live analyst for an ETH/USD paper-trading system.

Rules:
- Output JSON only. No markdown, no prose outside JSON.
- You are long/flat only. Never suggest shorting.
- If `open_trade` is null, never use `reduce` or `exit`.
- Prefer do_nothing when the setup is unclear or the market quality is poor, but do not default to do_nothing just because one field is missing.
- When flat, buy if the packet shows a credible long edge: trend is bullish or improving, momentum/breakout pressure is present, and warnings do not dominate the setup.
- Use `decision_support.long_setup_score`, `decision_support.long_setup_flags`, and `decision_support.warning_flags` as the primary decision checklist.
- When already in a trade, use `reduce` or `exit` only if the thesis is weakening, momentum is breaking down, or the trade has become overstretched.
- Respect the provided hard constraints and lesson packet.
- For buy decisions, choose a risk fraction between 0.0025 and 0.015 and a take_profit_r between 0.5 and 2.0.
- For reduce decisions, choose a reduce_fraction of 0.25, 0.5, or 1.0.
- Keep thesis_tags short and concrete.
- Use these exact keys only:
  action, confidence, rationale, risk_fraction_equity, take_profit_r, reduce_fraction, thesis_tags
- Do not use alias keys like decision, reason, thesis, risk_fraction, order, symbol, or price.
""".strip()


@dataclass
class LLMAnalysisResult:
    decision: LLMRuntimeDecision
    prompt: str
    raw_response: str
    latency_ms: int


class LLMLiveAnalystAgent:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        prompt_version: str = "v4.0",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._prompt_version = prompt_version

    @property
    def model(self) -> str:
        return self._model

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def analyze(self, context_packet: dict[str, object]) -> LLMAnalysisResult:
        prompt = self.build_prompt(context_packet)
        started = perf_counter()
        raw_response = await self._call_model(prompt)
        latency_ms = int((perf_counter() - started) * 1000)
        decision = self._parse_runtime_decision(raw_response)
        return LLMAnalysisResult(
            decision=decision,
            prompt=prompt,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )

    def fallback_decision(self, rationale: str) -> LLMRuntimeDecision:
        return LLMRuntimeDecision(
            action="do_nothing",
            confidence=0.0,
            rationale=rationale,
            thesis_tags=[],
        )

    def build_prompt(self, context_packet: dict[str, object]) -> str:
        return (
            "Return only JSON matching this runtime decision schema.\n"
            'Use exactly these keys: {"action","confidence","rationale","risk_fraction_equity","take_profit_r","reduce_fraction","thesis_tags"}.\n'
            'For do_nothing: {"action":"do_nothing","confidence":0.35,"rationale":"...",'
            '"risk_fraction_equity":null,"take_profit_r":null,"reduce_fraction":null,"thesis_tags":["..."]}.\n'
            'For buy: {"action":"buy","confidence":0.60,"rationale":"...",'
            '"risk_fraction_equity":0.005,"take_profit_r":1.0,"reduce_fraction":null,"thesis_tags":["..."]}.\n'
            'For exit: {"action":"exit","confidence":0.75,"rationale":"...",'
            '"risk_fraction_equity":null,"take_profit_r":null,"reduce_fraction":1.0,"thesis_tags":["..."]}.\n'
            'Do not emit any extra keys.\n\n'
            f"Context packet:\n{json.dumps(context_packet, default=str, separators=(',', ':'))}"
        )

    async def _call_model(self, prompt: str) -> str:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        ) as client:
            response = await client.post(
                "/responses",
                json={
                    "model": self._model,
                    "input": [
                        {
                            "role": "system",
                            "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
                        },
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": prompt}],
                        },
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
        output = self._extract_output_text(payload)
        if not output:
            raise RuntimeError("OpenAI response did not contain output text.")
        return output

    def _parse_runtime_decision(self, raw_response: str) -> LLMRuntimeDecision:
        candidate = raw_response.strip()
        if candidate.startswith("```"):
            lines = [line for line in candidate.splitlines() if not line.startswith("```")]
            candidate = "\n".join(lines).strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return self.fallback_decision("Model returned invalid JSON.")
        if isinstance(payload, dict):
            payload = self._normalize_payload(payload)
        try:
            decision = LLMRuntimeDecision.model_validate(payload)
        except Exception:
            return self.fallback_decision("Model returned a JSON payload that failed validation.")
        return decision

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)

        action = normalized.get("action", normalized.get("decision"))
        if isinstance(action, str):
            normalized_action = action.strip().lower()
            if normalized_action in {"hold", "wait", "none"}:
                normalized_action = "do_nothing"
            normalized["action"] = normalized_action

        if "rationale" not in normalized:
            rationale = normalized.get("reason", normalized.get("thesis"))
            if isinstance(rationale, str) and rationale.strip():
                normalized["rationale"] = rationale.strip()

        if "risk_fraction_equity" not in normalized:
            risk_fraction = normalized.get("risk_fraction")
            if isinstance(risk_fraction, (int, float)):
                normalized["risk_fraction_equity"] = risk_fraction

        if "take_profit_r" not in normalized:
            take_profit_r = normalized.get("tp_r")
            if isinstance(take_profit_r, (int, float)):
                normalized["take_profit_r"] = take_profit_r

        if "reduce_fraction" not in normalized:
            reduce_fraction = normalized.get("qty_fraction")
            if isinstance(reduce_fraction, (int, float)):
                normalized["reduce_fraction"] = reduce_fraction

        thesis_tags = normalized.get("thesis_tags")
        if isinstance(thesis_tags, str):
            normalized["thesis_tags"] = [thesis_tags]

        return normalized

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        parts: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                elif isinstance(text, dict):
                    value = text.get("value")
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
        return "\n\n".join(parts)
