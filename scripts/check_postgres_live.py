"""Exercise the live FastAPI repository path against PostgreSQL.

Run with SUL_POSTGRES_DSN configured. The script creates a run, persists a
session/event/memory/finding through the API's repositories, closes that app,
then creates a second app instance to prove the records survive a restart.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from synthetic_lab.api import create_app
from synthetic_lab.contracts import Event, Finding, FindingStatus, MemoryRecord, MemoryType, SessionRecord, Trust


def main() -> int:
    if not os.environ.get("SUL_POSTGRES_DSN"):
        raise SystemExit("SUL_POSTGRES_DSN must point to a running PostgreSQL instance")
    holder: dict[str, object] = {}

    async def executor(run) -> None:
        app = holder["app"]
        state = app.state.repository
        memory = app.state.memory
        now = datetime.now(timezone.utc)
        session = SessionRecord(id=f"session-{run.id}", run_id=run.id, persona_id="postgres-check", phase="persistence", due_business_time=now)
        await state.enqueue_session(session)
        event = Event(id=str(uuid4()), run_id=run.id, persona_id="postgres-check", session_id=session.id, sequence=await state.reserve_event_sequences(run.id), kind="postgres_persistence_check", wall_time=now, business_time=now, payload={"status": "success"})
        await state.append_event(event)
        await memory.append(MemoryRecord(id=str(uuid4()), run_id=run.id, persona_id="postgres-check", type=MemoryType.WORKFLOW, text="PostgreSQL persistence check completed.", trust=Trust.OBSERVED, valid_from=now, source_event_ids=[event.id]))
        await state.save_finding(Finding(id=str(uuid4()), run_id=run.id, session_id=session.id, invariant_id="postgres_persistence", status=FindingStatus.SUSPICION, expected=True, actual=True, evidence_ids=[event.id], verifier_version="postgres-smoke"))

    with TestClient(create_app(executor=executor)) as first:
        holder["app"] = first.app
        created = first.post("/api/runs", json={"scenario_id": "postgres_persistence"}).json()
        run_id = created["id"]
        assert first.post(f"/api/runs/{run_id}/start").status_code == 200
        for _ in range(50):
            if first.get(f"/api/runs/{run_id}").json()["status"] == "COMPLETED":
                break
            time.sleep(0.05)

    with TestClient(create_app(executor=executor)) as restarted:
        assert restarted.get(f"/api/runs/{run_id}").status_code == 200
        events = restarted.get(f"/api/runs/{run_id}/events").json()
        findings = restarted.get(f"/api/runs/{run_id}/findings").json()
        assert any(event["kind"] == "postgres_persistence_check" for event in events)
        assert findings and findings[0]["invariant_id"] == "postgres_persistence"
        print({"run_id": run_id, "events": len(events), "findings": len(findings), "restart": "verified"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
