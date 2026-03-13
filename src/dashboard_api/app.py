from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import get_settings
from control_plane.models import AgentConfigRecord, BacktestJobRequest, StrategyPromotionRequest
from control_plane.policies import ensure_default_policies
from evaluation.backtest_runner import run_backtest_job
from memory.supabase import SupabaseStore


class PolicyUpsertRequest(BaseModel):
    policy_name: str
    version: str
    status: str = "candidate"
    thresholds: dict[str, float] = Field(default_factory=dict)
    risk_params: dict[str, Any] = Field(default_factory=dict)
    strategy_config: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.backtest_tasks = set()
    yield
    tasks = list(app.state.backtest_tasks)
    for task in tasks:
        task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AT Dashboard API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _store() -> SupabaseStore:
        if settings.supabase_db_dsn is None:
            raise HTTPException(status_code=500, detail="SUPABASE_DB_URL is not configured.")
        return SupabaseStore(settings.supabase_db_dsn)

    store = _store()
    ensure_default_policies(store, settings)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/overview")
    async def overview() -> dict[str, Any]:
        local_store = _store()
        agent_statuses = local_store.list_agent_status()
        decisions = local_store.list_recent_decisions(limit=200)
        orders = local_store.list_recent_orders(limit=200)
        outcomes = local_store.list_recent_trade_outcomes(limit=250)
        lessons = local_store.list_recent_lessons(limit=50)
        backtest_runs = local_store.list_backtest_runs(limit=20)
        backtest_jobs = local_store.list_backtest_jobs(limit=20)
        promotions = local_store.list_strategy_promotions(limit=20)
        return {
            "summary": {
                "configured_agents": len(agent_statuses),
                "active_agents": sum(1 for row in agent_statuses if row.get("configured_status") == "active"),
                "healthy_agents": sum(1 for row in agent_statuses if row.get("runtime_status") == "healthy"),
                "recent_decisions": len(decisions),
                "recent_orders": len(orders),
                "recent_outcomes": len(outcomes),
            },
            "agent_statuses": agent_statuses,
            "decisions": decisions,
            "orders": orders,
            "outcomes": outcomes,
            "lessons": lessons,
            "backtest_runs": backtest_runs,
            "backtest_jobs": backtest_jobs,
            "promotions": promotions,
        }

    @app.get("/api/agents")
    async def list_agents() -> list[dict[str, Any]]:
        local_store = _store()
        return [agent.model_dump(mode="json") for agent in local_store.list_agent_configs()]

    @app.post("/api/agents")
    async def save_agent(agent: AgentConfigRecord) -> dict[str, str]:
        local_store = _store()
        agent_id = local_store.upsert_agent_config(agent)
        return {"id": agent_id, "agent_name": agent.agent_name}

    @app.put("/api/agents/{agent_name}")
    async def update_agent(agent_name: str, agent: AgentConfigRecord) -> dict[str, str]:
        local_store = _store()
        payload = agent.model_copy(update={"agent_name": agent_name})
        agent_id = local_store.upsert_agent_config(payload)
        return {"id": agent_id, "agent_name": agent_name}

    @app.get("/api/policies")
    async def list_policies() -> list[dict[str, Any]]:
        local_store = _store()
        return [policy.model_dump(mode="json") for policy in local_store.list_policy_versions()]

    @app.post("/api/policies")
    async def save_policy(request: PolicyUpsertRequest) -> dict[str, str]:
        local_store = _store()
        policy_id = local_store.upsert_policy_version(
            policy_name=request.policy_name,
            version=request.version,
            status=request.status,
            thresholds=request.thresholds,
            risk_params=request.risk_params,
            strategy_config=request.strategy_config,
            notes=request.notes,
        )
        return {"id": policy_id}

    async def _execute_job(job_id: str, request: BacktestJobRequest) -> None:
        task_store = _store()
        await run_backtest_job(
            settings=settings,
            store=task_store,
            request=request,
            requested_by="dashboard-ui",
            job_id=job_id,
        )

    @app.get("/api/backtests/jobs")
    async def list_backtest_jobs() -> list[dict[str, Any]]:
        local_store = _store()
        return local_store.list_backtest_jobs(limit=50)

    @app.post("/api/backtests/jobs")
    async def create_backtest_job(request: BacktestJobRequest) -> dict[str, str]:
        local_store = _store()
        job_id = local_store.create_backtest_job(request, requested_by="dashboard-ui")
        task = asyncio.create_task(_execute_job(job_id, request))
        app.state.backtest_tasks.add(task)
        task.add_done_callback(app.state.backtest_tasks.discard)
        return {"job_id": job_id, "status": "queued"}

    @app.get("/api/backtests/runs")
    async def list_backtest_runs() -> list[dict[str, Any]]:
        local_store = _store()
        return local_store.list_backtest_runs(limit=50)

    @app.get("/api/backtests/runs/{run_id}")
    async def backtest_run_detail(run_id: str) -> dict[str, Any]:
        local_store = _store()
        details = local_store.get_backtest_run_details(run_id)
        if details is None:
            raise HTTPException(status_code=404, detail="Backtest run not found.")
        return details

    @app.get("/api/promotions")
    async def list_promotions(agent_name: str | None = None) -> list[dict[str, Any]]:
        local_store = _store()
        return local_store.list_strategy_promotions(limit=100, agent_name=agent_name)

    @app.post("/api/promotions")
    async def create_promotion(request: StrategyPromotionRequest) -> dict[str, str]:
        local_store = _store()
        promotion_id = local_store.promote_strategy(
            agent_config_id=request.agent_config_id,
            new_policy_version_id=request.new_policy_version_id,
            rationale=request.rationale,
            promoted_by=request.promoted_by,
            source_run_id=request.source_run_id,
            metadata=request.metadata,
        )
        return {"id": promotion_id}

    @app.get("/api/history")
    async def history(agent_name: str | None = None) -> dict[str, Any]:
        local_store = _store()
        return {
            "decisions": local_store.list_recent_decisions(agent_name=agent_name, limit=300),
            "orders": local_store.list_recent_orders(agent_name=agent_name, limit=300),
            "outcomes": local_store.list_recent_trade_outcomes(agent_name=agent_name, limit=300),
            "lessons": local_store.list_recent_lessons(limit=200),
            "promotions": local_store.list_strategy_promotions(limit=200, agent_name=agent_name),
        }

    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "dashboard_api.app:create_app",
        factory=True,
        host=settings.dashboard_api_host,
        port=settings.dashboard_api_port,
        reload=False,
    )
