from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from data.schemas import ManagementDecision, ManagementDecisionBatch


SYSTEM_PROMPT = """
You are the position manager for a demo-paper MT5 EURUSD trading system.

Rules:
- Output JSON only.
- Use exactly this top-level shape: {"decisions":[...]}
- Each decision must use exactly these keys: ticket_id, action, confidence, rationale
- Valid actions: hold, take_partial_50, move_stop_to_breakeven, trail_stop_to_rule, close_ticket
- Only choose actions that appear in each ticket's allowed_actions list.
- Prefer hold when the setup remains valid or when the packet is mixed.
- Never propose adding size, reversing, widening stops, or removing protection.
""".strip()


@dataclass
class PositionManagementResult:
    decision_batch: ManagementDecisionBatch
    prompt: str
    raw_response: str
    latency_ms: int


class MT5PositionManagerAgent:
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

    async def analyze(self, context_packet: dict[str, object]) -> PositionManagementResult:
        prompt = self.build_prompt(context_packet)
        started = perf_counter()
        raw_response = await self._call_model(prompt)
        latency_ms = int((perf_counter() - started) * 1000)
        decision_batch = self._parse_decision_batch(raw_response, context_packet=context_packet)
        return PositionManagementResult(
            decision_batch=decision_batch,
            prompt=prompt,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )

    def build_prompt(self, context_packet: dict[str, object]) -> str:
        return (
            "Return only JSON for the management decisions.\n"
            'Schema: {"decisions":[{"ticket_id":"...","action":"hold","confidence":0.55,"rationale":"..."}]}.\n'
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

    def _parse_decision_batch(
        self,
        raw_response: str,
        *,
        context_packet: dict[str, object],
    ) -> ManagementDecisionBatch:
        candidate = raw_response.strip()
        if candidate.startswith("```"):
            candidate = "\n".join(line for line in candidate.splitlines() if not line.startswith("```")).strip()
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return self._fallback_decision_batch(context_packet=context_packet, rationale="Model returned invalid JSON.")
        if isinstance(payload, dict):
            payload = self._normalize_payload(payload)
        try:
            return ManagementDecisionBatch.model_validate(payload)
        except Exception:
            return self._fallback_decision_batch(
                context_packet=context_packet,
                rationale="Model returned a JSON payload that failed validation.",
            )

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        decisions = normalized.get("decisions")
        if isinstance(decisions, dict):
            decisions = [decisions]
        if not isinstance(decisions, list):
            decisions = []
        normalized_decisions: list[dict[str, Any]] = []
        for item in decisions:
            if not isinstance(item, dict):
                continue
            action = item.get("action", item.get("decision"))
            if isinstance(action, str):
                mapped = action.strip().lower()
                aliases = {
                    "hold": "hold",
                    "wait": "hold",
                    "partial": "take_partial_50",
                    "take_partial": "take_partial_50",
                    "breakeven": "move_stop_to_breakeven",
                    "move_stop": "move_stop_to_breakeven",
                    "trail": "trail_stop_to_rule",
                    "close": "close_ticket",
                    "exit": "close_ticket",
                }
                item["action"] = aliases.get(mapped, mapped)
            if "rationale" not in item:
                rationale = item.get("reason")
                if isinstance(rationale, str) and rationale.strip():
                    item["rationale"] = rationale.strip()
            normalized_decisions.append(item)
        normalized["decisions"] = normalized_decisions
        return normalized

    def _fallback_decision_batch(
        self,
        *,
        context_packet: dict[str, object],
        rationale: str,
    ) -> ManagementDecisionBatch:
        decisions: list[ManagementDecision] = []
        for ticket in context_packet.get("tickets", []):
            if not isinstance(ticket, dict):
                continue
            ticket_id = ticket.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id:
                continue
            decisions.append(
                ManagementDecision(ticket_id=ticket_id, action="hold", confidence=0.0, rationale=rationale)
            )
        return ManagementDecisionBatch(decisions=decisions)

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
