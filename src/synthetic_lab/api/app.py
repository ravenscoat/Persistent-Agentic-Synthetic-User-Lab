from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from synthetic_lab.contracts import RunRecord, RunStatus
from synthetic_lab.storage import InMemoryStateRepository


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


def create_app(state: InMemoryStateRepository | None = None) -> FastAPI:
    repository = state or InMemoryStateRepository()
    app = FastAPI(title="Persistent Synthetic User Lab", version="0.1.0")
    app.state.repository = repository

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        """Small operator view for local demos; production UIs can use the API."""
        runs = sorted(repository.runs.values(), key=lambda item: item.created_at, reverse=True)
        rows = "".join(
            f'<tr><td>{run.id}</td><td>{run.scenario_id}</td><td>{run.status.value}</td>'
            f'<td><a href="/api/runs/{run.id}/events">events</a> · '
            f'<a href="/api/runs/{run.id}/findings">findings</a> · '
            f'<a href="/api/runs/{run.id}/summary">summary</a></td></tr>'
            for run in runs
        ) or '<tr><td colspan="4">No runs yet</td></tr>'
        finding_count = len(repository.findings)
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>"
            "Synthetic User Lab</title></head><body><main>"
            "<h1>Persistent Synthetic User Lab</h1>"
            f"<p id='run-count'>Runs: {len(runs)} · Findings: {finding_count}</p>"
            "<table><thead><tr><th>ID</th><th>Scenario</th><th>Status</th><th>Links</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></main></body></html>"
        )

    @app.post("/api/runs", status_code=201)
    async def create_run(request: CreateRunRequest) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        run = RunRecord(id=str(uuid4()), scenario_id=request.scenario_id, created_at=now, business_time=now, config_snapshot=request.config_snapshot)
        await repository.create_run(run)
        return run.model_dump(mode="json")

    @app.get("/api/runs")
    async def list_runs(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        runs = sorted(repository.runs.values(), key=lambda item: item.created_at, reverse=True)
        return [run.model_dump(mode="json") for run in runs[:limit]]

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
        return await transition(run_id, RunStatus.RUNNING)

    @app.post("/api/runs/{run_id}/pause")
    async def pause_run(run_id: str) -> dict[str, Any]:
        return await transition(run_id, RunStatus.PAUSED)

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict[str, Any]:
        return await transition(run_id, RunStatus.RUNNING)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        return await transition(run_id, RunStatus.CANCELLED)

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
        return [finding.model_dump(mode="json") for finding in repository.findings.values() if finding.run_id == run_id]

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
        return {"run_id": run_id, "event_count": len(events), "event_sequences_unique": len({event.sequence for event in events}) == len(events), "personas": personas, "findings": [finding.model_dump(mode="json") for finding in repository.findings.values() if finding.run_id == run_id]}

    return app


app = create_app()
