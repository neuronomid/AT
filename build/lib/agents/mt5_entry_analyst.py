from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from data.schemas import EntryDecision


SYSTEM_PROMPT = """
You are the entry analyst for a demo-paper MT5 EURUSD trading system.

Rules:
- Output JSON only.
- Use exactly these keys: action, confidence, rationale, thesis_tags, requested_risk_fraction, context_signature
- Valid actions: enter_long, enter_short, hold
- Prefer hold when the setup is unclear, spread is poor, higher timeframes disagree, or feedback warns against the setup.
- Never emit prices, stop losses, take profits, or lot sizes.
- Requested risk fraction must stay between 0.0025 and 0.0075 when present.
- Thesis tags must be short and concrete.
""".strip()


@dataclass
class EntryAnalysisResult:
    decision: EntryDecision
    prompt: str
    raw_response: str
    latency_ms: int


class MT5EntryAnalystAgent:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        prompt_version: str = "v5.0",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._prompt_version = prompt_version

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def analyze(self, context_packet: dict[str, object]) -> EntryAnalysisResult:
        prompt = self.build_prompt(context_packet)
        started = perf_counter()
        raw_response = await self._call_model(prompt)
        latency_ms = int((perf_counter() - started) * 1000)
        decision = self._parse_decision(raw_response)
        return EntryAnalysisResult(
            decision=decision,
            prompt=prompt,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )

    def build_prompt(self, context_packet: dict[str, object]) -> str:
        return (
            "Return only JSON for the entry decision.\n"
            'Schema: {"action","confidence","rationale","thesis_tags","requested_risk_fraction","context_signature"}.\n'
            'Example hold: {"action":"hold","confidence":0.40,"rationale":"setup is mixed","thesis_tags":["mixed"],"requested_risk_fraction":null,"context_signature":"..." }.\n'
            'Example long: {"action":"enter_long","confidence":0.72,"rationale":"5m momentum aligns with 15m and 4h bias","thesis_tags":["trend","pullback"],"requested_risk_fraction":0.005,"context_signature":"..." }.\n'
            'Example short: {"action":"enter_short","confidence":0.72,"rationale":"5m breakdown aligns with 15m and 4h bias","thesis_tags":["breakdown"],"requested_risk_fraction":0.004,"context_signature":"..." }.\n'
            f"Context packet:\n{json.dumps(context_packet, default=str, separators=(',', ':'))}"
        )

    def fallback_decision(self, rationale: str) -> EntryDecision:
        return EntryDecision(action="hold", confidence=0.0, rationale=rationale, thesis_tags=[])

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
                        {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
                        {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
        output = self._extract_output_text(payload)
        if not output:
            raise RuntimeError("OpenAI response did not contain output text.")
        return output

    def _parse_decision(self, raw_response: str) -> EntryDecision:
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
            return EntryDecision.model_validate(payload)
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
