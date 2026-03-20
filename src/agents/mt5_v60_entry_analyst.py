from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from data.mt5_v60_schemas import MT5V60EntryDecision
from infra.openai_responses import OpenAIResponsesClient


SYSTEM_PROMPT = """
You are the Analyzer agent for an MT5 paper-trading scalping system on the 3-minute chart.

Rules:
- Output JSON only.
- Use exactly these keys: action, confidence, rationale, thesis_tags, requested_risk_fraction, stop_loss_price, take_profit_price, context_signature
- Valid actions: enter_long, enter_short, hold
- The runtime symbol is supplied in the context packet. Treat that symbol as the only tradable instrument for this session.
- Use both the numeric snapshot and the screenshot when a screenshot is attached.
- The 3m timeframe is the execution timeframe. 1m and 2m are supporting detail for breakout confirmation and timing. 5m is higher-timeframe backdrop only.
- The primary job is to find tradeable trend, trend strength, and consolidation/range conditions to avoid.
- When 3m, 2m, and 1m are aligned on a clear breakout or continuation, that lower-timeframe alignment is the deciding factor for entry timing.
- 5m must not veto a clear 3m/2m/1m breakout by itself. Use 5m only to describe whether the broader backdrop is supportive, neutral, or early/opposed.
- If the setup is valid but not the strongest, reduce requested risk instead of defaulting to hold.
- Prefer hold when the market is genuinely choppy, compressed, indecisive, or visually trapped in a tight range across the 3m/2m/1m structure.
- Do not miss obvious fast trend continuations just because 5m has not fully expanded yet.
- When entering, choose a safe but not excessively wide stop loss and a realistic take profit.
- Stop loss and take profit must be actual prices, not percentages.
- stop_loss_price and take_profit_price are internal planning anchors for sizing and for the Manager's first protection pass. The live entry will be sent without broker-side TP/SL.
- Requested risk fraction must not exceed 0.005. It is a fraction of current total balance, so 0.005 means 0.5% of balance.
- Choose requested risk fraction from your interpretation of price action and trend strength. Weaker or messier structure should use less risk.
- Do not emit lot size or broker commands.
- For stop-loss reversal checks, only the opposite side of the stopped trade is allowed.
- Consider spread in your stop placement logic.
- If the screenshot is missing or stale, lower conviction, but still allow a clear numeric 3m/2m/1m breakout to trade with reduced risk when the packet is unusually clear.
""".strip()


@dataclass
class MT5V60EntryAnalysisResult:
    decision: MT5V60EntryDecision
    prompt: str
    raw_response: str
    latency_ms: int


class MT5V60EntryAnalystAgent:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        reasoning_effort: str = "high",
        prompt_version: str = "v6.0_multimodal_v3",
    ) -> None:
        self._client = OpenAIResponsesClient(api_key=api_key, base_url=base_url, app_name="AT V6.0 Analyzer")
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
    ) -> MT5V60EntryAnalysisResult:
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
        return MT5V60EntryAnalysisResult(
            decision=self._parse_decision(raw_response),
            prompt=prompt,
            raw_response=raw_response,
            latency_ms=latency_ms,
        )

    def build_prompt(self, context_packet: dict[str, object]) -> str:
        screenshot = context_packet.get("screenshot", {})
        return (
            "Return only JSON for the entry decision.\n"
            'Schema: {"action","confidence","rationale","thesis_tags","requested_risk_fraction","stop_loss_price","take_profit_price","context_signature"}.\n'
            "Read entry_signals first, then recent_bars.3m, then recent_bars.2m and recent_bars.1m.\n"
            "Use recent_bars.3m as the main execution structure. Use 1m and 2m for breakout and continuation timing. Use 5m only as backdrop.\n"
            "Use both numeric stats and the screenshot. The screenshot is for major trend, trend quality, nearby barriers, and safer stop/target placement.\n"
            "A clear aligned breakout on 3m, 2m, and 1m should be traded even if 5m is only neutral or still early. Do not let 5m become a hard veto.\n"
            "If the setup is valid but less clean, scale requested_risk_fraction down instead of defaulting to hold.\n"
            "Prefer hold in genuine compression, range, or mixed structure, not in obvious aligned trend continuation.\n"
            "If entering, requested_risk_fraction must be <= 0.005, where 0.005 means 0.5% of current total balance.\n"
            "Choose requested_risk_fraction from your reading of trend strength and price action quality. Cleaner and stronger movement may justify more risk; weak or messy structure should use less.\n"
            "Both stop_loss_price and take_profit_price must be present as internal planning anchors. The system will enter without broker-side TP/SL, then the Manager will decide live placement.\n"
            "Take profit distance must stay realistic and within 1.0R of the initial stop distance.\n"
            "Example hold: "
            '{"action":"hold","confidence":0.31,"rationale":"the screenshot shows choppy sideways range conditions, so the numeric stats are not enough to justify an entry","thesis_tags":["range","chop"],"requested_risk_fraction":null,"stop_loss_price":null,"take_profit_price":null,"context_signature":"..."}\n'
            "Example long: "
            '{"action":"enter_long","confidence":0.72,"rationale":"3m breakout structure is bullish, 2m and 1m confirm continuation, and 5m is only backdrop rather than a veto","thesis_tags":["trend","continuation"],"requested_risk_fraction":0.004,"stop_loss_price":1.08342,"take_profit_price":1.08428,"context_signature":"..."}\n'
            "Example short: "
            '{"action":"enter_short","confidence":0.70,"rationale":"3m trend turned bearish after stop-loss reversal and the screenshot shows clean lower highs with no nearby support","thesis_tags":["reversal","breakdown"],"requested_risk_fraction":0.003,"stop_loss_price":1.08418,"take_profit_price":1.08334,"context_signature":"..."}\n'
            f"Screenshot metadata:\n{json.dumps(screenshot, default=str, separators=(',', ':'))}\n"
            f"Context packet:\n{json.dumps(context_packet, default=str, separators=(',', ':'))}"
        )

    def fallback_decision(self, rationale: str) -> MT5V60EntryDecision:
        return MT5V60EntryDecision(action="hold", confidence=0.0, rationale=rationale, thesis_tags=[])

    def _parse_decision(self, raw_response: str) -> MT5V60EntryDecision:
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
            decision = MT5V60EntryDecision.model_validate(payload)
        except Exception:
            return self.fallback_decision("Model returned a JSON payload that failed validation.")
        if decision.action != "hold" and (decision.stop_loss_price is None or decision.take_profit_price is None):
            return self.fallback_decision("Model returned an entry without explicit stop loss and take profit prices.")
        return decision

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
        stop_loss_price = normalized.get("stop_loss_price", normalized.get("stop_loss", normalized.get("sl")))
        if stop_loss_price is not None:
            normalized["stop_loss_price"] = stop_loss_price
        take_profit_price = normalized.get("take_profit_price", normalized.get("take_profit", normalized.get("tp")))
        if take_profit_price is not None:
            normalized["take_profit_price"] = take_profit_price
        return normalized
