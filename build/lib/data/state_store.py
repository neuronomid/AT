from data.schemas import AccountSnapshot, MarketSnapshot


class StateStore:
    """In-memory hot state for the initial research scaffold."""

    def __init__(self) -> None:
        self.market_snapshot: MarketSnapshot | None = None
        self.account_snapshot: AccountSnapshot | None = None

    def update_market(self, snapshot: MarketSnapshot) -> None:
        self.market_snapshot = snapshot

    def update_account(self, snapshot: AccountSnapshot) -> None:
        self.account_snapshot = snapshot
