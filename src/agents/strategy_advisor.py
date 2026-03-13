from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from data.schemas import BacktestReport, ReviewSummary, StrategyAdvice


SYSTEM_PROMPT = """
You are a trading strategy research advisor.

You are reviewing a research-phase ETH/USD trading system. Your job is to propose safer, more selective, better-structured changes.

Rules:
- Do not suggest live self-modifying behavior.
- Do not recommend increasing risk to improve returns.
- Focus on regime filters, trade quality, exit logic, overtrading control, and realistic execution assumptions.
- Do not treat near-zero trade count as a valid solution; recommendations must preserve meaningful but controlled trading activity.
- Use concise markdown.
- Always produce:
  1. a one-paragraph summary
  2. exactly 5 concrete recommendations
  3. a short "Do Next" section with the next 3 engineering tasks
""".strip()


class StrategyAdvisor:
    def __init__(self, *, api_key: str, model: str, base_url: str) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def build_prompt(
        self,
        *,
        review_summary: ReviewSummary,
        backtest_report: BacktestReport,
        lessons: list[dict[str, object]],
    ) -> str:
        lesson_lines = [
            f"- {lesson.get('message', '')}"
            for lesson in lessons[:10]
            if isinstance(lesson.get("message"), str) and str(lesson.get("message")).strip()
        ]
        if not lesson_lines:
            lesson_lines = ["- No persisted lessons were available."]

        return f"""
Review the following trading artifacts and propose strategy improvements.

Review summary:
- total decision records: {review_summary.decision_records}
- trade reviews: {review_summary.trade_reviews}
- executable decisions: {review_summary.executable_decisions}
- risk rejections: {review_summary.risk_rejections}
- action counts: {review_summary.action_counts}
- rejection reasons: {review_summary.rejection_reasons}
- review outcomes: {review_summary.review_outcomes}

Backtest summary:
- symbol: {backtest_report.symbol}
- timeframe: {backtest_report.timeframe}
- range: {backtest_report.start_at.isoformat()} to {backtest_report.end_at.isoformat()}
- bars: {backtest_report.total_bars}
- windows: {len(backtest_report.windows)}
- decision: {backtest_report.decision.status}
- baseline score: {backtest_report.decision.baseline_score}
- candidate score: {backtest_report.decision.candidate_score}
- baseline executed actions: {backtest_report.baseline.executed_actions}
- candidate executed actions: {backtest_report.candidate.executed_actions}
- baseline closed trades: {backtest_report.baseline.closed_trades}
- candidate closed trades: {backtest_report.candidate.closed_trades}
- baseline win rate: {backtest_report.baseline.win_rate}
- candidate win rate: {backtest_report.candidate.win_rate}
- baseline avg trade bps: {backtest_report.baseline.average_trade_bps}
- candidate avg trade bps: {backtest_report.candidate.average_trade_bps}
- baseline drawdown bps: {backtest_report.baseline.max_drawdown_bps}
- candidate drawdown bps: {backtest_report.candidate.max_drawdown_bps}
- baseline exposure ratio: {backtest_report.baseline.exposure_ratio}
- candidate exposure ratio: {backtest_report.candidate.exposure_ratio}
{self._hmm_summary(backtest_report)}

Lessons:
{chr(10).join(lesson_lines)}

Current direction:
- V2 is regime-aware
- V2 supports long and short in backtests
- V2 uses 1R stop, partial profit at 1R, trailing remainder toward 2R
- LLM suggestions must remain offline and advisory
- A strategy is not considered successful if it becomes profitable only by collapsing trade count close to zero

Produce markdown with:
1. Summary
2. Recommendations
3. Do Next
""".strip()

    async def advise(
        self,
        *,
        review_summary: ReviewSummary,
        backtest_report: BacktestReport,
        lessons: list[dict[str, object]],
    ) -> StrategyAdvice:
        prompt = self.build_prompt(
            review_summary=review_summary,
            backtest_report=backtest_report,
            lessons=lessons,
        )
        raw_response = await self._call_model(prompt)
        return StrategyAdvice(
            generated_at=datetime.now(timezone.utc),
            model=self._model,
            summary=self._first_paragraph(raw_response),
            recommendations=self._extract_recommendations(raw_response),
            prompt=prompt,
            raw_response=raw_response,
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
        text = self._extract_output_text(payload)
        if not text:
            raise RuntimeError("OpenAI response did not contain output text.")
        return text

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

    def _first_paragraph(self, text: str) -> str:
        for block in text.split("\n\n"):
            stripped = block.strip()
            if stripped:
                return stripped.replace("\n", " ")
        return ""

    def _extract_recommendations(self, text: str) -> list[str]:
        recommendations: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                recommendations.append(stripped[2:].strip())
            elif len(stripped) > 3 and stripped[0].isdigit() and stripped[1:3] == ". ":
                recommendations.append(stripped[3:].strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for recommendation in recommendations:
            if recommendation and recommendation not in seen:
                seen.add(recommendation)
                deduped.append(recommendation)
        return deduped[:5]

    def _hmm_summary(self, backtest_report: BacktestReport) -> str:
        lines: list[str] = []
        if backtest_report.trade_summary is not None:
            summary = backtest_report.trade_summary
            lines.extend(
                [
                    "- candidate winning trades: "
                    f"{summary.winning_trades}",
                    f"- candidate losing trades: {summary.losing_trades}",
                    f"- average planned risk usd: {summary.average_planned_risk_usd}",
                    f"- average planned stop loss bps: {summary.average_planned_stop_loss_bps}",
                    f"- average planned take profit bps: {summary.average_planned_take_profit_bps}",
                    f"- exit reasons: {summary.exit_reason_counts}",
                ]
            )
        if backtest_report.regime_summary is not None:
            summary = backtest_report.regime_summary
            lines.extend(
                [
                    f"- regime occupancy: {summary.regime_occupancy}",
                    f"- entry regime counts: {summary.entry_regime_counts}",
                    f"- average regime probability: {summary.average_regime_probability}",
                ]
            )
        if not lines:
            return ""
        return "\n" + "\n".join(lines)
