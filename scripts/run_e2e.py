"""Run a local browser/persona smoke test against the controlled demo app."""
from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn

from synthetic_lab.contracts import Action, AgentDecision, BudgetConfig, DecisionKind, ModelResponse, PersonaRecord, RunRecord, SessionRecord
from synthetic_lab.demo.app import create_demo_app
from synthetic_lab.demo.store import DemoStore
from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.memory.embeddings import OllamaEmbeddingClient
from synthetic_lab.reporting.reports import ReportBuilder
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository, InMemoryStateRepository
from synthetic_lab.verification.invariants import DemoVerificationContext, DemoVerifier


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
            if name in str(item.get("name", "")).casefold():
                return str(item["id"])
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
                decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="fill", arguments={"element_id": email, "value": "smoke@example.test"}, observation_id=self._observation_id(page)))
            elif self.form_step == 1 and password:
                self.form_step = 2
                decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="fill", arguments={"element_id": password, "value": "not-a-real-password"}, observation_id=self._observation_id(page)))
            elif self.form_step == 2 and submit:
                self.form_step = 3
                decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="click", arguments={"element_id": submit}, observation_id=self._observation_id(page)))
            else:
                decision = AgentDecision(kind=DecisionKind.FINISH, summary="signup page complete")
        else:
            decision = AgentDecision(kind=DecisionKind.FINISH, summary="reached dashboard")
        return ModelResponse(decision=decision, model_id="smoke-scripted", latency_ms=0)

    def _observation_id(self, page: str) -> str:
        # The context currently includes the page fields but not observation ID;
        # the smoke runner replaces this marker before each model call.
        return page.rsplit("Observation: ", 1)[-1].split("\n", 1)[0].strip()


async def main() -> int:
    store = DemoStore(fault="trial_expires_day_5")
    app = create_demo_app(store)
    config = uvicorn.Config(app, host="127.0.0.1", port=8011, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.3)
    run_id, session_id, now = str(uuid4()), str(uuid4()), datetime.now(timezone.utc)
    state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
    await state.create_run(RunRecord(id=run_id, scenario_id="trial_return", created_at=now, business_time=now))
    session = SessionRecord(id=session_id, run_id=run_id, persona_id="smoke-persona", phase="signup", due_business_time=now)
    await state.enqueue_session(session)
    persona = PersonaRecord(id="smoke-persona", run_id=run_id, kind="new_customer", goal="Create an account and reach the dashboard.", application_account_id="account-1", allowed_tool_names=["observe_page", "navigate", "click", "fill"])
    browser = PlaywrightBrowserSession(run_id, session_id, "http://127.0.0.1:8011", artifact_root="artifacts")
    await browser.start()
    try:
        await browser.page.goto("http://127.0.0.1:8011/")
        observation = await browser.observe()
        tools = BrowserToolRegistry({persona.id: browser})
        agent = PersonaAgent(model=SmokeModel(), context=MemoryContextAssembler(memory), tools=tools, state=state, memory=memory, budgets=BudgetConfig(max_steps=8, max_model_requests=8))
        result = await agent.run(persona, session, observation)
        state_path = await browser.save_state()
        store.advance_days(6)
        account_row = store.connection.execute("SELECT id FROM accounts ORDER BY id LIMIT 1").fetchone()
        if account_row is None:
            raise RuntimeError(f"persona did not create an account; agent result={result}; events={[event.payload for event in state.events[run_id]]}")
        verified = await DemoVerifier().check("trial_access_seven_days", DemoVerificationContext(store, str(account_row[0])))
        payload: dict[str, Any] = {"agent": result.__dict__, "verification": verified.model_dump(mode="json"), "browser_state": str(state_path), "memory_count": len(memory.records), "event_count": len(state.events[run_id])}
        Path("artifacts").mkdir(exist_ok=True)
        Path("artifacts/e2e-report.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(json.dumps(payload, indent=2, default=str))
        return 0 if result.status == "completed" and verified.verdict == "confirmed" else 1
    finally:
        await browser.close()
        server.should_exit = True
        await server_task
        store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
    def __init__(self) -> None:
        self.form_step = 0
