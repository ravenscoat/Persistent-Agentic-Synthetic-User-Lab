"""Prove that a browser persona can resume after a worker crash."""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn

from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.config import Settings
from synthetic_lab.contracts import Action, AgentDecision, BudgetConfig, DecisionKind, ModelResponse, PersonaRecord, RunRecord, SessionRecord
from synthetic_lab.demo.app import create_demo_app
from synthetic_lab.demo.postgres_store import PostgresDemoStore
from synthetic_lab.llm import build_local_model
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.runtime.workflow import WorkflowContext, milestones
from synthetic_lab.storage import PostgresMemoryRepository, PostgresStateRepository


class RecoveryWorkflowModel:
    """Deterministic local stand-in when --real-model is omitted."""

    @staticmethod
    def _elements(content: str) -> list[dict[str, Any]]:
        try:
            return ast.literal_eval(content.split("Elements: ", 1)[1])
        except (IndexError, SyntaxError, ValueError):
            return []

    @classmethod
    def _target(cls, content: str, name: str) -> str | None:
        return next((str(item["target"]) for item in cls._elements(content) if name.casefold() in str(item.get("name", "")).casefold()), None)

    @classmethod
    def _filled(cls, content: str, name: str) -> bool:
        return bool(next((item.get("filled") for item in cls._elements(content) if name.casefold() in str(item.get("name", "")).casefold()), False))

    async def decide(self, messages, decision_schema=None, generation_options=None) -> ModelResponse:
        content = str(messages[1]["content"])
        url = content.split("URL: ", 1)[1].split("\n", 1)[0]
        match = re.search(r"Current milestone (\d+)", content)
        milestone = int(match.group(1)) if match else 1

        def action(tool: str, name: str | None = None, value: str | None = None, url_value: str | None = None) -> AgentDecision:
            arguments = {"url": url_value} if tool == "navigate" else {"target": self._target(content, name or "")}
            if tool == "fill":
                arguments["value"] = value
            return AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name=tool, arguments=arguments))

        if milestone == 1:
            if url.endswith("/"):
                decision = action("navigate", url_value="/signup")
            elif not self._filled(content, "email"):
                decision = action("fill", "email", "workflow@example.test")
            elif not self._filled(content, "password"):
                decision = action("fill", "password", "workflow-password")
            else:
                decision = action("click", "create account")
        elif milestone == 2:
            decision = action("click", "projects") if "/dashboard" in url else action("click", "create project")
        elif milestone == 3:
            if "/dashboard" in url:
                decision = action("click", "tasks")
            elif self._target(content, "title") and not self._filled(content, "title"):
                decision = action("fill", "title", "Verify payment retry")
            elif self._target(content, "create task"):
                decision = action("click", "create task")
            else:
                decision = action("click", "complete task-1")
        elif milestone == 4:
            decision = action("click", "billing") if "/billing" not in url else action("click", "start subscription")
        elif milestone == 5:
            decision = action("click", "billing") if "/billing" not in url else action("click", "charge account")
        elif milestone == 6:
            decision = action("click", "billing") if "/billing" not in url else action("click", "cancel subscription")
        else:
            decision = AgentDecision(kind=DecisionKind.FINISH, summary="All database milestones are complete.")
        return ModelResponse(decision=decision, model_id="recovery-scripted", latency_ms=0)


def last_observed_url(events: list[Any]) -> str:
    for event in reversed(events):
        data = event.payload.get("data", {})
        if event.kind == "tool_result" and isinstance(data, dict) and isinstance(data.get("url"), str):
            return data["url"]
    raise RuntimeError("no durable browser observation was recorded before interruption")


async def complete(store: PostgresDemoStore) -> bool:
    return all(milestones(store.workflow_snapshot()))


async def main(real_model: bool = False) -> int:
    dsn = os.getenv("SUL_POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("SUL_POSTGRES_DSN is required: recovery must use durable PostgreSQL state")
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    # Reuse the guarded evaluation-schema convention accepted by the store.
    schema = f"sul_eval_{uuid4().hex}"
    store = PostgresDemoStore(dsn, schema=schema)
    await store.apply_migration(str(root / "migrations" / "003_demo_business_postgres.sql"))
    state = PostgresStateRepository.from_dsn(dsn, schema=schema)
    memory = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
    await state.apply_migration(root / "migrations" / "002_initial_postgres.sql")
    server = uvicorn.Server(uvicorn.Config(create_demo_app(store), host="127.0.0.1", port=8012, log_level="error"))
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.3)
    run_id, session_id, now = str(uuid4()), str(uuid4()), datetime.now(timezone.utc)
    persona = PersonaRecord(id="recovery-persona", run_id=run_id, kind="new_customer", goal="Complete the controlled subscription workflow.", application_account_id="account-1", allowed_tool_names=["observe_page", "navigate", "click", "fill"])
    await state.create_run(RunRecord(id=run_id, scenario_id="workflow_resume", created_at=now, business_time=now))
    await state.enqueue_session(SessionRecord(id=session_id, run_id=run_id, persona_id=persona.id, phase="workflow", due_business_time=now))
    first_browser = resumed_browser = None
    model: Any = None
    try:
        leased = await state.lease_ready_session("worker-before-crash", now, lease_seconds=1)
        assert leased is not None
        first_browser = PlaywrightBrowserSession(run_id, session_id, "http://127.0.0.1:8012", artifact_root=root / "artifacts")
        await first_browser.start()
        await first_browser.page.goto("http://127.0.0.1:8012/")
        observation = await first_browser.observe()
        model = build_local_model(Settings()) if real_model else RecoveryWorkflowModel()
        first_tools = BrowserToolRegistry({persona.id: first_browser})
        first_agent = PersonaAgent(model=model, context=WorkflowContext(memory, store=store, tool_registry=first_tools), tools=first_tools, state=state, memory=memory, budgets=BudgetConfig(max_steps=30, max_model_requests=40), completion_check=lambda: complete(store))
        interrupted = await first_agent.run(persona, leased, observation, stop_after_steps=6)
        if interrupted.status != "interrupted":
            raise RuntimeError(f"first worker did not reach the planned crash boundary: {interrupted}")
        state_path = await first_browser.save_state()
        await first_browser.close()
        first_browser = None

        # A fresh worker has no Python object from the first one.
        state = PostgresStateRepository.from_dsn(dsn, schema=schema)
        memory = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
        resume_url = last_observed_url(await state.list_events(run_id, limit=1000))
        reclaimed = await state.lease_ready_session("worker-after-crash", now + timedelta(seconds=2), lease_seconds=30)
        if reclaimed is None or reclaimed.id != session_id or reclaimed.step_count != interrupted.steps:
            raise RuntimeError("the restarted worker could not reclaim the interrupted session")
        resumed_browser = PlaywrightBrowserSession(run_id, session_id, "http://127.0.0.1:8012", artifact_root=root / "artifacts", storage_state_path=state_path)
        await resumed_browser.start()
        await resumed_browser.page.goto(resume_url)
        resumed_observation = await resumed_browser.observe()
        resumed_tools = BrowserToolRegistry({persona.id: resumed_browser})
        resumed_model = build_local_model(Settings()) if real_model else RecoveryWorkflowModel()
        resumed_agent = PersonaAgent(model=resumed_model, context=WorkflowContext(memory, store=store, tool_registry=resumed_tools), tools=resumed_tools, state=state, memory=memory, budgets=BudgetConfig(max_steps=30, max_model_requests=40), completion_check=lambda: complete(store))
        result = await resumed_agent.run(persona, reclaimed, resumed_observation)
        events = await state.list_events(run_id, limit=1000)
        payload = {"schema": schema, "model_mode": "qwen" if real_model else "scripted", "interrupted": interrupted.__dict__, "resumed": result.__dict__, "reclaimed_step_count": reclaimed.step_count, "restored_url": resume_url, "browser_state": str(state_path), "event_count": len(events), "memory_count": len(await memory.list_recent(run_id, persona.id, "tool_log", 1000)), "business_state": store.workflow_snapshot(), "milestones": milestones(store.workflow_snapshot()), "duration_seconds": round(time.monotonic() - started, 2)}
        report = root / "artifacts" / f"browser-resume-{run_id}.json"
        report.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(json.dumps(payload, indent=2, default=str))
        return 0 if result.status == "completed" and all(payload["milestones"]) else 1
    finally:
        if first_browser is not None:
            await first_browser.close()
        if resumed_browser is not None:
            await resumed_browser.close()
        if real_model and model is not None and hasattr(model, "aclose"):
            await model.aclose()
        server.should_exit = True
        await server_task
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true", help="use local Qwen via Ollama")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(real_model=args.real_model)))
