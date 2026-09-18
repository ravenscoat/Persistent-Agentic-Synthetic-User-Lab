"""Prove an expired PostgreSQL agent session and its memories survive a restart."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from synthetic_lab.contracts import Event, MemoryRecord, MemoryType, RunRecord, SessionRecord, SessionStatus, Trust
from synthetic_lab.demo.postgres_store import PostgresDemoStore
from synthetic_lab.storage import PostgresMemoryRepository, PostgresStateRepository


async def main() -> int:
    dsn = os.environ["SUL_POSTGRES_DSN"]
    schema = f"sul_eval_{uuid4().hex}"
    demo = PostgresDemoStore(dsn, schema=schema)
    root = Path(__file__).resolve().parents[1]
    await demo.apply_migration(str(root / "migrations" / "002_initial_postgres.sql"))
    now = datetime.now(timezone.utc)
    run_id, session_id = str(uuid4()), str(uuid4())
    first_state = PostgresStateRepository.from_dsn(dsn, schema=schema)
    first_memory = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
    await first_state.create_run(RunRecord(id=run_id, scenario_id="restart_recovery", created_at=now, business_time=now))
    session = SessionRecord(id=session_id, run_id=run_id, persona_id="p1", phase="task_completion", due_business_time=now)
    await first_state.enqueue_session(session)
    leased = await first_state.lease_ready_session("worker-before-crash", now, lease_seconds=1)
    assert leased is not None
    interrupted = leased.model_copy(update={"status": SessionStatus.RUNNING, "step_count": 3})
    event = Event(id=str(uuid4()), run_id=run_id, persona_id="p1", session_id=session_id, sequence=0, kind="tool_result", wall_time=now, business_time=now, payload={"tool_name": "create_task", "status": "success"})
    await first_state.checkpoint_step(session_id, event, interrupted)
    memory = MemoryRecord(id=str(uuid4()), run_id=run_id, persona_id="p1", type=MemoryType.WORKFLOW, text="Project created; task creation succeeded; next milestone is task completion.", trust=Trust.OBSERVED, valid_from=now)
    await first_memory.append(memory)

    # New repository objects model a process restart. The expired lease can be reclaimed.
    restarted_state = PostgresStateRepository.from_dsn(dsn, schema=schema)
    restarted_memory = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
    reclaimed = await restarted_state.lease_ready_session("worker-after-restart", now + timedelta(seconds=2), lease_seconds=30)
    events = await restarted_state.list_events(run_id)
    memories = await restarted_memory.list_recent(run_id, "p1", MemoryType.WORKFLOW.value, 5)
    payload = {
        "schema": schema,
        "reclaimed_session": reclaimed.id if reclaimed else None,
        "resumed_step_count": reclaimed.step_count if reclaimed else None,
        "event_count": len(events),
        "memory": memories[0].text if memories else None,
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/restart-recovery.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if reclaimed and reclaimed.step_count == 3 and len(events) == 1 and memories else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
