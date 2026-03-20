from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from time import perf_counter

from data.mt5_v60_schemas import MT5V60ManagementDecisionBatch
from infra.openai_responses import OpenAIResponsesClient


SYSTEM_PROMPT = """
You are the Manager agent for an MT5 paper-trading system.

Rules:
- Output JSON only.
- Use exactly this top-level shape: {"decisions":[...]}
- Each decision must use exactly these keys: ticket_id, confidence, rationale, commands, visual_context_update
- Commands may only use these actions: hold, modify_ticket, close_partial, close_ticket
- Only choose actions that appear in each ticket's allowed_actions list.
- The runtime symbol is supplied in the context packet. Manage only tickets for that symbol.
- Never add size, reverse, widen risk, or remove protection.
- A ticket may arrive with stop_loss or take_profit set to null because the entry was intentionally sent without broker-side TP/SL.
- initial_stop_loss and initial_take_profit are internal Analyzer anchors. Treat them as the widest allowed stop and the 1.0R take-profit cap.
- If ticket.first_protection_review_pending is true, the current live stop/take-profit was auto-attached right after a naked fill. Review it on this pass. Keep it if it still sits in a sensible place, or move it if structure/spread says it is misplaced.
- If a ticket is missing live stop_loss or take_profit, prioritize establishing sensible protection unless immediate exit is clearly better.
- When placing or moving a stop, keep it outside obvious noise and spread so it is not trivially clipped, but do not place it wider than the initial Analyzer risk anchor.
- Do not set a take profit farther than initial_take_profit unless you are closing exposure instead of extending it.
- Decide deliberately when breakeven is justified. Do not force breakeven only because the trade is slightly green.
- This is an active scalp manager, not a passive observer. Do not leave stop and take-profit unchanged for the full life of a fast trade unless the structure clearly still justifies it.
- Trail the stop only in the profitable direction. Use favorable excursion, recent candles, and obvious structure so the stop tightens without choking the trade too early.
- Use partial closes when the trade has extended, hits a barrier, or gives back too much from the best excursion. Prefer meaningful de-risking over random slicing.
- When 1m, 2m, and 3m start reversing against the trade or the trade gives back a meaningful part of its best excursion, protect profit or cut risk instead of just holding.
- If the trade is valid but not strong enough to hold unchanged, choose modify_ticket or close_partial. Do not default to hold out of caution.
- If the current call includes a fresh screenshot, inspect it and return visual_context_update as an object, for example {"summary":"..."}.
- If the current call does not include a fresh screenshot, use cached_visual_context and set visual_context_update to null.
- Prefer hold when the trade still looks valid.
- close_partial must use a fraction between 0 and 1.
- modify_ticket may adjust stop_loss_price and/or take_profit_price.
- Use hold only when you want no broker-side change. If you want to set or move stop_loss_price or take_profit_price, action must be modify_ticket.
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
        prompt_version: str = "v6.0_multimodal_v2",
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
            "Use hold only when no broker-side change is requested. If you are setting or changing stop_loss_price or take_profit_price, use modify_ticket.\n"
            "ticket.stop_loss and ticket.take_profit are live broker-side protection levels. ticket.initial_stop_loss and ticket.initial_take_profit are internal Analyzer anchors.\n"
            "If a ticket has stop_loss or take_profit set to null, the trade was entered naked on purpose and you are responsible for the first live protection placement.\n"
            "If ticket.first_protection_review_pending is true, the current live protection was auto-attached immediately after a naked fill. It is safety-first protection, not automatically the final best placement. Review it now: keep it if it still makes sense, or move it if the chart structure says it should be improved.\n"
            "When you place the first stop, keep it outside normal spread/noise and nearby chart clutter. Do not tuck it so close that a normal wiggle is likely to stop the trade immediately.\n"
            "Do not widen beyond ticket.initial_stop_loss. Do not set take profit farther than ticket.initial_take_profit.\n"
            "Decide when breakeven is justified from the trade structure, favorable excursion, and recent pullback behavior. Use max_favorable_r, drawdown_from_peak_r, stop_at_or_better_than_breakeven, and volume_remaining_fraction when useful.\n"
            "You are also responsible for active trailing and partial management. Trail only in the profitable direction and use partials deliberately when extension starts fading or a barrier is near.\n"
            "If 1m, 2m, and 3m begin to reverse against the open trade or the trade gives back too much from max_favorable_r, protect profit or reduce exposure instead of sitting still.\n"
            "If the trade is still valid but not strong enough to leave unchanged, use modify_ticket or close_partial rather than defaulting to hold.\n"
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
            payload = self._normalize_payload(payload, context_packet=context_packet)
        try:
            return MT5V60ManagementDecisionBatch.model_validate(payload)
        except Exception:
            return self._fallback_decision_batch(
                context_packet=context_packet,
                rationale="Model returned a JSON payload that failed validation.",
            )

    def _normalize_payload(
        self,
        payload: dict[str, Any],
        *,
        context_packet: dict[str, object],
    ) -> dict[str, Any]:
        normalized = dict(payload)
        ticket_state_by_id = self._ticket_state_by_id(context_packet)
        decisions = normalized.get("decisions")
        if isinstance(decisions, dict):
            decisions = [decisions]
        if not isinstance(decisions, list):
            decisions = []
        normalized_decisions: list[dict[str, Any]] = []
        for item in decisions:
            if not isinstance(item, dict):
                continue
            ticket_id = item.get("ticket_id")
            ticket_state = ticket_state_by_id.get(ticket_id) if isinstance(ticket_id, str) else None
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
                if command.get("action") == "hold" and self._command_changes_protection(command, ticket_state=ticket_state):
                    command["action"] = "modify_ticket"
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

    def _ticket_state_by_id(self, context_packet: dict[str, object]) -> dict[str, dict[str, Decimal | None]]:
        ticket_state_by_id: dict[str, dict[str, Decimal | None]] = {}
        tickets = context_packet.get("tickets", [])
        if not isinstance(tickets, list):
            return ticket_state_by_id
        for ticket in tickets:
            if not isinstance(ticket, dict):
                continue
            ticket_id = ticket.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id:
                continue
            ticket_state_by_id[ticket_id] = {
                "stop_loss": self._coerce_price(ticket.get("stop_loss")),
                "take_profit": self._coerce_price(ticket.get("take_profit")),
            }
        return ticket_state_by_id

    def _command_changes_protection(
        self,
        command: dict[str, Any],
        *,
        ticket_state: dict[str, Decimal | None] | None,
    ) -> bool:
        requested_stop = self._coerce_price(command.get("stop_loss_price"))
        requested_take_profit = self._coerce_price(command.get("take_profit_price"))
        if ticket_state is None:
            return requested_stop is not None or requested_take_profit is not None
        return (
            (requested_stop is not None and requested_stop != ticket_state.get("stop_loss"))
            or (requested_take_profit is not None and requested_take_profit != ticket_state.get("take_profit"))
        )

    def _coerce_price(self, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

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
