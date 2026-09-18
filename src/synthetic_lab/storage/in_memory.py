from __future__ import annotations

import asyncio
import copy
import re
from datetime import datetime, timedelta
from typing import Sequence

from synthetic_lab.contracts import (
    Event,
    Expectation,
    Finding,
    MemoryRecord,
    MemoryStatus,
    MemoryType,
    RunRecord,
    RunStatus,
    SessionRecord,
    SessionStatus,
)


class InMemoryStateRepository:
    """Concurrency-safe repository used by unit tests and local prototyping."""

    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.events: dict[str, list[Event]] = {}
        self.expectations: dict[str, Expectation] = {}
        self.findings: dict[str, Finding] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, run: RunRecord) -> RunRecord:
        async with self._lock:
            if run.id in self.runs:
                raise ValueError(f"run already exists: {run.id}")
            self.runs[run.id] = copy.deepcopy(run)
            self.events[run.id] = []
            return copy.deepcopy(run)

    async def get_run(self, run_id: str) -> RunRecord:
        async with self._lock:
            try:
                return copy.deepcopy(self.runs[run_id])
            except KeyError as exc:
                raise KeyError(f"unknown run: {run_id}") from exc

    async def transition_run(self, run_id: str, status: RunStatus | str) -> RunRecord:
        async with self._lock:
            run = self.runs[run_id]
            target = RunStatus(status)
            allowed = {
                RunStatus.CREATED: {RunStatus.RUNNING, RunStatus.CANCELLED},
                RunStatus.RUNNING: {RunStatus.PAUSED, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
                RunStatus.PAUSED: {RunStatus.RUNNING, RunStatus.CANCELLED},
            }
            if target != run.status and target not in allowed.get(run.status, set()):
                raise ValueError(f"invalid run transition: {run.status.value} -> {target.value}")
            run.status = target
            return copy.deepcopy(run)

    async def enqueue_session(self, session: SessionRecord) -> SessionRecord:
        async with self._lock:
            if session.id in self.sessions:
                raise ValueError(f"session already exists: {session.id}")
            if session.run_id not in self.runs:
                raise KeyError(f"unknown run: {session.run_id}")
            self.sessions[session.id] = copy.deepcopy(session)
            return copy.deepcopy(session)

    async def lease_ready_session(self, owner: str, now: datetime, lease_seconds: int) -> SessionRecord | None:
        async with self._lock:
            candidates = sorted(self.sessions.values(), key=lambda s: (s.due_business_time, s.id))
            for session in candidates:
                expired = session.lease_expires_at is not None and session.lease_expires_at <= now
                ready = session.status is SessionStatus.READY or (session.status in {SessionStatus.LEASED, SessionStatus.RUNNING} and expired)
                if ready and session.due_business_time <= now:
                    session.status = SessionStatus.LEASED
                    session.lease_owner = owner
                    session.lease_expires_at = now + timedelta(seconds=lease_seconds)
                    return copy.deepcopy(session)
            return None

    async def checkpoint_step(self, session_id: str, event: Event, next_session: SessionRecord) -> SessionRecord:
        async with self._lock:
            current = self.sessions[session_id]
            if event.run_id != current.run_id or next_session.id != session_id:
                raise ValueError("checkpoint scope mismatch")
            self._append_event_locked(event)
            self.sessions[session_id] = copy.deepcopy(next_session)
            return copy.deepcopy(next_session)

    async def append_event(self, event: Event) -> Event:
        async with self._lock:
            self._append_event_locked(event)
            return copy.deepcopy(event)

    def _append_event_locked(self, event: Event) -> None:
        events = self.events.setdefault(event.run_id, [])
        if any(existing.id == event.id for existing in events):
            return
        if any(existing.sequence == event.sequence for existing in events):
            raise ValueError(f"duplicate event sequence: {event.sequence}")
        events.append(copy.deepcopy(event))
        events.sort(key=lambda item: item.sequence)

    async def list_events(self, run_id: str, after_sequence: int = -1, limit: int = 100) -> list[Event]:
        if limit <= 0:
            return []
        async with self._lock:
            return copy.deepcopy([e for e in self.events.get(run_id, []) if e.sequence > after_sequence][:limit])

    async def save_expectation(self, expectation: Expectation) -> Expectation:
        async with self._lock:
            self.expectations[expectation.id] = copy.deepcopy(expectation)
            return copy.deepcopy(expectation)

    async def save_finding(self, finding: Finding) -> Finding:
        async with self._lock:
            self.findings[finding.id] = copy.deepcopy(finding)
            return copy.deepcopy(finding)


class InMemoryMemoryRepository:
    """Scoped memory repository with deterministic lexical retrieval for tests."""

    def __init__(self) -> None:
        self.records: dict[str, MemoryRecord] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _visible(record: MemoryRecord, run_id: str, persona_id: str | None) -> bool:
        return record.run_id == run_id and record.status is MemoryStatus.ACTIVE and (record.persona_id is None or record.persona_id == persona_id)

    async def append(self, record: MemoryRecord) -> MemoryRecord:
        async with self._lock:
            if record.id in self.records:
                raise ValueError(f"memory already exists: {record.id}")
            self.records[record.id] = copy.deepcopy(record)
            return copy.deepcopy(record)

    async def search(self, run_id: str, persona_id: str | None, query: str, limit: int) -> list[MemoryRecord]:
        terms = set(re.findall(r"[a-z0-9_]+", query.casefold()))
        async with self._lock:
            matches = []
            for record in self.records.values():
                if not self._visible(record, run_id, persona_id):
                    continue
                haystack = set(re.findall(r"[a-z0-9_]+", record.text.casefold()))
                score = len(terms & haystack)
                if score:
                    matches.append((score, record.valid_from, record))
            matches.sort(key=lambda item: (-item[0], -item[1].timestamp(), item[2].id))
            return [copy.deepcopy(item[2]) for item in matches[: max(0, limit)]]

    async def list_recent(self, run_id: str, persona_id: str | None, memory_type: str, limit: int) -> list[MemoryRecord]:
        async with self._lock:
            values = [r for r in self.records.values() if self._visible(r, run_id, persona_id) and r.type.value == memory_type]
            values.sort(key=lambda r: (r.valid_from, r.id), reverse=True)
            return copy.deepcopy(values[: max(0, limit)])

    async def get_by_ids(self, run_id: str, persona_id: str | None, ids: Sequence[str]) -> list[MemoryRecord]:
        async with self._lock:
            return copy.deepcopy([self.records[item] for item in ids if item in self.records and self._visible(self.records[item], run_id, persona_id)])

    async def supersede(self, run_id: str, persona_id: str | None, old_id: str, new_record: MemoryRecord) -> MemoryRecord:
        async with self._lock:
            old = self.records.get(old_id)
            if old is None or not self._visible(old, run_id, persona_id):
                raise KeyError(old_id)
            old.status = MemoryStatus.SUPERSEDED
            if new_record.supersedes_id != old_id:
                raise ValueError("new record must reference superseded record")
            self.records[new_record.id] = copy.deepcopy(new_record)
            return copy.deepcopy(new_record)
