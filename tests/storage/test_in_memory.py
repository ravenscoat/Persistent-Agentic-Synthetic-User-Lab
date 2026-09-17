from datetime import datetime, timedelta, timezone

import pytest

from synthetic_lab.contracts import Event, MemoryRecord, MemoryType, RunRecord, SessionRecord, Trust
from synthetic_lab.storage import InMemoryMemoryRepository, InMemoryStateRepository


def run() -> RunRecord:
    now = datetime.now(timezone.utc)
    return RunRecord(id="r1", scenario_id="trial_return", created_at=now, business_time=now)


@pytest.mark.asyncio
async def test_lease_and_checkpoint_are_idempotency_safe() -> None:
    state = InMemoryStateRepository()
    current = run()
    await state.create_run(current)
    now = current.business_time
    session = SessionRecord(id="s1", run_id="r1", persona_id="p1", phase="start", due_business_time=now)
    await state.enqueue_session(session)
    leased = await state.lease_ready_session("worker-a", now, 30)
    assert leased is not None and leased.lease_owner == "worker-a"
    event = Event(id="e1", run_id="r1", session_id="s1", sequence=0, kind="step", wall_time=now, business_time=now)
    next_session = leased.model_copy(update={"status": "COMPLETED", "step_count": 1})
    await state.checkpoint_step("s1", event, next_session)
    assert await state.lease_ready_session("worker-b", now + timedelta(seconds=60), 30) is None
    assert len(await state.list_events("r1")) == 1


@pytest.mark.asyncio
async def test_memory_scope_and_supersession() -> None:
    repo = InMemoryMemoryRepository()
    now = datetime.now(timezone.utc)
    await repo.append(MemoryRecord(id="shared", run_id="r1", type=MemoryType.SEMANTIC, text="trial lasts seven days", trust=Trust.VERIFIED, valid_from=now))
    await repo.append(MemoryRecord(id="private", run_id="r1", persona_id="p1", type=MemoryType.ENTITY, text="account alpha", trust=Trust.OBSERVED, valid_from=now))
    assert [item.id for item in await repo.search("r1", "p2", "account alpha", 10)] == []
    assert [item.id for item in await repo.search("r1", "p2", "trial seven days", 10)] == ["shared"]
    assert await repo.get_by_ids("r1", "p2", ["private"]) == []
    replacement = MemoryRecord(id="new", run_id="r1", persona_id="p1", type=MemoryType.ENTITY, text="account beta", trust=Trust.OBSERVED, valid_from=now, supersedes_id="private")
    await repo.supersede("r1", "p1", "private", replacement)
    assert [item.id for item in await repo.get_by_ids("r1", "p1", ["private", "new"])] == ["new"]
