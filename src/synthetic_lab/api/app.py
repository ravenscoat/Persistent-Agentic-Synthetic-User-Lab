from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from synthetic_lab.contracts import RunRecord, RunStatus
from synthetic_lab.storage import InMemoryStateRepository, PostgresMemoryRepository, PostgresStateRepository
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository
from synthetic_lab.config import Settings
from synthetic_lab.runtime.execution import RunExecutionManager, RunExecutor
from synthetic_lab.runtime.demo_executor import DemoAgentExecutor
from synthetic_lab.runtime.scenario_executor import ScenarioExecutor
from synthetic_lab.reporting.reports import ReportBuilder


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


def create_app(
    state: Any | None = None,
    memory: Any | None = None,
    executor: Callable[[RunRecord], Awaitable[None]] | None = None,
) -> FastAPI:
    settings = Settings()
    using_postgres = state is None and bool(settings.postgres_dsn)
    repository = state or (PostgresStateRepository.from_dsn(settings.postgres_dsn) if using_postgres else InMemoryStateRepository())
    app = FastAPI(title="Persistent Synthetic User Lab", version="0.1.0")
    app.state.repository = repository
    app.state.memory = memory or (PostgresMemoryRepository.from_dsn(settings.postgres_dsn) if using_postgres else InMemoryMemoryRepository())
    app.state.execution = RunExecutionManager(repository)
    if executor is None:
        demo_executor = DemoAgentExecutor(repository, app.state.memory, settings=settings)
        scenario_executor = ScenarioExecutor(repository, app.state.memory, settings=settings)

        async def default_executor(run: RunRecord) -> None:
            if run.scenario_id in {"trial_return", "payment_retry", "ownership_transfer", "interrupted_onboarding", "stale_task_status"}:
                await scenario_executor(run)
            else:
                await demo_executor(run)

        executor = default_executor

    @app.on_event("shutdown")
    async def close_execution_manager() -> None:
        await app.state.execution.close()

    @app.on_event("startup")
    async def prepare_persistence() -> None:
        if using_postgres:
            root = Path(__file__).resolve().parents[3] / "migrations"
            await repository.apply_migration(root / "002_initial_postgres.sql")
        # Rehydrate runs that were active when the API process stopped. The
        # executor is idempotent at the session boundary and resumes from the
        # last durable checkpoint rather than assuming the browser survived.
        for recovered in await repository.list_runs(limit=1000):
            if recovered.status is RunStatus.RUNNING:
                await app.state.execution.start(recovered, executor)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        """Serve the operator console with a small server-rendered bootstrap payload."""
        runs = await repository.list_runs(limit=100)
        bootstrap = [run.model_dump(mode="json") for run in runs]
        template = (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")
        payload = json.dumps(bootstrap, separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/")
        links = "".join(f'<a class="run-api-link" href="/api/runs/{run.id}/summary">{run.id}</a>' for run in runs)
        return template.replace("<!-- INITIAL_RUNS -->", payload).replace("<!-- RUN_LINKS -->", links)

    @app.get("/dashboard/static/{asset}")
    async def dashboard_static(asset: str) -> FileResponse:
        """Serve dashboard assets without adding a frontend build dependency."""
        root = (Path(__file__).parent / "static").resolve()
        path = (root / asset).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(path)

    @app.get("/api/observability/config")
    async def observability_config() -> dict[str, Any]:
        """Return safe observability links; secrets are never sent to the browser."""
        from synthetic_lab.config import Settings
        import importlib.util

        settings = Settings()
        host = (settings.langfuse_host or "").rstrip("/")
        return {
            "langfuse_enabled": settings.langfuse_enabled,
            "langfuse_sdk_installed": importlib.util.find_spec("langfuse") is not None,
            "langfuse_host": host or None,
            "message": "Langfuse links appear when SUL_LANGFUSE_HOST, SUL_LANGFUSE_PUBLIC_KEY, and SUL_LANGFUSE_SECRET_KEY are configured.",
        }

    @app.post("/api/runs", status_code=201)
    async def create_run(request: CreateRunRequest) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        run = RunRecord(id=str(uuid4()), scenario_id=request.scenario_id, created_at=now, business_time=now, config_snapshot=request.config_snapshot)
        await repository.create_run(run)
        return run.model_dump(mode="json")

    @app.get("/api/runs")
    async def list_runs(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        runs = await repository.list_runs(limit=limit)
        return [run.model_dump(mode="json") for run in runs]

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        try:
            return (await repository.get_run(run_id)).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    async def transition(run_id: str, status: RunStatus) -> dict[str, Any]:
        try:
            return (await repository.transition_run(run_id, status)).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/runs/{run_id}/start")
    async def start_run(run_id: str) -> dict[str, Any]:
        run = await transition(run_id, RunStatus.RUNNING)
        if executor is not None:
            snapshot = await app.state.execution.start(RunRecord.model_validate(run), executor)
            run["execution"] = snapshot.as_dict()
        return run

    @app.post("/api/runs/{run_id}/pause")
    async def pause_run(run_id: str) -> dict[str, Any]:
        return await transition(run_id, RunStatus.PAUSED)

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict[str, Any]:
        return await transition(run_id, RunStatus.RUNNING)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        await app.state.execution.cancel(run_id)
        return await transition(run_id, RunStatus.CANCELLED)

    @app.get("/api/runs/{run_id}/execution")
    async def execution_status(run_id: str) -> dict[str, Any]:
        try:
            await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        snapshot = app.state.execution.status(run_id)
        return snapshot.as_dict() if snapshot else {"run_id": run_id, "state": "not_started"}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, after_sequence: int = Query(-1), limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        try:
            await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return [event.model_dump(mode="json") for event in await repository.list_events(run_id, after_sequence, limit)]

    @app.get("/api/runs/{run_id}/findings")
    async def findings(run_id: str) -> list[dict[str, Any]]:
        try:
            await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return [finding.model_dump(mode="json") for finding in await repository.list_findings(run_id)]

    @app.get("/api/runs/{run_id}/reports")
    async def run_reports(run_id: str) -> list[dict[str, Any]]:
        """Return evidence-backed reports assembled from durable records."""
        try:
            await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        events = await repository.list_events(run_id, limit=1000)
        reports = []
        for finding in await repository.list_findings(run_id):
            report = ReportBuilder().build(finding, events, [], explanation="Verdict is produced by the independent verifier; this report only assembles persisted evidence.")
            reports.append(report.as_dict())
        return reports

    @app.get("/api/runs/{run_id}/metrics")
    async def run_metrics(run_id: str) -> dict[str, Any]:
        """Aggregate latency, failures, retries, tools, and trace coverage."""
        try:
            await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        events = await repository.list_events(run_id, limit=1000)
        tools: dict[str, int] = {}
        failures = retries = model_latency = 0.0
        action_count = 0
        trace_count = 0
        for event in events:
            payload = event.payload or {}
            if event.kind == "tool_result":
                action_count += 1
                name = str(payload.get("tool_name") or "unknown")
                tools[name] = tools.get(name, 0) + 1
                model_latency += float(payload.get("model_latency_ms") or 0)
            if event.kind in {"model_failed", "session_failed", "session_blocked"} or payload.get("status") == "error":
                failures += 1
            retries += float(payload.get("retry_count") or 0)
            if payload.get("langfuse_trace_id"):
                trace_count += 1
        findings = await repository.list_findings(run_id)
        return {"run_id": run_id, "event_count": len(events), "action_count": action_count, "model_latency_ms": round(model_latency, 2), "failures": int(failures), "retries": int(retries), "tool_usage": tools, "findings": len(findings), "langfuse_trace_events": trace_count}

    @app.get("/api/runs/{run_id}/summary")
    async def run_summary(run_id: str) -> dict[str, Any]:
        """Return a compact operator view without exposing full page text."""
        try:
            await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        events = await repository.list_events(run_id, limit=1000)
        personas: dict[str, dict[str, Any]] = {}
        for event in events:
            persona_id = event.persona_id or "unassigned"
            item = personas.setdefault(persona_id, {"event_count": 0, "action_count": 0, "model_latency_ms": 0.0, "retrieved_memory_ids": []})
            item["event_count"] += 1
            if event.kind == "tool_result":
                item["action_count"] += 1
                item["model_latency_ms"] += float(event.payload.get("model_latency_ms") or 0)
            for memory_id in event.payload.get("retrieved_memory_ids", []):
                if memory_id not in item["retrieved_memory_ids"]:
                    item["retrieved_memory_ids"].append(memory_id)
        return {
            "run_id": run_id,
            "event_count": len(events),
            "event_sequences_unique": len({event.sequence for event in events}) == len(events),
            "last_event_at": events[-1].wall_time.isoformat() if events else None,
            "personas": personas,
            "findings": [finding.model_dump(mode="json") for finding in await repository.list_findings(run_id)],
        }

    return app


app = create_app()
