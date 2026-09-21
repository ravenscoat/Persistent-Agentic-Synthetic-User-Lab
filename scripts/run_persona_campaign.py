"""Run the returned-persona scenario matrix with Qwen and healthy/fault controls."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import uvicorn

from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.config import Settings
from synthetic_lab.contracts import BudgetConfig, Event, MemoryRecord, MemoryType, PersonaRecord, RunRecord, SessionRecord, Trust
from synthetic_lab.demo import create_demo_app
from synthetic_lab.demo.postgres_store import PostgresDemoStore
from synthetic_lab.evaluation import PERSONA_SCENARIOS, PersonaScenario
from synthetic_lab.llm import build_local_model
from synthetic_lab.memory import with_optional_qdrant
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.runtime.scheduler import DurableScheduler
from synthetic_lab.storage import PostgresMemoryRepository, PostgresStateRepository
from synthetic_lab.verification import DemoReplayService, DemoVerificationContext, DemoVerifier
from synthetic_lab.reporting.reports import ReportBuilder


class ReturnVisitContext(MemoryContextAssembler):
    """Guide a bounded inspection using the current browser URL."""

    def __init__(self, repository, *, route, tool_registry):
        super().__init__(repository, memory_limit=1, tool_registry=tool_registry)
        self.route = route

    async def build(self, persona, session, observation, budgets):
        from urllib.parse import urlparse
        arrived = urlparse(observation.url).path == self.route
        instruction = (
            'The requested page is now visible. Read its current status and return kind="finish" with a non-empty summary. No more navigation is needed.'
            if arrived else
            f'You are already authenticated. Use tool_name="navigate" with arguments={{"url":"{self.route}"}}. Do not sign up or click controls. Only navigate is allowed.'
        )
        scoped = persona.model_copy(update={"goal": persona.goal + " " + instruction})
        return await super().build(scoped, session, observation, budgets)


async def run_case(spec: PersonaScenario, fault: str | None, settings: Settings) -> dict[str, object]:
    dsn = os.getenv("SUL_POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("SUL_POSTGRES_DSN is required")
    root, schema, run_id = Path(__file__).resolve().parents[1], f"sul_eval_{uuid4().hex}", str(uuid4())
    now, account_id = datetime.now(timezone.utc), "account-1"
    store = PostgresDemoStore(dsn, fault=fault, schema=schema)
    state = PostgresStateRepository.from_dsn(dsn, schema=schema)
    memory = with_optional_qdrant(PostgresMemoryRepository.from_dsn(dsn, schema=schema), settings)
    server = uvicorn.Server(uvicorn.Config(create_demo_app(store), host="127.0.0.1", port=8015, log_level="error"))
    task = browser = model = None
    try:
        await store.apply_migration(root / "migrations" / "003_demo_business_postgres.sql")
        await state.apply_migration(root / "migrations" / "002_initial_postgres.sql")
        await state.create_run(RunRecord(id=run_id, scenario_id=spec.scenario_id, created_at=now, business_time=now, config_snapshot={"fault": fault}, model_metadata={"mode": "qwen"}))
        store.create_account(account_id, f"{spec.persona_kind}@example.test", "not-a-real-password")
        member_id, operation_id = spec.persona_kind, None
        if spec.scenario_id == "trial_return":
            store.advance_days(6)
        elif spec.scenario_id == "payment_retry":
            operation_id = "purchase-1"; store.purchase(account_id, operation_id, 2500)
        elif spec.scenario_id == "ownership_transfer":
            member_id = "old-owner"; store.transfer_owner(account_id, "old-owner", "new-owner")
        elif spec.scenario_id == "interrupted_onboarding":
            store.set_onboarding_step(account_id, 2)
        elif spec.scenario_id == "stale_task_status":
            store.create_project(account_id, "project-1", "Payments migration")
            store.create_task("project-1", "task-1", "Verify payment retry")
            store.complete_task("task-1")
        task = asyncio.create_task(server.serve()); await asyncio.sleep(.25)
        persona = PersonaRecord(id=member_id, run_id=run_id, kind=spec.persona_kind, application_account_id=account_id, goal=spec.goal, allowed_tool_names=["navigate"])
        session = SessionRecord(id=f"{spec.persona_kind}-return", run_id=run_id, persona_id=member_id, phase="return", due_business_time=now)
        await state.enqueue_session(session)
        await memory.append(MemoryRecord(id=f"{spec.scenario_id}-return-memory", run_id=run_id, persona_id=member_id, type=MemoryType.ENTITY, text=f"Return visit for {spec.scenario_id}; inspect the current business state.", trust=Trust.VERIFIED, valid_from=now))
        browser = PlaywrightBrowserSession(run_id, session.id, "http://127.0.0.1:8015", artifact_root=root / "artifacts")
        await browser.start(); await browser.page.context.add_cookies([{"name":"account_id","value":account_id,"url":"http://127.0.0.1:8015","httpOnly":True},{"name":"member_id","value":member_id,"url":"http://127.0.0.1:8015","httpOnly":True}]); await browser.page.goto("http://127.0.0.1:8015/")
        observation, tools, model = await browser.observe(), BrowserToolRegistry({member_id: browser}), build_local_model(settings)
        result = {}
        def factory(leased):
            async def execute(current):
                agent = PersonaAgent(model=model, context=ReturnVisitContext(memory, route=spec.return_route, tool_registry=tools), tools=tools, state=state, memory=memory, budgets=BudgetConfig(max_steps=3, max_model_requests=4, output_tokens=256), completion_check=lambda: asyncio.sleep(0, result=browser.page.url.endswith(spec.return_route)))
                result["value"] = await agent.run(persona, current, observation)
            return execute
        leased = await DurableScheduler(state, factory).run_once(f"worker-{spec.persona_kind}", now=now)
        verified = await DemoVerifier().check(spec.invariant_id, DemoVerificationContext(store, account_id, operation_id))
        events = await state.list_events(run_id, limit=100)
        replay = None
        report_path = None
        if verified.verdict == "confirmed":
            from synthetic_lab.contracts import Finding, FindingStatus
            finding = Finding(id=str(uuid4()), run_id=run_id, session_id=session.id, invariant_id=spec.invariant_id, status=FindingStatus.CONFIRMED, expected=verified.expected, actual=verified.actual, evidence_ids=[e.id for e in events if e.kind == "tool_result"], verifier_version="demo-verifier-v1")
            replay = await DemoReplayService().replay(finding, fault=fault)
            finding = finding.model_copy(update={"replay_status": replay.status})
            await state.save_finding(finding)
            report_path = root / "artifacts" / f"campaign-finding-{finding.id}.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(ReportBuilder().build(finding, events, []).as_dict(), indent=2), encoding="utf-8")
        return {"run_id": run_id, "schema": schema, "finding_report": str(report_path) if report_path else None, "events": [e.model_dump(mode="json") for e in events], "scenario": spec.scenario_id, "persona": spec.persona_kind, "fault": fault, "leased": leased, "agent": result["value"].__dict__, "verdict": verified.verdict, "expected_verdict": "confirmed" if fault else "satisfied", "replay": replay.status.value if replay else None, "event_sequences_unique": len({e.sequence for e in events}) == len(events)}
    finally:
        if browser: await browser.close()
        if model and hasattr(model, "aclose"): await model.aclose()
        server.should_exit = True
        if task: await task
        store.close()


def case_passed(row) -> bool:
    return (
        row.get("leased") is True
        and row.get("agent", {}).get("status") == "completed"
        and row.get("verdict") == row.get("expected_verdict")
        and row.get("event_sequences_unique") is True
        and (not row.get("fault") or row.get("replay") == "reproduced")
    )


async def main() -> int:
    settings = Settings().model_copy(update={"model_concurrency": 1})
    rows = []
    output_path = Path(__file__).resolve().parents[1] / "artifacts" / "persona-campaign.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    def save(complete=False):
        expected_rows = len(PERSONA_SCENARIOS) * 2
        payload = {"model": "qwen", "rows": rows, "complete": complete, "passed": complete and len(rows) == expected_rows and all(case_passed(row) for row in rows)}
        output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return payload
    save()
    for spec in PERSONA_SCENARIOS:
        for fault in (None, spec.fault):
            print(f"Starting {spec.scenario_id}: {fault or 'healthy'}", file=sys.stderr, flush=True)
            try:
                row = await run_case(spec, fault, settings)
            except Exception as exc:
                row = {"scenario": spec.scenario_id, "fault": fault, "error_type": type(exc).__name__}
            rows.append(row)
            save()
            print(f"Finished {len(rows)}/{len(PERSONA_SCENARIOS) * 2}: {'passed' if case_passed(row) else 'failed'}", file=sys.stderr, flush=True)
    output = save(complete=True)
    print(json.dumps(output, indent=2, default=str))
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
