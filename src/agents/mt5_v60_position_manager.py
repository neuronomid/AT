from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from time import perf_counter

from data.mt5_v60_schemas import MT5V60ManagementDecisionBatch
from infra.openai_responses import OpenAIResponsesClient


SYSTEM_PROMPT = """
You are the Manager agent for an MT5 BTCUSD@ paper-trading system.

Rules:
- Output JSON only.
- Use exactly this top-level shape: {"decisions":[...]}
- Each decision must use exactly these keys: ticket_id, confidence, rationale, commands, visual_context_update
- Commands may only use these actions: hold, modify_ticket, close_partial, close_ticket
- Only choose actions that appear in each ticket's allowed_actions list.
- Never add size, reverse, widen risk, or remove protection.
- A ticket may arrive with stop_loss or take_profit set to null because the entry was intentionally sent without broker-side TP/SL.
- initial_stop_loss and initial_take_profit are internal Analyzer anchors. Treat them as the widest allowed stop and the 1.0R take-profit cap.
- If a ticket is missing live stop_loss or take_profit, prioritize establishing sensible protection unless immediate exit is clearly better.
- When placing or moving a stop, keep it outside obvious noise and spread so it is not trivially clipped, but do not place it wider than the initial Analyzer risk anchor.
- Do not set a take profit farther than initial_take_profit unless you are closing exposure instead of extending it.
- If the current call includes a fresh screenshot, inspect it and return visual_context_update as an object, for example {"summary":"..."}.
- If the current call does not include a fresh screenshot, use cached_visual_context and set visual_context_update to null.
- Prefer hold when the trade still looks valid.
- close_partial must use a fraction between 0 and 1.
- modify_ticket may adjust stop_loss_price and/or take_profit_price.
""".strip()


@dataclass
class MT5V60PositionManagementResult:
    decision_batch: MT5V60ManagementDecisionBatch
    prompt: str
    raw_response: str
    latency_ms: int


class MT5V60PositionManagerAgent:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        reasoning_effort: str | None = None,
        prompt_version: str = "v6.0_multimodal_v1",
    ) -> None:
        self._client = OpenAIResponsesClient(api_key=api_key, base_url=base_url, app_name="AT V6.0 Manager")
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._prompt_version = prompt_version

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def analyze(
        self,
        context_packet: dict[str, object],
        *,
        image_path: str | None = None,
    ) -> MT5V60PositionManagementResult:
        prompt = self.build_prompt(context_packet)
        started = perf_counter()
        raw_response = await self._client.complete_json(
            model=self._model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            image_path=image_path,
            reasoning_effort=self._reasoning_effort,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        return MT5V60PositionManagementResult(
            decision_batch=self._parse_decision_batch(raw_response, context_packet=context_packet),
            prompt=prompt,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )

    def build_prompt(self, context_packet: dict[str, object]) -> str:
        return (
            "Return only JSON for the management decisions.\n"
            'Schema: {"decisions":[{"ticket_id":"...","confidence":0.55,"rationale":"...","commands":[{"action":"hold","stop_loss_price":null,"take_profit_price":null,"close_fraction":null}],"visual_context_update":{"summary":"optional"}|null}]}\n'
            "ticket.stop_loss and ticket.take_profit are live broker-side protection levels. ticket.initial_stop_loss and ticket.initial_take_profit are internal Analyzer anchors.\n"
            "If a ticket has stop_loss or take_profit set to null, the trade was entered naked on purpose and you are responsible for the first live protection placement.\n"
            "When you place the first stop, keep it outside normal spread/noise and nearby chart clutter. Do not tuck it so close that a normal wiggle is likely to stop the trade immediately.\n"
            "Do not widen beyond ticket.initial_stop_loss. Do not set take profit farther than ticket.initial_take_profit.\n"
            'If manager_context.image_attached is true, inspect the screenshot and return visual_context_update as an object like {"summary":"..."}.\n'
            "If manager_context.image_attached is false, use manager_context.screenshot.cached_visual_context and set visual_context_update to null.\n"
            f"Context packet:\n{json.dumps(context_packet, default=str, separators=(',', ':'))}"
        )

    def _parse_decision_batch(
        self,
        raw_response: str,
        *,
        context_packet: dict[str, object],
    ) -> MT5V60ManagementDecisionBatch:
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
            return MT5V60ManagementDecisionBatch.model_validate(payload)
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
            commands = item.get("commands")
            if isinstance(commands, dict):
                commands = [commands]
            if not isinstance(commands, list):
                action = item.get("action", "hold")
                commands = [{"action": action}]
            normalized_commands: list[dict[str, Any]] = []
            for command in commands:
                if not isinstance(command, dict):
                    continue
                action = command.get("action", command.get("decision"))
                if isinstance(action, str):
                    mapped = action.strip().lower()
                    aliases = {
                        "hold": "hold",
                        "wait": "hold",
                        "close": "close_ticket",
                        "exit": "close_ticket",
                        "partial": "close_partial",
                        "take_partial": "close_partial",
                        "modify": "modify_ticket",
                        "adjust": "modify_ticket",
                    }
                    command["action"] = aliases.get(mapped, mapped)
                if "stop_loss_price" not in command and command.get("stop_loss") is not None:
                    command["stop_loss_price"] = command.get("stop_loss")
                if "take_profit_price" not in command and command.get("take_profit") is not None:
                    command["take_profit_price"] = command.get("take_profit")
                if "close_fraction" not in command and command.get("fraction") is not None:
                    command["close_fraction"] = command.get("fraction")
                normalized_commands.append(command)
            if "rationale" not in item:
                rationale = item.get("reason")
                if isinstance(rationale, str) and rationale.strip():
                    item["rationale"] = rationale.strip()
            visual_context_update = item.get("visual_context_update")
            if isinstance(visual_context_update, str):
                summary = visual_context_update.strip()
                item["visual_context_update"] = {"summary": summary} if summary else None
            item["commands"] = normalized_commands
            normalized_decisions.append(item)
        normalized["decisions"] = normalized_decisions
        return normalized

    def _fallback_decision_batch(
        self,
        *,
        context_packet: dict[str, object],
        rationale: str,
    ) -> MT5V60ManagementDecisionBatch:
        decisions = []
        for ticket in context_packet.get("tickets", []):
            if not isinstance(ticket, dict):
                continue
            ticket_id = ticket.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id:
                continue
            decisions.append(
                {
                    "ticket_id": ticket_id,
                    "confidence": 0.0,
                    "rationale": rationale,
                    "commands": [{"action": "hold"}],
                    "visual_context_update": None,
                }
            )
        return MT5V60ManagementDecisionBatch.model_validate({"decisions": decisions})
