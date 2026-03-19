from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from brokers.mt5_v60.bridge_state import MT5V60BridgeState
from data.mt5_v60_schemas import MT5V60BridgeCommandPollResponse, MT5V60BridgeSnapshot, MT5V60ExecutionAck
from infra.logging import get_logger
from memory.journal import Journal
from memory.supabase_mt5_v60 import SupabaseMT5V60Store


def _safe_store_call(logger, operation: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        logger.error("mt5_v60_bridge_store_error operation=%s error=%s", operation, exc)


def create_mt5_v60_bridge_app(
    state: MT5V60BridgeState,
    *,
    journal: Journal | None = None,
    store: SupabaseMT5V60Store | None = None,
    agent_name: str = "mt5_v60",
) -> FastAPI:
    app = FastAPI(title="AT MT5 Bridge V6.0", version="6.0")
    logger = get_logger(__name__)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc: RequestValidationError) -> JSONResponse:
        body = (await request.body()).decode("utf-8", errors="replace")
        safe_errors = []
        for error in exc.errors():
            safe_error = dict(error)
            if "input" in safe_error:
                safe_error["input"] = str(type(safe_error["input"]).__name__)
            safe_errors.append(safe_error)
        logger.error("mt5_v60_bridge_validation_error path=%s errors=%s body=%s", request.url.path, safe_errors, body)
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    @app.post("/bridge/snapshot")
    async def publish_snapshot(snapshot: MT5V60BridgeSnapshot) -> dict[str, object]:
        normalized = await state.publish_snapshot(snapshot)
        if journal is not None:
            journal.record({"record_type": "mt5_v60_bridge_snapshot", "agent_name": agent_name, "snapshot": normalized.model_dump(mode="json")})
        if store is not None:
            _safe_store_call(logger, "insert_mt5_v60_bridge_snapshot", store.insert_mt5_v60_bridge_snapshot, agent_name=agent_name, snapshot=normalized)
            _safe_store_call(logger, "insert_mt5_v60_close_events", store.insert_mt5_v60_close_events, agent_name=agent_name, close_events=normalized.recent_close_events)
        return {"status": "ok", "pending_command_count": normalized.health.pending_command_count}

    @app.get("/bridge/commands", response_model=MT5V60BridgeCommandPollResponse)
    async def poll_commands(limit: int = 10) -> MT5V60BridgeCommandPollResponse:
        commands = await state.poll_commands(limit=limit)
        if commands and journal is not None:
            for command in commands:
                journal.record({"record_type": "mt5_v60_bridge_command_polled", "agent_name": agent_name, "command": command.model_dump(mode="json")})
        return MT5V60BridgeCommandPollResponse(commands=commands)

    @app.post("/bridge/acks")
    async def record_ack(ack: MT5V60ExecutionAck) -> dict[str, object]:
        await state.ack_command(ack)
        if journal is not None:
            journal.record({"record_type": "mt5_v60_bridge_ack", "agent_name": agent_name, "ack": ack.model_dump(mode="json")})
        if store is not None:
            _safe_store_call(logger, "insert_mt5_v60_bridge_ack", store.insert_mt5_v60_bridge_ack, agent_name=agent_name, ack=ack)
        return {"status": "ok"}

    @app.get("/bridge/health")
    async def health() -> dict[str, object]:
        current = await state.health()
        return current.model_dump(mode="json")

    return app
