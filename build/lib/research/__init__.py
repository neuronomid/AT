"""Discovery-first research utilities for ETH/USD strategy generation."""

from research.discovery import DiscoveryResearcher
from research.reporting import (
    render_discovered_strategy_markdown,
    render_discovery_report_markdown,
    render_inverse_appendix_markdown,
)

__all__ = [
    "DiscoveryResearcher",
    "render_discovered_strategy_markdown",
    "render_discovery_report_markdown",
    "render_inverse_appendix_markdown",
]
