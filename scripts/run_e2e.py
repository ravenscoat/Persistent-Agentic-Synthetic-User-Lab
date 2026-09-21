"""Run a local browser/persona smoke test against the controlled demo app."""
from __future__ import annotations

import ast
import asyncio
import json
import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn

from synthetic_lab.contracts import Action, AgentDecision, BudgetConfig, DecisionKind, Finding, FindingStatus, ModelResponse, PersonaRecord, ReplayStatus, RunRecord, SessionRecord
from synthetic_lab.config import Settings
from synthetic_lab.demo.app import create_demo_app
from synthetic_lab.demo.store import DemoStore
from synthetic_lab.demo.postgres_store import PostgresDemoStore
from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.memory import with_optional_qdrant
from synthetic_lab.memory.embeddings import OllamaEmbeddingClient
from synthetic_lab.llm import build_local_model
from synthetic_lab.reporting.reports import ReportBuilder
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.runtime.workflow import WorkflowContext, milestones, findings
from synthetic_lab.storage import PostgresMemoryRepository, PostgresStateRepository
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository, InMemoryStateRepository
from synthetic_lab.verification.invariants import DemoVerificationContext, DemoVerifier
from synthetic_lab.verification import DemoReplayService


class SmokeModel:
    """Deterministic model used to prove browser/runtime plumbing safely."""

    def __init__(self) -> None:
        self.form_step = 0

    def _element(self, content: str, name: str) -> str | None:
        start = content.find("Elements: ")
        if start < 0:
            return None
        try:
            elements = ast.literal_eval(content[start + len("Elements: "):])
        except (SyntaxError, ValueError):
            return None
        for item in elements:
            if name.casefold() in str(item.get("name", "")).casefold():
                return str(item.get("target"))
        return None

    async def decide(self, messages, decision_schema=None, generation_options=None) -> ModelResponse:
        page = str(messages[1]["content"])
        url = page.split("URL: ", 1)[1].split("\n", 1)[0]
        if url.endswith("/"):
            decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="navigate", arguments={"url": "/signup"}))
        elif "/signup" in url and "dashboard" not in url:
            email = self._element(page, "email")
            password = self._element(page, "password")
            submit = self._element(page, "create account")
            if self.form_step == 0 and email:
                self.form_step = 1
                decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="fill", arguments={"target": email, "value": "smoke@example.test"}))
            elif self.form_step == 1 and password:
                self.form_step = 2
                decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="fill", arguments={"target": password, "value": "not-a-real-password"}))
            elif self.form_step == 2 and submit:
                self.form_step = 3
                decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="click", arguments={"target": submit}))
            else:
                decision = AgentDecision(kind=DecisionKind.FINISH, summary="signup page complete")
        else:
            decision = AgentDecision(kind=DecisionKind.FINISH, summary="reached dashboard")
        return ModelResponse(decision=decision, model_id="smoke-scripted", latency_ms=0)

    def _observation_id(self, page: str) -> str:
        # The context currently includes the page fields but not observation ID;
        # the smoke runner replaces this marker before each model call.
        return page.rsplit("Observation: ", 1)[-1].split("\n", 1)[0].strip()


class WorkflowModel(SmokeModel):
    """Deterministic multi-page persona used to exercise the SaaS workflow."""

    def __init__(self) -> None:
        super().__init__()
        self.step = 0
        self.email = f"workflow-{uuid4().hex[:8]}@example.test"

    async def decide(self, messages, decision_schema=None, generation_options=None) -> ModelResponse:
        page = str(messages[1]["content"])
        url = page.split("URL: ", 1)[1].split("\n", 1)[0]
        def action(tool: str, name: str | None = None, value: str | None = None, url_value: str | None = None) -> AgentDecision:
            args = {"url": url_value} if tool == "navigate" else {"target": self._element(page, name or "")}
            if tool == "fill":
                args["value"] = value
            return AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name=tool, arguments=args))
        if url.endswith("/"):
            decision = action("navigate", url_value="/signup")
        elif "/signup" in url and "dashboard" not in url:
            email = self._element(page, "email")
            password = self._element(page, "password")
            if not email:
                decision = AgentDecision(kind=DecisionKind.FINISH, summary="signup controls unavailable")
            elif "filled': True" not in page.split("Elements:", 1)[1].split("email", 1)[-1][:100]:
                decision = action("fill", "email", self.email)
            elif password and self.step < 3:
                self.step = 3
                decision = action("fill", "password", "workflow-password")
            else:
                decision = action("click", "create account")
        elif "/dashboard" in url and self.step >= 10:
            decision = action("click", "Billing")
        elif "/dashboard" in url and self.step < 4:
            self.step = 4
            decision = action("click", "Projects")
        elif "/projects" in url:
            decision = action("fill", "name", "Payments migration",) if self.step == 4 else action("click", "Create project")
            self.step = 5
        elif "/tasks" in url and self.step <= 7:
            if self.step == 5:
                decision = action("fill", "title", "Verify payment retry")
                self.step = 6
            elif self.step == 6:
                decision = action("click", "Create task")
                self.step = 7
            else:
                decision = action("click", "Complete task-1")
                self.step = 8
        elif "/tasks" in url:
            decision = action("click", "Billing")
        elif "/billing" in url and self.step == 8:
            decision = action("click", "Start subscription")
            self.step = 9
        elif "/billing" in url and self.step == 9:
            decision = action("click", "Charge account")
            self.step = 10
        elif "/billing" in url and self.step == 10:
            decision = action("click", "Cancel subscription")
            self.step = 11
        else:
            decision = AgentDecision(kind=DecisionKind.FINISH, summary="workflow completed")
        return ModelResponse(decision=decision, model_id="workflow-scripted", latency_ms=0)


async def main(real_model: bool = False, workflow: bool = False) -> int:
    started = time.monotonic()
    settings = Settings()
    dsn = os.getenv("SUL_POSTGRES_DSN")
    schema = f"sul_eval_{uuid4().hex}" if dsn else None
    fault = os.getenv("SUL_BUSINESS_FAULT") or (None if workflow else "trial_expires_day_5")
    store = PostgresDemoStore(dsn, fault=fault, schema=schema) if dsn else DemoStore(fault=fault)
    if dsn:
        await store.apply_migration(str(Path(__file__).resolve().parents[1] / 'migrations' / '003_demo_business_postgres.sql'))
    if dsn:
        state = PostgresStateRepository.from_dsn(dsn, schema=schema)
        memory = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
        memory = with_optional_qdrant(memory, settings)
        await state.apply_migration(str(Path(__file__).resolve().parents[1] / 'migrations' / '002_initial_postgres.sql'))
    else:
        state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
    app = create_demo_app(store)
    port = int(os.environ.get("SUL_E2E_PORT", "8011"))
    origin = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.3)
    run_id, session_id, now = str(uuid4()), str(uuid4()), datetime.now(timezone.utc)
    await state.create_run(RunRecord(id=run_id, scenario_id="trial_return", created_at=now, business_time=now))
    session = SessionRecord(id=session_id, run_id=run_id, persona_id="smoke-persona", phase="signup", due_business_time=now)
    await state.enqueue_session(session)
    persona = PersonaRecord(id="smoke-persona", run_id=run_id, kind="new_customer", goal="Create an account using email smoke@example.test and password not-a-real-password. Fill empty required fields, then submit. Once the Account dashboard at /dashboard is visible, return kind=finish with a summary. Do not purchase, transfer ownership, or start another workflow.", application_account_id="account-1", allowed_tool_names=["observe_page", "navigate", "click", "fill"])
    browser = PlaywrightBrowserSession(run_id, session_id, origin, artifact_root="artifacts")
    await browser.start()
    try:
        await browser.page.goto(origin + "/")
        observation = await browser.observe()
        tools = BrowserToolRegistry({persona.id: browser})
        model = build_local_model(settings) if real_model else (WorkflowModel() if workflow else SmokeModel())
        async def signup_complete():
            return store.account_exists() and browser.page.url.endswith('/dashboard') and await browser.page.title() == 'Account dashboard'
        goal = ("Follow this exact sequence once: create an account; from the dashboard open Projects; create one project; continue to Tasks; create one task; complete task-1; open Billing; start one subscription; charge the account once; return to Billing; cancel subscription-1; then finish. Do not repeat a successful action and do not start another workflow.") if workflow else persona.goal
        persona = persona.model_copy(update={"goal": goal})
        async def workflow_complete():
            return all(milestones(store.workflow_snapshot()))
        completion_check = workflow_complete if workflow else signup_complete
        context = WorkflowContext(memory, store=store, tool_registry=tools) if workflow else MemoryContextAssembler(memory, tool_registry=tools)
        agent = PersonaAgent(model=model, context=context, tools=tools, state=state, memory=memory, budgets=BudgetConfig(max_steps=30 if workflow else 8, max_model_requests=40 if workflow else 10), completion_check=completion_check)
        result = await agent.run(persona, session, observation)
        state_path = await browser.save_state()
        store.advance_days(6)
        account_id = "account-1" if store.account_exists("account-1") else None
        signup_verified = account_id is not None and browser.page.url.endswith('/dashboard') and await browser.page.title() == 'Account dashboard'
        workflow_verified = await workflow_complete() if workflow else signup_verified
        verified = await DemoVerifier().check("trial_access_seven_days", DemoVerificationContext(store, account_id)) if account_id and not workflow else None
        events = await state.list_events(run_id, limit=1000)
        trace = [{"tool": event.payload.get("tool_name"), "target": event.payload.get("target_name"), "status": event.payload.get("status"), "url": event.payload.get("data", {}).get("url")} for event in events if event.kind == "tool_result"]
        if workflow:
            signup_verified = store.account_exists('account-1') and any(action['url'] and action['url'].endswith('/dashboard') for action in trace)
        memory_count = len(await memory.list_recent(run_id, persona.id, "tool_log", 1000))
        payload: dict[str, Any] = {"signup_verified": signup_verified, "workflow_verified": workflow_verified, "actions": trace, "agent": result.__dict__, "verification": verified.model_dump(mode="json") if verified else None, "browser_state": str(state_path), "memory_count": memory_count, "event_count": len(events)}
        Path("artifacts").mkdir(exist_ok=True)
        payload.update(database_backend='postgresql' if dsn else 'sqlite', state_backend='postgresql' if dsn else 'in_memory', database_schema=schema, model_mode='qwen' if real_model else 'scripted', business_state=store.workflow_snapshot(), milestones=milestones(store.workflow_snapshot()) if workflow else [])
        workflow_findings = findings(store.workflow_snapshot(), trace) if workflow else []
        finding_reports = []
        if workflow_findings:
            tool_events = [event for event in events if event.kind == "tool_result"]
            for item in workflow_findings:
                evidence_ids = [tool_events[index].id for index in item["action_indices"]]
                finding = Finding(
                    id=str(uuid4()), run_id=run_id, session_id=session_id,
                    invariant_id=item["invariant"], status=FindingStatus.CONFIRMED,
                    expected=item["expected"], actual=item["actual"], evidence_ids=evidence_ids,
                    verifier_version="workflow-state-check-v1", replay_status=ReplayStatus.NOT_ATTEMPTED,
                )
                replay = await DemoReplayService().replay(finding, fault=fault)
                finding = finding.model_copy(update={"replay_status": replay.status})
                await state.save_finding(finding)
                report = ReportBuilder().build(
                    finding, events, [],
                    explanation="Independent workflow-state check compared the post-action business state to the expected invariant.",
                )
                report_path = Path("artifacts") / f"finding-{finding.id}.json"
                report_path.write_text(json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8")
                finding_reports.append({"finding_id": finding.id, "report_path": str(report_path), "evidence_event_ids": evidence_ids, "replay_status": replay.status.value, "replay_reason": replay.reason})
        payload.update(duration_seconds=round(time.monotonic()-started, 2), findings=workflow_findings, finding_reports=finding_reports)
        Path(f"artifacts/e2e-{run_id}.json").write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
        Path("artifacts/e2e-report.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(json.dumps(payload, indent=2, default=str))
        return 0 if result.status == "completed" and workflow_verified else 1
    finally:
        await browser.close()
        if real_model and hasattr(model, "aclose"):
            await model.aclose()
        server.should_exit = True
        await server_task
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true", help="use the configured Ollama model instead of the deterministic smoke policy")
    parser.add_argument("--workflow", action="store_true", help="run the multi-page SaaS workflow policy")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(real_model=args.real_model, workflow=args.workflow)))
    def __init__(self) -> None:
        self.form_step = 0
