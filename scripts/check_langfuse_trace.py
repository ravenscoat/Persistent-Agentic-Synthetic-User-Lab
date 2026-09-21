"""Emit one safe deterministic browser trace and print only its trace ID.

Use this after configuring SUL_LANGFUSE_* values. The scenario uses an
in-memory controlled demo and a deterministic route model, so it makes no LLM
provider request and sends no browser text, credentials, or page payloads to
Langfuse.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from synthetic_lab.config import Settings
from synthetic_lab.contracts import RunRecord
from synthetic_lab.runtime.scenario_executor import ScenarioExecutor
from synthetic_lab.storage import InMemoryMemoryRepository, InMemoryStateRepository


async def main() -> int:
    settings = Settings().model_copy(update={"postgres_dsn": None})
    if not settings.langfuse_enabled:
        raise SystemExit("Set SUL_LANGFUSE_HOST, SUL_LANGFUSE_PUBLIC_KEY, and SUL_LANGFUSE_SECRET_KEY first.")
    state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
    now = datetime.now(timezone.utc)
    run = RunRecord(
        id="langfuse-trace-proof",
        scenario_id="payment_retry",
        created_at=now,
        business_time=now,
        config_snapshot={"fault": None, "trace_enabled": True},
    )
    await state.create_run(run)
    await ScenarioExecutor(state, memory, settings=settings)(run)
    events = await state.list_events(run.id, limit=100)
    trace_ids = sorted({str(event.payload["langfuse_trace_id"]) for event in events if event.payload.get("langfuse_trace_id")})
    print(json.dumps({"run_id": run.id, "event_count": len(events), "trace_ids": trace_ids}, indent=2))
    return 0 if trace_ids else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
