from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from synthetic_lab.contracts import RunRecord, RunStatus


RunExecutor = Callable[[RunRecord], Awaitable[None]]


@dataclass(slots=True)
class ExecutionSnapshot:
    run_id: str
    state: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }


class RunExecutionManager:
    """Owns one background task per run and keeps its status observable.

    The manager deliberately does not know how a persona is built. A caller
    supplies the real Qwen/Playwright executor, which keeps the HTTP layer
    independent from model and browser infrastructure.
    """

    def __init__(self, repository: Any) -> None:
        self.repository = repository
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.snapshots: dict[str, ExecutionSnapshot] = {}

    async def start(self, run: RunRecord, executor: RunExecutor) -> ExecutionSnapshot:
        existing = self.tasks.get(run.id)
        if existing and not existing.done():
            return self.snapshots[run.id]
        snapshot = ExecutionSnapshot(run_id=run.id, state="queued", started_at=datetime.now(timezone.utc))
        self.snapshots[run.id] = snapshot
        self.tasks[run.id] = asyncio.create_task(self._run(run, executor), name=f"sul-run-{run.id}")
        return snapshot

    async def _run(self, run: RunRecord, executor: RunExecutor) -> None:
        snapshot = self.snapshots[run.id]
        snapshot.state = "running"
        try:
            await executor(run)
            current = await self.repository.get_run(run.id)
            if current.status is RunStatus.RUNNING:
                await self.repository.transition_run(run.id, RunStatus.COMPLETED)
            snapshot.state = "completed"
        except asyncio.CancelledError:
            snapshot.state = "cancelled"
            raise
        except Exception as exc:  # surfaced through the API, never swallowed
            snapshot.state = "failed"
            snapshot.error = f"{type(exc).__name__}: {exc}"
            try:
                current = await self.repository.get_run(run.id)
                if current.status in {RunStatus.RUNNING, RunStatus.PAUSED}:
                    await self.repository.transition_run(run.id, RunStatus.FAILED)
            except Exception:
                pass
        finally:
            snapshot.finished_at = datetime.now(timezone.utc)

    async def cancel(self, run_id: str) -> None:
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()

    def status(self, run_id: str) -> ExecutionSnapshot | None:
        return self.snapshots.get(run_id)

    async def close(self) -> None:
        active = [task for task in self.tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
