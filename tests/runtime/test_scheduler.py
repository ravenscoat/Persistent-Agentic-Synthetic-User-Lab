from datetime import datetime, timedelta, timezone

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


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_after_worker_restart() -> None:
    now = datetime.now(timezone.utc)
    state = InMemoryStateRepository()
    await state.create_run(RunRecord(id="r-restart", scenario_id="demo", created_at=now, business_time=now))
    await state.enqueue_session(SessionRecord(id="s-restart", run_id="r-restart", persona_id="p1", phase="start", due_business_time=now))
    first = await state.lease_ready_session("worker-a", now, lease_seconds=10)
    assert first is not None and first.lease_owner == "worker-a"
    assert await state.lease_ready_session("worker-b", now, lease_seconds=10) is None
    recovered = await state.lease_ready_session("worker-b", now + timedelta(seconds=11), lease_seconds=10)
    assert recovered is not None
    assert recovered.id == "s-restart"
    assert recovered.lease_owner == "worker-b"
