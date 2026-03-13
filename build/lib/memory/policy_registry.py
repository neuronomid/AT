class PolicyRegistry:
    """Versions prompts, thresholds, and strategy policies."""

    def register(self, name: str, version: str) -> str:
        return f"{name}:{version}"
