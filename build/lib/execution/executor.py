from brokers.alpaca.trading import AlpacaTradingService
from data.schemas import OrderRequest, OrderSnapshot
from execution.order_manager import OrderManager


class ExecutionExecutor:
    def __init__(self, trading_service: AlpacaTradingService, order_manager: OrderManager) -> None:
        self._trading_service = trading_service
        self._order_manager = order_manager

    async def place(self, order_request: OrderRequest) -> OrderSnapshot:
        order = await self._trading_service.submit_order(order_request)
        self._order_manager.mark_pending(order)
        return order
