from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


class DurableScheduler:
    """Small lease-based scheduler; execution state remains in the repository."""

    def __init__(self, state: Any, agent_factory: Callable[[Any], Any], *, lease_seconds: int = 120) -> None:
        self.state = state
        self.agent_factory = agent_factory
        self.lease_seconds = lease_seconds

    async def run_once(self, owner: str, *, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        session = await self.state.lease_ready_session(owner, current_time, self.lease_seconds)
        if session is None:
            return False
        agent = self.agent_factory(session)
        await agent(session)
        return True

    async def pause_run(self, run_id: str) -> Any:
        return await self.state.transition_run(run_id, "PAUSED")

    async def resume_run(self, run_id: str) -> Any:
        return await self.state.transition_run(run_id, "RUNNING")

    async def cancel_run(self, run_id: str) -> Any:
        return await self.state.transition_run(run_id, "CANCELLED")
