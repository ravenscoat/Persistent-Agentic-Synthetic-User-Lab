from datetime import datetime, timezone

import pytest

from synthetic_lab.contracts import RunRecord, SessionRecord
from synthetic_lab.runtime.scheduler import DurableScheduler
from synthetic_lab.storage import InMemoryStateRepository


@pytest.mark.asyncio
async def test_scheduler_leases_and_runs_one_session() -> None:
    now = datetime.now(timezone.utc)
    state = InMemoryStateRepository()
    await state.create_run(RunRecord(id="r1", scenario_id="demo", created_at=now, business_time=now))
    await state.enqueue_session(SessionRecord(id="s1", run_id="r1", persona_id="p1", phase="start", due_business_time=now))
    called: list[str] = []

    def factory(session):
        async def execute(leased):
            called.append(leased.id)
        return execute

    scheduler = DurableScheduler(state, factory)
    assert await scheduler.run_once("worker", now=now)
    assert called == ["s1"]
    assert not await scheduler.run_once("worker", now=now)
