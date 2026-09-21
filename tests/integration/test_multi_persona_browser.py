"""Opt-in real-browser proof for isolated owner and teammate personas."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from synthetic_lab.config import Settings
from synthetic_lab.contracts import RunRecord
from synthetic_lab.runtime.scenario_executor import ScenarioExecutor
from synthetic_lab.storage import InMemoryMemoryRepository, InMemoryStateRepository


@pytest.mark.asyncio
async def test_two_browser_personas_confirm_only_the_former_owner_permission_leak(tmp_path) -> None:
    if os.getenv("SUL_RUN_BROWSER_INTEGRATION") != "1":
        pytest.skip("set SUL_RUN_BROWSER_INTEGRATION=1 to run real multi-persona browser proof")
    state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
    now = datetime.now(timezone.utc)
    run = RunRecord(
        id="owner-leak-browser-proof",
        scenario_id="ownership_transfer",
        created_at=now,
        business_time=now,
        config_snapshot={"fault": "owner_transfer_leak", "trace_enabled": False},
    )
    await state.create_run(run)
    executor = ScenarioExecutor(state, memory, settings=Settings(artifact_root=tmp_path, postgres_dsn=None))
    await executor(run)

    old_session = await state.get_session(f"old-owner-{run.id}")
    new_session = await state.get_session(f"new-owner-{run.id}")
    assert old_session.status.value == new_session.status.value == "COMPLETED"
    events = await state.list_events(run.id, limit=100)
    assert {event.persona_id for event in events if event.persona_id} == {"old-owner", "new-owner"}
    findings = await state.list_findings(run.id)
    assert len(findings) == 1
    assert findings[0].session_id == old_session.id
    assert findings[0].expected == "member"
    assert findings[0].actual == "owner"
    old_memory = await memory.list_recent(run.id, "old-owner", "entity", 10)
    new_memory = await memory.list_recent(run.id, "new-owner", "entity", 10)
    assert all("new-owner" not in record.id for record in old_memory)
    assert all("old-owner" not in record.id for record in new_memory)
