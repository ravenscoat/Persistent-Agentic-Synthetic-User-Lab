"""End-to-end recovery: API worker + PostgreSQL + real browser checkpoint."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from synthetic_lab.api import create_app
from synthetic_lab.config import Settings
from synthetic_lab.contracts import Action, AgentDecision, DecisionKind, ModelResponse, RunRecord, RunStatus
from synthetic_lab.runtime.product_executor import ProductAdapterExecutor
from synthetic_lab.storage import PostgresMemoryRepository, PostgresStateRepository


class _SignupModel:
    """A deterministic stand-in so this is an executor recovery test, not an LLM test."""

    model_name = "recovery-script"

    def __init__(self, *, block_after_first_action: bool = False) -> None:
        self.calls = 0
        self.block_after_first_action = block_after_first_action
        self.blocked = asyncio.Event()

    async def decide(self, messages, decision_schema=None, generation_options=None) -> ModelResponse:
        if self.calls and self.block_after_first_action:
            self.blocked.set()
            await asyncio.Event().wait()
        content = "\n".join(str(message.get("content", "")) for message in messages)
        if self.calls == 0:
            decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id="fill-email", tool_name="fill", arguments={"target": _target(content, "email"), "value": "recovery@example.test"}))
        elif self.calls == 1:
            decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id="fill-password", tool_name="fill", arguments={"target": _target(content, "password"), "value": "safe-test-password"}))
        else:
            decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id="submit-signup", tool_name="click", arguments={"target": _target(content, "Create account")}))
        self.calls += 1
        return ModelResponse(decision=decision, latency_ms=1, model_id="recovery-script")

    async def aclose(self) -> None:
        return None


def _target(content: str, name: str) -> str:
    match = re.search(r"'target': '([^']+)', 'role': '[^']+', 'name': '" + re.escape(name) + r"'", content)
    if not match:
        raise AssertionError(f"current browser observation did not expose {name!r}")
    return match.group(1)


async def _wait_for(predicate, *, timeout: float = 8) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("timed out waiting for executor state")
        await asyncio.sleep(0.03)


@pytest.mark.asyncio
async def test_api_restart_resumes_browser_run_from_postgres_checkpoint(tmp_path) -> None:
    """Stop one API worker after a browser action; a new worker finishes safely."""
    dsn = os.getenv("SUL_INTEGRATION_POSTGRES_DSN")
    origin = os.getenv("SUL_BROWSER_RECOVERY_URL")
    if not dsn or not origin:
        pytest.skip("set SUL_INTEGRATION_POSTGRES_DSN and SUL_BROWSER_RECOVERY_URL for the end-to-end recovery proof")

    psycopg = pytest.importorskip("psycopg")
    schema = f"sul_api_recovery_{uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    try:
        state_a = PostgresStateRepository.from_dsn(dsn, schema=schema)
        memory_a = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
        await state_a.apply_migration(Path(__file__).resolve().parents[2] / "migrations" / "002_initial_postgres.sql")
        run_id = "api-restart-proof"
        now = datetime.now(timezone.utc)
        scenario = {
            "product": {"name": "Isolated Flowboard", "base_url": origin, "start_path": "/signup"},
            "persona": {"kind": "new_customer", "goal": "Create a workspace safely."},
            "invariant": {"id": "dashboard_visible", "kind": "text_contains", "expected": "Good afternoon"},
            "required_clicks": ["Create account"],
            "max_steps": 8,
        }
        await state_a.create_run(RunRecord(id=run_id, scenario_id="custom:recovery", status=RunStatus.RUNNING, created_at=now, business_time=now, config_snapshot={"authored_scenario": scenario, "trace_enabled": False}))
        settings = Settings(artifact_root=tmp_path)
        first_model = _SignupModel(block_after_first_action=True)
        executor_a = ProductAdapterExecutor(state_a, memory_a, settings=settings, model_factory=lambda _: first_model)
        app_a = create_app(state_a, memory_a, executor=executor_a)
        session_id = f"session-{run_id[:8]}"

        async with app_a.router.lifespan_context(app_a):
            async def first_step_saved() -> bool:
                try:
                    return (await state_a.get_session(session_id)).step_count == 1
                except KeyError:
                    return False
            await _wait_for(first_step_saved)
            await _wait_for(lambda: _event_set(first_model.blocked))
            assert (tmp_path / f"{run_id}-{session_id}-state.json").is_file()
            # This is the platform-stop boundary. Cancellation leaves the run
            # and session RUNNING, exactly as an abrupt worker shutdown would.
            await app_a.state.execution.close()

        state_b = PostgresStateRepository.from_dsn(dsn, schema=schema)
        memory_b = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
        resumed_model = _SignupModel()
        executor_b = ProductAdapterExecutor(state_b, memory_b, settings=settings, model_factory=lambda _: resumed_model)
        app_b = create_app(state_b, memory_b, executor=executor_b)
        async with app_b.router.lifespan_context(app_b):
            async def terminal() -> bool:
                return (await state_b.get_run(run_id)).status in {RunStatus.COMPLETED, RunStatus.FAILED}
            await _wait_for(terminal)
            current = await state_b.get_run(run_id)
            snapshot = app_b.state.execution.status(run_id)
            assert current.status is RunStatus.COMPLETED, snapshot.error if snapshot else "recovered run did not have an execution snapshot"

        session = await state_b.get_session(session_id)
        assert session.status.value == "COMPLETED"
        events = await state_b.list_events(run_id, limit=100)
        assert [event.kind for event in events].count("session_started") == 1
        created = [event for event in events if event.kind == "tool_result" and event.payload.get("target_name") == "Create account" and event.payload.get("status") == "success"]
        assert len(created) == 1, "restart must not submit the business action twice"
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


async def _event_set(event: asyncio.Event) -> bool:
    return event.is_set()
