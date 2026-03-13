from __future__ import annotations

from data.schemas import DiscoveryReport, DiscoveredStrategySpec, InverseAppendixSummary


def render_discovery_report_markdown(report: DiscoveryReport) -> str:
    lines = [
        "# Discovery Research Report",
        "",
        "## Dataset",
        f"- Symbol: {report.dataset.symbol}",
        f"- Timeframe: {report.dataset.timeframe}",
        f"- Window: {report.dataset.start_at.isoformat()} to {report.dataset.end_at.isoformat()}",
        f"- Warmup start: {report.dataset.warmup_start_at.isoformat()}",
        f"- Total fetched bars: {report.dataset.total_bars}",
        f"- Evaluation bars: {report.dataset.evaluation_bars}",
        f"- Evaluable bars: {report.dataset.evaluable_bars}",
        f"- Estimated round-trip cost: {report.dataset.estimated_round_trip_cost_bps:.2f} bps",
        "",
        "## Headline Findings",
    ]
    for finding in report.headline_findings:
        lines.append(f"- {finding}")

    lines.extend(["", "## Regime Summary"])
    for regime, count in sorted(report.regime_summary.regime_occupancy.items()):
        avg_forward = report.regime_summary.average_forward_60m_bps.get(regime, 0.0)
        avg_prob = report.regime_summary.average_probability.get(regime, 0.0)
        lines.append(
            f"- {regime}: occupancy={count}, avg_forward_60m={avg_forward:.2f} bps, avg_probability={avg_prob:.2%}"
        )

    if report.regime_summary.regime_transitions:
        lines.extend(["", "## Regime Transitions"])
        for transition, count in sorted(report.regime_summary.regime_transitions.items()):
            lines.append(f"- {transition}: {count}")

    if report.indicator_bucket_tables:
        lines.extend(["", "## Indicator Buckets"])
        for table in report.indicator_bucket_tables:
            bucket_summary = ", ".join(
                f"{bucket}={value:.2f} bps" for bucket, value in sorted(table.buckets.items())
            )
            lines.append(f"- {table.direction} {table.indicator}: {bucket_summary}")

    if report.selected_pattern is not None:
        pattern = report.selected_pattern
        lines.extend(
            [
                "",
                "## Selected Pattern",
                f"- Direction: {pattern.direction}",
                f"- Regime: {pattern.regime}",
                f"- Support: {pattern.support_count}",
                f"- Score after costs: {pattern.score_bps:.2f} bps",
                f"- Forward 15m mean: {pattern.forward_15m_mean_bps:.2f} bps",
                f"- Forward 30m mean: {pattern.forward_30m_mean_bps:.2f} bps",
                f"- Forward 60m mean: {pattern.forward_60m_mean_bps:.2f} bps",
                f"- Mean favorable excursion: {pattern.mean_favorable_excursion_bps:.2f} bps",
                f"- Mean adverse excursion: {pattern.mean_adverse_excursion_bps:.2f} bps",
                f"- P60 favorable excursion: {pattern.percentile_60_favorable_excursion_bps:.2f} bps",
                f"- P60 adverse excursion: {pattern.percentile_60_adverse_excursion_bps:.2f} bps",
                f"- P85 favorable excursion: {pattern.percentile_85_favorable_excursion_bps:.2f} bps",
                f"- Median bars to peak favorable: {pattern.median_bars_to_peak_favorable}",
                f"- ATR band: {pattern.atr_band[0]:.2f} to {pattern.atr_band[1]:.2f}",
            ]
        )
        for key, value in sorted(pattern.thresholds.items()):
            lines.append(f"- {key}: {value}")

    return "\n".join(lines)


def render_discovered_strategy_markdown(strategy: DiscoveredStrategySpec) -> str:
    lines = [
        f"# Candidate Strategy: {strategy.policy_label}",
        "",
        f"- Direction: {strategy.direction}",
        f"- Source regime: {strategy.source_regime}",
        f"- Notes: {strategy.notes}",
        "",
        "## Thresholds",
    ]
    for key, value in sorted(strategy.thresholds.items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Strategy Config"])
    for key, value in sorted(strategy.strategy_config.items()):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def render_inverse_appendix_markdown(appendix: InverseAppendixSummary) -> str:
    lines = [
        "# Inverse Research Appendix",
        "",
        f"- Enabled: {appendix.enabled}",
        f"- Headline: {appendix.headline}",
    ]
    if appendix.selected_pattern is not None:
        pattern = appendix.selected_pattern
        lines.extend(
            [
                "",
                "## Selected Bear Pattern",
                f"- Support: {pattern.support_count}",
                f"- Score after costs: {pattern.score_bps:.2f} bps",
                f"- Forward 60m mean: {pattern.forward_60m_mean_bps:.2f} bps",
            ]
        )
    if appendix.strategy is not None:
        lines.extend(["", "## Synthesized Inverse Strategy", f"- Policy: {appendix.strategy.policy_label}"])
    return "\n".join(lines)
