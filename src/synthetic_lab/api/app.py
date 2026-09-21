from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import hmac
import html
import json
import re
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthetic_lab.contracts import RunRecord, RunStatus
from synthetic_lab.storage import InMemoryStateRepository, PostgresMemoryRepository, PostgresStateRepository
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository
from synthetic_lab.config import Settings
from synthetic_lab.runtime.execution import RunExecutionManager, RunExecutor
from synthetic_lab.runtime.demo_executor import DemoAgentExecutor
from synthetic_lab.runtime.scenario_executor import ScenarioExecutor
from synthetic_lab.runtime.product_executor import ProductAdapterExecutor
from synthetic_lab.product_adapter import AuthoredScenarioSpec, ProductTestPlanRequest, TestProfileRequest, authored_scenario, plan_product_test_with_local_model
from synthetic_lab.reporting.reports import ReportBuilder
from synthetic_lab.evaluation import EvaluationExecutor, EvaluationRequest, summarize_evaluation


_OPERATOR_COOKIE = "sul_operator_session"


def _issue_operator_session(secret: str) -> str:
    """Create a short-lived, tamper-evident operator session cookie."""
    issued = str(int(datetime.now(timezone.utc).timestamp())).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), issued, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(issued + b"." + signature).decode("ascii")


def _valid_operator_session(value: str | None, secret: str, ttl_seconds: int) -> bool:
    if not value:
        return False
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
        issued, signature = raw.split(b".", 1)
        expected = hmac.new(secret.encode("utf-8"), issued, hashlib.sha256).digest()
        age = int(datetime.now(timezone.utc).timestamp()) - int(issued.decode("ascii"))
        return 0 <= age <= ttl_seconds and hmac.compare_digest(signature, expected)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return False


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_authored_scenario(self) -> "CreateRunRequest":
        if "authored_scenario" in self.config_snapshot:
            AuthoredScenarioSpec.model_validate(self.config_snapshot["authored_scenario"])
        return self


def create_app(
    state: Any | None = None,
    memory: Any | None = None,
    executor: Callable[[RunRecord], Awaitable[None]] | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or Settings()
    using_postgres = state is None and bool(settings.postgres_dsn)
    repository = state or (PostgresStateRepository.from_dsn(settings.postgres_dsn) if using_postgres else InMemoryStateRepository())
    app = FastAPI(title="Persistent Synthetic User Lab", version="0.1.0")
    app.state.repository = repository
    app.state.memory = memory or (PostgresMemoryRepository.from_dsn(settings.postgres_dsn) if using_postgres else InMemoryMemoryRepository())
    app.state.execution = RunExecutionManager(repository)
    if executor is None:
        demo_executor = DemoAgentExecutor(repository, app.state.memory, settings=settings)
        scenario_executor = ScenarioExecutor(repository, app.state.memory, settings=settings)
        product_executor = ProductAdapterExecutor(repository, app.state.memory, settings=settings)

        async def default_executor(run: RunRecord) -> None:
            if authored_scenario(run.config_snapshot) is not None:
                await product_executor(run)
            elif run.scenario_id in {"trial_return", "payment_retry", "ownership_transfer", "interrupted_onboarding", "stale_task_status"}:
                await scenario_executor(run)
            else:
                await demo_executor(run)

        executor = default_executor
    evaluation_executor = EvaluationExecutor(repository, executor)

    @app.middleware("http")
    async def require_operator_login(request: Request, call_next):
        """Keep sensitive run data behind an optional operator session."""
        if not settings.dashboard_auth_enabled:
            return await call_next(request)
        path = request.url.path
        public = {"/health", "/metrics", "/login"}
        protected = path == "/dashboard" or path.startswith("/api/") or path.startswith("/reports/")
        authenticated = _valid_operator_session(
            request.cookies.get(_OPERATOR_COOKIE),
            str(settings.dashboard_session_secret or ""),
            settings.dashboard_session_ttl_seconds,
        )
        if protected and path not in public and not authenticated:
            if path.startswith("/api/"):
                return JSONResponse(status_code=401, content={"detail": "operator login required"})
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

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
    async def health() -> dict[str, Any]:
        runs = await repository.list_runs(limit=1000)
        return {"status": "ok", "storage": "postgres" if using_postgres else "in_memory", "run_count": len(runs)}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> str:
        """Small Prometheus-compatible operational snapshot for local deployment."""
        runs = await repository.list_runs(limit=1000)
        by_status: dict[str, int] = {}
        for run in runs:
            by_status[run.status.value] = by_status.get(run.status.value, 0) + 1
        lines = ["# HELP sul_runs_total Synthetic User Lab runs by lifecycle status.", "# TYPE sul_runs_total gauge"]
        lines.extend(f'sul_runs_total{{status="{status}"}} {count}' for status, count in sorted(by_status.items()))
        lines.extend(["# HELP sul_storage_backend Storage backend in use.", "# TYPE sul_storage_backend gauge", f'sul_storage_backend{{backend="{"postgres" if using_postgres else "in_memory"}"}} 1'])
        return "\n".join(lines) + "\n"

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        """Serve the operator console with a small server-rendered bootstrap payload."""
        runs = await repository.list_runs(limit=100)
        bootstrap = [run.model_dump(mode="json") for run in runs]
        template = (Path(__file__).parent / "static" / "lab.html").read_text(encoding="utf-8")
        # Version both assets by content so a browser cannot mix old CSS with
        # new markup after an update, including when only the stylesheet changes.
        for asset in ("lab.css", "lab.js"):
            digest = hashlib.sha256((Path(__file__).parent / "static" / asset).read_bytes()).hexdigest()[:12]
            template = re.sub(r"/dashboard/static/" + re.escape(asset) + r"(?:\?[^\"']*)?", f"/dashboard/static/{asset}?v={digest}", template)
        payload = json.dumps(bootstrap, separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/")
        links = "".join(f'<a class="run-api-link" href="/api/runs/{run.id}/summary">{run.id}</a>' for run in runs)
        return template.replace("<!-- INITIAL_RUNS -->", payload).replace("<!-- RUN_LINKS -->", links)

    @app.get("/login", response_class=HTMLResponse)
    async def operator_login_page(request: Request) -> str:
        if not settings.dashboard_auth_enabled:
            return RedirectResponse("/dashboard", status_code=303)
        if _valid_operator_session(request.cookies.get(_OPERATOR_COOKIE), str(settings.dashboard_session_secret or ""), settings.dashboard_session_ttl_seconds):
            return RedirectResponse("/dashboard", status_code=303)
        return """<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Synthetic User Lab · Operator login</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f6fb;font-family:Inter,system-ui,sans-serif;color:#172033}.card{width:min(390px,calc(100vw - 40px));padding:34px;background:white;border:1px solid #e3e8f1;border-radius:18px;box-shadow:0 16px 50px #17203312}h1{margin:0 0 8px;font-size:24px}p{color:#667085;line-height:1.55}label{display:grid;gap:8px;margin:23px 0 16px;font-size:13px;font-weight:700}input{border:1px solid #cfd7e6;border-radius:9px;padding:12px;font:inherit}button{width:100%;border:0;border-radius:9px;background:#525bd5;color:white;padding:12px;font-weight:700;cursor:pointer}</style></head><body><main class=\"card\"><h1>Operator login</h1><p>Use the deployment password to open the Synthetic User Lab console.</p><form method=\"post\" action=\"/login\"><label>Dashboard password<input name=\"password\" type=\"password\" required autofocus></label><button type=\"submit\">Open dashboard</button></form></main></body></html>"""

    @app.post("/login")
    async def operator_login(request: Request, password: str = Form(...)) -> RedirectResponse:
        configured = settings.dashboard_password or ""
        if not settings.dashboard_auth_enabled or not hmac.compare_digest(password, configured):
            return RedirectResponse("/login", status_code=303)
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(
            _OPERATOR_COOKIE,
            _issue_operator_session(str(settings.dashboard_session_secret)),
            max_age=settings.dashboard_session_ttl_seconds,
            httponly=True,
            secure=settings.dashboard_cookie_secure,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    async def operator_logout(request: Request) -> RedirectResponse:
        response = RedirectResponse("/login" if settings.dashboard_auth_enabled else "/dashboard", status_code=303)
        response.delete_cookie(_OPERATOR_COOKIE)
        return response

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
        import importlib.util

        host = (settings.langfuse_host or "").rstrip("/")
        return {
            "langfuse_enabled": settings.langfuse_enabled,
            "langfuse_sdk_installed": importlib.util.find_spec("langfuse") is not None,
            "langfuse_host": host or None,
            "message": "Langfuse links appear when SUL_LANGFUSE_HOST, SUL_LANGFUSE_PUBLIC_KEY, and SUL_LANGFUSE_SECRET_KEY are configured.",
        }

    @app.get("/api/product-adapters/schema")
    async def product_adapter_schema() -> dict[str, Any]:
        """Expose the exact authoring contract used by the runtime."""
        return AuthoredScenarioSpec.model_json_schema()

    @app.post("/api/test-plans")
    async def create_test_plan(request: ProductTestPlanRequest) -> dict[str, Any]:
        """Create a reviewable browser-test draft from a client's goal."""
        return (await plan_product_test_with_local_model(request, settings)).model_dump(mode="json")

    @app.post("/api/test-profiles", status_code=201)
    async def create_test_profile(request: TestProfileRequest) -> dict[str, Any]:
        """Save a reusable client journey through the normal durable run store."""
        now = datetime.now(timezone.utc)
        profile = RunRecord(
            id=str(uuid4()), scenario_id=f"profile:{uuid4()}", created_at=now, business_time=now,
            config_snapshot={"test_profile": request.model_dump(mode="json")},
        )
        await repository.create_run(profile)
        return {"id": profile.id, **request.model_dump(mode="json")}

    @app.get("/api/test-profiles")
    async def list_test_profiles(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        profiles = [run for run in await repository.list_runs(limit=1000) if run.scenario_id.startswith("profile:")]
        return [{"id": profile.id, "created_at": profile.created_at, **profile.config_snapshot["test_profile"]} for profile in profiles[:limit]]

    @app.post("/api/test-profiles/{profile_id}/runs", status_code=201)
    async def create_run_from_profile(profile_id: str) -> dict[str, Any]:
        try:
            profile = await repository.get_run(profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="test profile not found") from exc
        if not profile.scenario_id.startswith("profile:"):
            raise HTTPException(status_code=404, detail="test profile not found")
        now = datetime.now(timezone.utc)
        profile_data = profile.config_snapshot["test_profile"]
        run = RunRecord(
            id=str(uuid4()), scenario_id=f"profile-run:{profile_id[:8]}", created_at=now, business_time=now,
            config_snapshot={"authored_scenario": profile_data["scenario"], "profile_id": profile_id},
        )
        await repository.create_run(run)
        return run.model_dump(mode="json")

    @app.post("/api/runs", status_code=201)
    async def create_run(request: CreateRunRequest) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        run = RunRecord(id=str(uuid4()), scenario_id=request.scenario_id, created_at=now, business_time=now, config_snapshot=request.config_snapshot)
        await repository.create_run(run)
        return run.model_dump(mode="json")

    @app.post("/api/evaluations", status_code=202)
    async def create_evaluation(request: EvaluationRequest) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        campaign = RunRecord(
            id=str(uuid4()), scenario_id=f"evaluation:{request.scenario_id}",
            created_at=now, business_time=now,
            config_snapshot={"evaluation": request.model_dump(mode="json")},
        )
        await repository.create_run(campaign)
        running = await repository.transition_run(campaign.id, RunStatus.RUNNING)
        snapshot = await app.state.execution.start(running, evaluation_executor)
        result = summarize_evaluation(running, [])
        result["execution"] = snapshot.as_dict()
        return result

    @app.get("/api/evaluations")
    async def list_evaluations(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        campaigns = [run for run in await repository.list_runs(limit=1000) if run.scenario_id.startswith("evaluation:")]
        return [summarize_evaluation(run, await repository.list_events(run.id, limit=1000)) for run in campaigns[:limit]]

    @app.get("/api/evaluations/{evaluation_id}")
    async def get_evaluation(evaluation_id: str) -> dict[str, Any]:
        try:
            campaign = await repository.get_run(evaluation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="evaluation not found") from exc
        if not campaign.scenario_id.startswith("evaluation:"):
            raise HTTPException(status_code=404, detail="evaluation not found")
        return summarize_evaluation(campaign, await repository.list_events(evaluation_id, limit=1000))

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
            run = await repository.get_run(run_id)
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

    @app.get("/api/runs/{run_id}/artifacts/final-page")
    async def final_page_screenshot(run_id: str) -> FileResponse:
        try:
            await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        root = Path(settings.artifact_root).resolve()
        path = (root / f"{run_id}-final-page.png").resolve()
        if path.parent != root or not path.is_file():
            raise HTTPException(status_code=404, detail="final screenshot not available")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/runs/{run_id}/artifacts/{session_id}")
    async def persona_final_page_screenshot(run_id: str, session_id: str) -> FileResponse:
        """Serve one named persona screenshot without accepting a file path."""
        if not re.fullmatch(r"session-[A-Za-z0-9_-]{1,80}", session_id):
            raise HTTPException(status_code=404, detail="screenshot not available")
        try:
            await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        root = Path(settings.artifact_root).resolve()
        path = (root / f"{run_id}-{session_id}-final-page.png").resolve()
        if path.parent != root or not path.is_file():
            raise HTTPException(status_code=404, detail="persona screenshot not available")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/runs/{run_id}/metrics")
    async def run_metrics(run_id: str) -> dict[str, Any]:
        """Aggregate latency, failures, retries, tools, and trace coverage."""
        try:
            run = await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        events = await repository.list_events(run_id, limit=1000)
        tools: dict[str, int] = {}
        model_ids: set[str] = set()
        failures = retries = model_latency = 0.0
        session_usage: dict[str, tuple[int, int]] = {}
        fallback_usage: dict[str, tuple[int, int]] = {}
        action_count = 0
        trace_count = 0
        for event in events:
            payload = event.payload or {}
            if event.kind == "tool_result":
                action_count += 1
                name = str(payload.get("tool_name") or "unknown")
                tools[name] = tools.get(name, 0) + 1
                model_latency += float(payload.get("model_latency_ms") or 0)
                if payload.get("model_id"):
                    model_ids.add(str(payload["model_id"]))
                session_id = event.session_id or "unassigned"
                prior = fallback_usage.get(session_id, (0, 0))
                fallback_usage[session_id] = (prior[0] + int(payload.get("input_tokens") or 0), prior[1] + int(payload.get("output_tokens") or 0))
            if event.kind == "session_finished" and event.session_id:
                session_usage[event.session_id] = (int(payload.get("input_tokens") or 0), int(payload.get("output_tokens") or 0))
            if event.kind in {"model_failed", "session_failed", "session_blocked"} or payload.get("status") == "error":
                failures += 1
            retries += float(payload.get("retry_count") or 0)
            if payload.get("langfuse_trace_id"):
                trace_count += 1
        findings = await repository.list_findings(run_id)
        snapshot = app.state.execution.status(run_id)
        queue_time_ms = None
        execution_state = "not_started"
        if snapshot is not None:
            execution_state = snapshot.state
            if snapshot.started_at is not None:
                queue_time_ms = max(0.0, round((snapshot.started_at - run.created_at).total_seconds() * 1000, 2))
        usage = list(session_usage.values()) + [value for session, value in fallback_usage.items() if session not in session_usage]
        # Local Ollama models have no per-request provider charge. This is not
        # a claim about hardware or electricity cost, only the API/provider
        # cost visible to a client report. Hosted models remain deliberately
        # unknown until the provider or Langfuse supplies an authoritative rate.
        local_only = bool(model_ids) and all("/" not in model and not model.startswith("openai:") for model in model_ids)
        cost_usd = 0.0 if local_only else None
        cost_source = "local_provider_cost_excludes_hardware" if local_only else "provider_or_langfuse"
        return {"run_id": run_id, "event_count": len(events), "action_count": action_count, "execution_state": execution_state, "queue_time_ms": queue_time_ms, "model_latency_ms": round(model_latency, 2), "input_tokens": sum(value[0] for value in usage), "output_tokens": sum(value[1] for value in usage), "cost_usd": cost_usd, "cost_source": cost_source, "models": sorted(model_ids), "failures": int(failures), "retries": int(retries), "tool_usage": tools, "findings": len(findings), "langfuse_trace_events": trace_count}

    @app.get("/api/runs/{run_id}/client-report")
    async def client_report(run_id: str) -> dict[str, Any]:
        """A shareable, evidence-first report without raw prompt or secret data."""
        try:
            run = await repository.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        events = await repository.list_events(run_id, limit=1000)
        findings = await repository.list_findings(run_id)
        scenario = authored_scenario(run.config_snapshot)
        actions = [
            {"sequence": event.sequence, "action": event.payload.get("tool_name"), "target": event.payload.get("target_name"), "status": event.payload.get("status")}
            for event in events if event.kind == "tool_result"
        ]
        screenshots = [
            {"persona_id": event.payload.get("persona_id"), "url": event.payload.get("url")}
            for event in events
            if event.kind == "artifact_captured" and event.payload.get("kind") == "screenshot" and event.payload.get("url")
        ]
        setup_requirements = [
            str(event.payload.get("summary") or "Secure login setup is required before this journey can run.")
            for event in events if event.kind == "login_setup_required"
        ]
        expected = None if scenario is None else {scenario.invariant.kind: scenario.invariant.expected}
        return {
            "title": f"Synthetic User Lab report: {scenario.product.name if scenario else run.scenario_id}",
            "run_id": run.id,
            "status": run.status.value,
            "product": None if scenario is None else {"name": scenario.product.name, "base_url": scenario.product.base_url},
            "journey": None if scenario is None else scenario.persona.goal,
            "required_actions": [] if scenario is None else scenario.required_clicks,
            "expected": expected,
            "actions": actions,
            "findings": [finding.model_dump(mode="json") for finding in findings],
            "setup_requirements": setup_requirements,
            "evidence": {
                "final_screenshot": screenshots[0]["url"] if screenshots else None,
                "screenshots": screenshots,
                "verification_events": sum(event.kind == "verification_completed" for event in events),
            },
            "replay": "Start a fresh browser session, use the same saved test profile, and replay the required actions in their recorded order.",
            "metrics": await run_metrics(run_id),
        }

    @app.get("/reports/{run_id}", response_class=HTMLResponse)
    async def client_report_page(run_id: str) -> str:
        """Render the safe report payload as an operator-authenticated page."""
        report = await client_report(run_id)
        escape = lambda value: html.escape(str(value), quote=True)
        actions = "".join(
            f"<tr><td>{escape(item['sequence'])}</td><td>{escape(item['action'] or '—')}</td><td>{escape(item['target'] or '—')}</td><td>{escape(item['status'] or 'unknown')}</td></tr>"
            for item in report["actions"]
        ) or "<tr><td colspan='4'>No persisted browser actions.</td></tr>"
        findings = "".join(
            f"<article class='finding'><b>{escape(item['status'])}: {escape(item['invariant_id'])}</b><p>Expected: {escape(json.dumps(item['expected']))}<br>Actual: {escape(json.dumps(item['actual']))}</p><small>Verifier: {escape(item['verifier_version'])} · Replay: {escape(item['replay_status'])}</small></article>"
            for item in report["findings"]
        ) or "<p class='muted'>No confirmed findings were recorded.</p>"
        product = report["product"] or {}
        metrics = report["metrics"]
        screenshots = report["evidence"]["screenshots"]
        screenshot_html = "".join(
            f"<figure><figcaption>{escape(item.get('persona_id') or 'Browser session')}</figcaption><img src='{escape(item['url'])}' alt='Final tested page for {escape(item.get('persona_id') or 'browser session')}'></figure>"
            for item in screenshots
        ) or "<p class='muted'>No final screenshot was captured.</p>"
        cost = "Not available" if metrics["cost_usd"] is None else f"${metrics['cost_usd']:.4f}"
        cost_label = "Provider cost (local)" if metrics["cost_source"] == "local_provider_cost_excludes_hardware" else "Model cost"
        setup_html = "" if not report["setup_requirements"] else "".join(f"<p>{escape(item)}</p>" for item in report["setup_requirements"])
        setup_section = "" if not setup_html else f"<section class='card setup'><h2>Login setup required</h2>{setup_html}<p class='muted'>Add the login selectors and environment-variable names to the saved test profile. Password values are never stored in this report or dashboard.</p></section>"
        return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(report['title'])}</title><style>body{{margin:0;background:#f5f7fb;color:#182236;font:14px Inter,system-ui,sans-serif}}main{{max-width:1020px;margin:auto;padding:40px 22px}}h1{{font-size:30px;margin:7px 0}}h2{{font-size:16px}}.eyebrow{{font-size:11px;color:#5260cc;font-weight:800;letter-spacing:1.2px}}.muted{{color:#69768b;line-height:1.6}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0}}.card{{background:#fff;border:1px solid #e1e6f0;border-radius:14px;padding:20px;margin:16px 0}}.metric b{{font-size:22px}}.metric small{{display:block;color:#778399;margin-bottom:6px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px;text-align:left;border-bottom:1px solid #edf0f5}}th{{font-size:11px;color:#768299;text-transform:uppercase}}.finding{{border-left:3px solid #e85e72;background:#fff7f8;padding:12px 14px;margin:10px 0}}.setup{{border-left:4px solid #e2a33f;background:#fffaf0}}figure{{margin:0 0 18px}}figcaption{{font-weight:700;margin:0 0 8px;text-transform:capitalize}}img{{width:100%;border-radius:9px;border:1px solid #e1e6f0}}a{{color:#4c58c7}}@media(max-width:650px){{.grid{{grid-template-columns:repeat(2,1fr)}}main{{padding:25px 14px}}}}</style></head><body><main><p class='eyebrow'>SYNTHETIC USER LAB · EVIDENCE REPORT</p><h1>{escape(report['title'])}</h1><p class='muted'>Run {escape(report['run_id'])} · {escape(report['status'])}</p><section class='grid'><article class='card metric'><small>Browser actions</small><b>{escape(metrics['action_count'])}</b></article><article class='card metric'><small>Verified findings</small><b>{escape(metrics['findings'])}</b></article><article class='card metric'><small>Model time</small><b>{escape(metrics['model_latency_ms'])} ms</b></article><article class='card metric'><small>{escape(cost_label)}</small><b>{escape(cost)}</b></article></section>{setup_section}<section class='card'><h2>Journey</h2><p><b>{escape(product.get('name', 'Controlled scenario'))}</b><br><a href='{escape(product.get('base_url', '#'))}'>{escape(product.get('base_url', '—'))}</a></p><p>{escape(report['journey'] or '—')}</p><p><b>Expected:</b> {escape(json.dumps(report['expected']))}</p></section><section class='card'><h2>What the agent tried</h2><table><thead><tr><th>#</th><th>Action</th><th>Target</th><th>Result</th></tr></thead><tbody>{actions}</tbody></table></section><section class='card'><h2>Verified findings</h2>{findings}</section><section class='card'><h2>Screenshot evidence</h2>{screenshot_html}</section><section class='card'><h2>Replay instructions</h2><p class='muted'>{escape(report['replay'])}</p></section></main></body></html>"""

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
