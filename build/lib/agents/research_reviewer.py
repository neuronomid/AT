from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from data.schemas import BacktestReport, DiscoveryReport, InverseAppendixSummary, StrategyAdvice


SYSTEM_PROMPT = """
You are a trading strategy research reviewer.

You are reviewing a discovery-first ETH/USD research cycle. Your job is to assess whether the discovered strategy is coherent, realistic, and worth another engineering pass.

Rules:
- Keep the primary strategy spot-compatible and long/flat.
- Treat the inverse appendix as research only.
- Do not recommend increasing risk to manufacture returns.
- Focus on regime fit, trade quality, execution realism, and robustness.
- Use concise markdown.
- Always produce:
  1. a one-paragraph summary
  2. exactly 4 assessment bullets
  3. a short "Do Next" section with the next 3 engineering tasks
""".strip()


class ResearchReviewAdvisor:
    def __init__(self, *, api_key: str, model: str, base_url: str) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    def build_prompt(
        self,
        *,
        discovery_report: DiscoveryReport,
        backtest_3m: BacktestReport,
        backtest_6m: BacktestReport | None = None,
        inverse_appendix: InverseAppendixSummary | None = None,
    ) -> str:
        pattern = discovery_report.selected_pattern
        pattern_lines = ["- No primary pattern was available."]
        if pattern is not None:
            pattern_lines = [
                f"- regime: {pattern.regime}",
                f"- support_count: {pattern.support_count}",
                f"- score_bps_after_costs: {pattern.score_bps}",
                f"- forward_60m_mean_bps: {pattern.forward_60m_mean_bps}",
                f"- mean_favorable_excursion_bps: {pattern.mean_favorable_excursion_bps}",
                f"- mean_adverse_excursion_bps: {pattern.mean_adverse_excursion_bps}",
                f"- median_bars_to_peak_favorable: {pattern.median_bars_to_peak_favorable}",
                f"- thresholds: {pattern.thresholds}",
                f"- atr_band: {pattern.atr_band}",
            ]

        inverse_lines = ["- inverse appendix disabled or unavailable."]
        if inverse_appendix is not None:
            inverse_lines = [
                f"- enabled: {inverse_appendix.enabled}",
                f"- headline: {inverse_appendix.headline}",
            ]
            if inverse_appendix.selected_pattern is not None:
                inverse_lines.append(
                    "- selected_pattern: "
                    f"support={inverse_appendix.selected_pattern.support_count}, "
                    f"score={inverse_appendix.selected_pattern.score_bps}, "
                    f"forward_60m={inverse_appendix.selected_pattern.forward_60m_mean_bps}"
                )
            if inverse_appendix.strategy is not None:
                inverse_lines.append(f"- inverse_policy_label: {inverse_appendix.strategy.policy_label}")

        validation_lines = ["- six_month_validation: not_run"]
        if backtest_6m is not None:
            validation_lines = [
                "- six_month_validation: completed",
                f"- six_month_score: {backtest_6m.candidate.score}",
                f"- six_month_realized_pnl_bps: {backtest_6m.candidate.realized_pnl_bps}",
                f"- six_month_average_trade_bps: {backtest_6m.candidate.average_trade_bps}",
                f"- six_month_closed_trades: {backtest_6m.candidate.closed_trades}",
                f"- six_month_decision: {backtest_6m.decision.status}",
            ]

        strategy_lines = ["- No synthesized strategy was available."]
        if discovery_report.candidate_strategy is not None:
            strategy_lines = [
                f"- policy_label: {discovery_report.candidate_strategy.policy_label}",
                f"- direction: {discovery_report.candidate_strategy.direction}",
                f"- source_regime: {discovery_report.candidate_strategy.source_regime}",
                f"- thresholds: {discovery_report.candidate_strategy.thresholds}",
                f"- strategy_config: {discovery_report.candidate_strategy.strategy_config}",
            ]

        bucket_lines = [
            f"- {bucket.direction}:{bucket.indicator} => {bucket.buckets}"
            for bucket in discovery_report.indicator_bucket_tables[:6]
        ]
        if not bucket_lines:
            bucket_lines = ["- No indicator bucket tables were available."]

        return f"""
Review this discovery-first ETH/USD research cycle.

Dataset summary:
- symbol: {discovery_report.dataset.symbol}
- timeframe: {discovery_report.dataset.timeframe}
- window: {discovery_report.dataset.start_at.isoformat()} to {discovery_report.dataset.end_at.isoformat()}
- warmup_start: {discovery_report.dataset.warmup_start_at.isoformat()}
- total_bars: {discovery_report.dataset.total_bars}
- evaluation_bars: {discovery_report.dataset.evaluation_bars}
- evaluable_bars: {discovery_report.dataset.evaluable_bars}
- estimated_round_trip_cost_bps: {discovery_report.dataset.estimated_round_trip_cost_bps}

Headline findings:
{chr(10).join(f"- {finding}" for finding in discovery_report.headline_findings)}

Regime summary:
- occupancy: {discovery_report.regime_summary.regime_occupancy}
- transitions: {discovery_report.regime_summary.regime_transitions}
- average_forward_60m_bps: {discovery_report.regime_summary.average_forward_60m_bps}
- average_probability: {discovery_report.regime_summary.average_probability}

Indicator bucket tables:
{chr(10).join(bucket_lines)}

Selected primary pattern:
{chr(10).join(pattern_lines)}

Synthesized strategy:
{chr(10).join(strategy_lines)}

Three-month backtest:
- score: {backtest_3m.candidate.score}
- realized_pnl_bps: {backtest_3m.candidate.realized_pnl_bps}
- average_trade_bps: {backtest_3m.candidate.average_trade_bps}
- closed_trades: {backtest_3m.candidate.closed_trades}
- win_rate: {backtest_3m.candidate.win_rate}
- drawdown_bps: {backtest_3m.candidate.max_drawdown_bps}
- exposure_ratio: {backtest_3m.candidate.exposure_ratio}
- decision: {backtest_3m.decision.status}

Inverse appendix:
{chr(10).join(inverse_lines)}

Validation:
{chr(10).join(validation_lines)}

Produce markdown with:
1. Summary
2. Assessment
3. Do Next
""".strip()

    async def advise(
        self,
        *,
        discovery_report: DiscoveryReport,
        backtest_3m: BacktestReport,
        backtest_6m: BacktestReport | None = None,
        inverse_appendix: InverseAppendixSummary | None = None,
    ) -> StrategyAdvice:
        prompt = self.build_prompt(
            discovery_report=discovery_report,
            backtest_3m=backtest_3m,
            backtest_6m=backtest_6m,
            inverse_appendix=inverse_appendix,
        )
        raw_response = await self._call_model(prompt)
        return StrategyAdvice(
            generated_at=datetime.now(timezone.utc),
            model=self._model,
            summary=self._first_paragraph(raw_response),
            recommendations=self._extract_bullets(raw_response),
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

    def _extract_bullets(self, text: str) -> list[str]:
        bullets: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                bullets.append(stripped[2:].strip())
            elif len(stripped) > 3 and stripped[0].isdigit() and stripped[1:3] == ". ":
                bullets.append(stripped[3:].strip())
        seen: set[str] = set()
        ordered: list[str] = []
        for bullet in bullets:
            if bullet and bullet not in seen:
                seen.add(bullet)
                ordered.append(bullet)
        return ordered[:6]
