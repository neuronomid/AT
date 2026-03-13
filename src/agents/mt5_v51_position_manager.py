from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from data.mt5_v51_schemas import MT5V51ManagementDecision, MT5V51ManagementDecisionBatch
from infra.openrouter import OpenRouterChatClient


SYSTEM_PROMPT = """
You are the position manager for a demo-paper MT5 BTCUSD trading system.

Rules:
- Output JSON only.
- Use exactly this top-level shape: {"decisions":[...]}
- Each decision must use exactly these keys: ticket_id, action, confidence, rationale
- Valid actions: hold, close_ticket
- Only choose actions that appear in each ticket's allowed_actions list.
- Normal profit-taking is handled deterministically by code at 0.5R and 1.0R.
- Use close_ticket only for an early abort when the 20s, 1m, and 5m structure no longer support the trade.
- Never propose adding size, reversing, widening stops, or removing protection.
""".strip()


@dataclass
class MT5V51PositionManagementResult:
    decision_batch: MT5V51ManagementDecisionBatch
    prompt: str
    raw_response: str
    latency_ms: int


class MT5V51PositionManagerAgent:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        reasoning_enabled: bool = False,
        prompt_version: str = "v5.1",
    ) -> None:
        self._client = OpenRouterChatClient(api_key=api_key, base_url=base_url, app_name="AT V5.1 Manager")
        self._model = model
        self._reasoning_enabled = reasoning_enabled
        self._prompt_version = prompt_version

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def analyze(self, context_packet: dict[str, object]) -> MT5V51PositionManagementResult:
        prompt = self.build_prompt(context_packet)
        started = perf_counter()
        raw_response = await self._call_model(prompt)
        latency_ms = int((perf_counter() - started) * 1000)
        return MT5V51PositionManagementResult(
            decision_batch=self._parse_decision_batch(raw_response, context_packet=context_packet),
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
        return await self._client.complete_json(
            model=self._model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            reasoning_enabled=self._reasoning_enabled,
        )

    def _parse_decision_batch(
        self,
        raw_response: str,
        *,
        context_packet: dict[str, object],
    ) -> MT5V51ManagementDecisionBatch:
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
            return MT5V51ManagementDecisionBatch.model_validate(payload)
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
    ) -> MT5V51ManagementDecisionBatch:
        decisions: list[MT5V51ManagementDecision] = []
        for ticket in context_packet.get("tickets", []):
            if not isinstance(ticket, dict):
                continue
            ticket_id = ticket.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id:
                continue
            decisions.append(
                MT5V51ManagementDecision(ticket_id=ticket_id, action="hold", confidence=0.0, rationale=rationale)
            )
        return MT5V51ManagementDecisionBatch(decisions=decisions)
