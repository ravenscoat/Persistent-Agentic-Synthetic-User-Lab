"""Real PostgreSQL proof that an agent can resume after a durable checkpoint.

This test is deliberately opt-in. It needs an explicitly supplied database
URL because unit tests must never accidentally write to a developer's local
PostgreSQL instance. It creates and removes one random ``sul_recovery_*``
schema, never touching the application's normal schema.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from synthetic_lab.contracts import (
    Action,
    ActionStatus,
    AgentDecision,
    BudgetConfig,
    DecisionKind,
    ModelResponse,
    Observation,
    PersonaRecord,
    RunRecord,
    SessionRecord,
    SessionStatus,
    ToolResult,
)
from synthetic_lab.memory import MemoryContextAssembler
from synthetic_lab.runtime import PersonaAgent
from synthetic_lab.storage import PostgresMemoryRepository, PostgresStateRepository


class _ScriptedModel:
    def __init__(self, decisions: list[AgentDecision]) -> None:
        self._decisions = iter(decisions)

    async def decide(self, messages, decision_schema=None, generation_options=None) -> ModelResponse:
        return ModelResponse(decision=next(self._decisions), latency_ms=1, model_id="restart-recovery-test")


class _SafeTool:
    async def dispatch(self, persona, action) -> ToolResult:
        return ToolResult(
            action_id=action.id,
            status=ActionStatus.SUCCESS,
            data={"ok": True},
            observed_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_postgres_checkpoint_survives_new_repository_instances() -> None:
    """Simulate process A stopping after step 1 and process B finishing it."""
    dsn = os.getenv("SUL_INTEGRATION_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set SUL_INTEGRATION_POSTGRES_DSN to run the real PostgreSQL recovery proof")

    psycopg = pytest.importorskip("psycopg")
    schema = f"sul_recovery_{uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    try:
        state_a = PostgresStateRepository.from_dsn(dsn, schema=schema)
        memory_a = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
        migration = Path(__file__).resolve().parents[2] / "migrations" / "002_initial_postgres.sql"
        await state_a.apply_migration(migration)

        now = datetime.now(timezone.utc)
        run_id, session_id, persona_id = "restart-run", "restart-session", "restart-persona"
        await state_a.create_run(RunRecord(id=run_id, scenario_id="restart-proof", created_at=now, business_time=now))
        initial = SessionRecord(id=session_id, run_id=run_id, persona_id=persona_id, phase="signup", due_business_time=now)
        await state_a.enqueue_session(initial)
        persona = PersonaRecord(id=persona_id, run_id=run_id, kind="customer", goal="Complete a safe test journey.", application_account_id="test-account", allowed_tool_names=["observe_page"])
        observation = Observation(id="restart-observation", run_id=run_id, session_id=session_id, url="http://controlled.test/", captured_at=now)

        first = PersonaAgent(
            model=_ScriptedModel([AgentDecision(kind=DecisionKind.ACTION, action=Action(id="step-one", tool_name="observe_page"))]),
            context=MemoryContextAssembler(memory_a), tools=_SafeTool(), state=state_a, memory=memory_a,
            budgets=BudgetConfig(max_steps=4),
        )
        interrupted = await first.run(persona, initial, observation, stop_after_steps=1)
        assert interrupted.status == "interrupted"

        # New repository objects represent a newly started API/worker process.
        state_b = PostgresStateRepository.from_dsn(dsn, schema=schema)
        memory_b = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
        durable_session = await state_b.get_session(session_id)
        assert durable_session.status is SessionStatus.RUNNING
        assert durable_session.step_count == 1
        assert len(await memory_b.list_recent(run_id, persona_id, "tool_log", 10)) == 1

        resumed = PersonaAgent(
            model=_ScriptedModel([AgentDecision(kind=DecisionKind.FINISH, summary="Recovered from the durable checkpoint.")]),
            context=MemoryContextAssembler(memory_b), tools=_SafeTool(), state=state_b, memory=memory_b,
            budgets=BudgetConfig(max_steps=4),
        )
        result = await resumed.run(persona, durable_session, observation)
        assert result.status == "completed"
        assert (await state_b.get_session(session_id)).status is SessionStatus.COMPLETED
        events = await state_b.list_events(run_id, limit=20)
        assert [event.sequence for event in events] == [0, 1, 2]
        assert [event.kind for event in events].count("session_started") == 1
        assert events[-1].kind == "session_finished"
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
