"""Optional PostgreSQL persistence for durable agent memories.

The dependency is imported lazily so the core test suite remains usable without
PostgreSQL installed. Semantic vector retrieval is intentionally delegated to
Qdrant; this adapter provides the authoritative memory record store.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from synthetic_lab.contracts import (
    Event, Expectation, Finding, MemoryRecord, MemoryStatus, MemoryType,
    RunRecord, RunStatus, SessionRecord, SessionStatus, Trust,
)


class PostgresRepositoryError(RuntimeError):
    """A PostgreSQL operation failed or the optional driver is unavailable."""


class PostgresStateRepository:
    """Durable run/session/event repository with transactionally saved checkpoints."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str, *, schema: str | None = None) -> "PostgresStateRepository":
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise PostgresRepositoryError("install synthetic-user-lab[postgres] first") from exc
        options = {"options": f"-c search_path={schema}"} if schema else {}
        return cls(lambda: psycopg.connect(dsn, **options))

    async def apply_migration(self, migration_path: str | Path) -> None:
        sql = Path(migration_path).read_text(encoding="utf-8")
        statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
        def apply() -> None:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    for statement in statements:
                        cursor.execute(statement)
                connection.commit()
        try:
            await asyncio.to_thread(apply)
        except Exception as exc:
            raise PostgresRepositoryError("PostgreSQL state migration failed") from exc

    async def create_run(self, run: RunRecord) -> RunRecord:
        await self._write(
            "INSERT INTO sul_runs(id,scenario_id,status,created_at,business_time,config_snapshot,model_metadata) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
            (run.id, run.scenario_id, run.status.value, run.created_at, run.business_time, json.dumps(run.config_snapshot), json.dumps(run.model_metadata)),
        )
        return run

    async def get_run(self, run_id: str) -> RunRecord:
        rows = await self._read("SELECT id,scenario_id,status,created_at,business_time,config_snapshot,model_metadata FROM sul_runs WHERE id=%s", (run_id,))
        if not rows:
            raise KeyError(f"unknown run: {run_id}")
        row = rows[0]
        return RunRecord(id=row[0], scenario_id=row[1], status=RunStatus(row[2]), created_at=row[3], business_time=row[4], config_snapshot=row[5] or {}, model_metadata=row[6] or {})

    async def transition_run(self, run_id: str, status: RunStatus | str) -> RunRecord:
        run = await self.get_run(run_id)
        target = RunStatus(status)
        allowed = {
            RunStatus.CREATED: {RunStatus.RUNNING, RunStatus.CANCELLED},
            RunStatus.RUNNING: {RunStatus.PAUSED, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
            RunStatus.PAUSED: {RunStatus.RUNNING, RunStatus.CANCELLED},
        }
        if target != run.status and target not in allowed.get(run.status, set()):
            raise ValueError(f"invalid run transition: {run.status.value} -> {target.value}")
        await self._write("UPDATE sul_runs SET status=%s WHERE id=%s", (target.value, run_id))
        return run.model_copy(update={"status": target})

    async def enqueue_session(self, session: SessionRecord) -> SessionRecord:
        await self._write(
            "INSERT INTO sul_sessions(id,run_id,persona_id,status,phase,due_business_time,lease_owner,lease_expires_at,step_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (session.id, session.run_id, session.persona_id, session.status.value, session.phase, session.due_business_time, session.lease_owner, session.lease_expires_at, session.step_count),
        )
        return session

    async def get_session(self, session_id: str) -> SessionRecord:
        rows = await self._read("SELECT id,run_id,persona_id,status,phase,due_business_time,lease_owner,lease_expires_at,step_count FROM sul_sessions WHERE id=%s", (session_id,))
        if not rows:
            raise KeyError(f"unknown session: {session_id}")
        return _session(rows[0])

    async def lease_ready_session(self, owner: str, now: datetime, lease_seconds: int) -> SessionRecord | None:
        def lease() -> SessionRecord | None:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT id,run_id,persona_id,status,phase,due_business_time,lease_owner,lease_expires_at,step_count
                        FROM sul_sessions WHERE due_business_time <= %s
                        AND (status='READY' OR (status IN ('LEASED','RUNNING') AND lease_expires_at <= %s))
                        ORDER BY due_business_time,id FOR UPDATE SKIP LOCKED LIMIT 1""",
                        (now, now),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return None
                    cursor.execute("UPDATE sul_sessions SET status='LEASED',lease_owner=%s,lease_expires_at=%s WHERE id=%s", (owner, now + timedelta(seconds=lease_seconds), row[0]))
                connection.commit()
            return _session(row).model_copy(update={"status": SessionStatus.LEASED, "lease_owner": owner, "lease_expires_at": now + timedelta(seconds=lease_seconds)})
        return await asyncio.to_thread(lease)

    async def checkpoint_step(self, session_id: str, event: Event, next_session: SessionRecord) -> SessionRecord:
        def checkpoint() -> None:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT run_id FROM sul_sessions WHERE id=%s FOR UPDATE", (session_id,))
                    row = cursor.fetchone()
                    if row is None:
                        raise KeyError(session_id)
                    if row[0] != event.run_id or next_session.id != session_id:
                        raise ValueError("checkpoint scope mismatch")
                    _insert_event(cursor, event)
                    _update_session(cursor, next_session)
                connection.commit()
        await asyncio.to_thread(checkpoint)
        return next_session

    async def append_event(self, event: Event) -> Event:
        def append() -> None:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    _insert_event(cursor, event)
                connection.commit()
        await asyncio.to_thread(append)
        return event

    async def list_events(self, run_id: str, after_sequence: int = -1, limit: int = 100) -> list[Event]:
        if limit <= 0:
            return []
        rows = await self._read(
            "SELECT id,run_id,persona_id,session_id,sequence_no,kind,wall_time,business_time,payload,artifact_ids FROM sul_events WHERE run_id=%s AND sequence_no>%s ORDER BY sequence_no LIMIT %s",
            (run_id, after_sequence, limit),
        )
        return [Event(id=row[0], run_id=row[1], persona_id=row[2], session_id=row[3], sequence=row[4], kind=row[5], wall_time=row[6], business_time=row[7], payload=row[8] or {}, artifact_ids=row[9] or []) for row in rows]

    async def save_expectation(self, expectation: Expectation) -> Expectation:
        await self._write("INSERT INTO sul_expectations(id,run_id,persona_id,invariant_id,entity_ids,expected_values,due_business_time,source_spec_id,status) VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status", (expectation.id, expectation.run_id, expectation.persona_id, expectation.invariant_id, json.dumps(expectation.entity_ids), json.dumps(expectation.expected_values), expectation.due_business_time, expectation.source_spec_id, expectation.status))
        return expectation

    async def save_finding(self, finding: Finding) -> Finding:
        await self._write("INSERT INTO sul_findings(id,run_id,session_id,invariant_id,status,expected,actual,evidence_ids,verifier_version,replay_status) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s) ON CONFLICT(id) DO UPDATE SET status=EXCLUDED.status", (finding.id, finding.run_id, finding.session_id, finding.invariant_id, finding.status.value, json.dumps(finding.expected), json.dumps(finding.actual), json.dumps(finding.evidence_ids), finding.verifier_version, finding.replay_status.value))
        return finding

    async def _read(self, query: str, params: tuple[Any, ...]) -> list[Any]:
        def read() -> list[Any]:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
        return await asyncio.to_thread(read)

    async def _write(self, query: str, params: tuple[Any, ...]) -> None:
        def write() -> None:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, params)
                connection.commit()
        await asyncio.to_thread(write)


def _session(row: Sequence[Any]) -> SessionRecord:
    return SessionRecord(id=row[0], run_id=row[1], persona_id=row[2], status=SessionStatus(row[3]), phase=row[4], due_business_time=row[5], lease_owner=row[6], lease_expires_at=row[7], step_count=row[8])


def _insert_event(cursor: Any, event: Event) -> None:
    cursor.execute("INSERT INTO sul_events(id,run_id,persona_id,session_id,sequence_no,kind,wall_time,business_time,payload,artifact_ids) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT(id) DO NOTHING", (event.id, event.run_id, event.persona_id, event.session_id, event.sequence, event.kind, event.wall_time, event.business_time, json.dumps(event.payload), json.dumps(event.artifact_ids)))


def _update_session(cursor: Any, session: SessionRecord) -> None:
    cursor.execute("UPDATE sul_sessions SET status=%s,phase=%s,due_business_time=%s,lease_owner=%s,lease_expires_at=%s,step_count=%s WHERE id=%s", (session.status.value, session.phase, session.due_business_time, session.lease_owner, session.lease_expires_at, session.step_count, session.id))


class PostgresMemoryRepository:
    """MemoryRepository implementation using psycopg and a connection factory."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str, *, schema: str | None = None) -> "PostgresMemoryRepository":
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise PostgresRepositoryError("install synthetic-user-lab[postgres] first") from exc
        options = {"options": f"-c search_path={schema}"} if schema else {}
        return cls(lambda: psycopg.connect(dsn, **options))

    async def close(self) -> None:
        return None

    async def apply_migration(self, migration_path: str) -> None:
        from pathlib import Path

        statements = [part.strip() for part in Path(migration_path).read_text(encoding="utf-8").split(";") if part.strip()]

        def apply() -> None:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    for statement in statements:
                        cursor.execute(statement)
                connection.commit()

        try:
            await asyncio.to_thread(apply)
        except Exception as exc:
            raise PostgresRepositoryError("PostgreSQL migration failed") from exc

    async def append(self, record: MemoryRecord) -> MemoryRecord:
        def insert() -> None:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO sul_memory
                        (id, run_id, persona_id, memory_type, text_value,
                         structured_data, source_event_ids, trust, valid_from,
                         valid_to, supersedes_id, status, embedding_model,
                         embedding_dimension)
                        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)""",
                        (record.id, record.run_id, record.persona_id, record.type.value,
                         record.text, json.dumps(record.structured_data),
                         json.dumps(record.source_event_ids), record.trust.value,
                         record.valid_from, record.valid_to, record.supersedes_id,
                         record.status.value, record.embedding_model,
                         record.embedding_dimension),
                    )
                connection.commit()

        try:
            await asyncio.to_thread(insert)
            return record
        except Exception as exc:
            raise PostgresRepositoryError("could not append memory") from exc

    async def list_recent(self, run_id: str, persona_id: str | None, memory_type: str, limit: int) -> list[MemoryRecord]:
        return await self._read(
            """SELECT id,run_id,persona_id,memory_type,text_value,structured_data,
            source_event_ids,trust,valid_from,valid_to,supersedes_id,status,
            embedding_model,embedding_dimension FROM sul_memory
            WHERE run_id=%s AND (persona_id=%s OR persona_id IS NULL)
            AND memory_type=%s AND status='active' ORDER BY valid_from DESC LIMIT %s""",
            (run_id, persona_id, memory_type, max(0, limit)),
        )

    async def search(self, run_id: str, persona_id: str | None, query: str, limit: int) -> list[MemoryRecord]:
        records = await self._read(
            """SELECT id,run_id,persona_id,memory_type,text_value,structured_data,
            source_event_ids,trust,valid_from,valid_to,supersedes_id,status,
            embedding_model,embedding_dimension FROM sul_memory
            WHERE run_id=%s AND (persona_id=%s OR persona_id IS NULL)
            AND status='active' ORDER BY valid_from DESC""",
            (run_id, persona_id),
        )
        terms = set(re.findall(r"[a-z0-9_]+", query.casefold()))
        scored = [(len(terms & set(re.findall(r"[a-z0-9_]+", item.text.casefold()))), item) for item in records]
        return [item for score, item in sorted((pair for pair in scored if pair[0]), key=lambda pair: (-pair[0], -pair[1].valid_from.timestamp(), pair[1].id))[: max(0, limit)]]

    async def get_by_ids(self, run_id: str, persona_id: str | None, ids: Sequence[str]) -> list[MemoryRecord]:
        if not ids:
            return []
        return await self._read(
            """SELECT id,run_id,persona_id,memory_type,text_value,structured_data,
            source_event_ids,trust,valid_from,valid_to,supersedes_id,status,
            embedding_model,embedding_dimension FROM sul_memory
            WHERE run_id=%s AND (persona_id=%s OR persona_id IS NULL) AND id = ANY(%s)""",
            (run_id, persona_id, list(ids)),
        )

    async def _read(self, query: str, params: tuple[Any, ...]) -> list[MemoryRecord]:
        def read() -> list[MemoryRecord]:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, params)
                    return [_memory(row) for row in cursor.fetchall()]

        try:
            return await asyncio.to_thread(read)
        except Exception as exc:
            raise PostgresRepositoryError("could not read memories") from exc

    async def supersede(self, run_id: str, persona_id: str | None, old_id: str, new_record: MemoryRecord) -> MemoryRecord:
        if new_record.run_id != run_id or new_record.persona_id != persona_id or new_record.supersedes_id != old_id:
            raise ValueError("superseding record scope or reference does not match")
        def replace() -> None:
            with self._connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE sul_memory SET status='superseded' WHERE id=%s AND run_id=%s AND (persona_id=%s OR persona_id IS NULL) AND status='active'", (old_id, run_id, persona_id))
                    if cursor.rowcount != 1:
                        raise KeyError(old_id)
                    cursor.execute("INSERT INTO sul_memory (id,run_id,persona_id,memory_type,text_value,structured_data,source_event_ids,trust,valid_from,valid_to,supersedes_id,status,embedding_model,embedding_dimension) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)", (new_record.id, new_record.run_id, new_record.persona_id, new_record.type.value, new_record.text, json.dumps(new_record.structured_data), json.dumps(new_record.source_event_ids), new_record.trust.value, new_record.valid_from, new_record.valid_to, new_record.supersedes_id, new_record.status.value, new_record.embedding_model, new_record.embedding_dimension))
                connection.commit()
        await asyncio.to_thread(replace)
        return new_record


def _memory(row: Sequence[Any]) -> MemoryRecord:
    return MemoryRecord(id=row[0], run_id=row[1], persona_id=row[2], type=MemoryType(row[3]), text=row[4], structured_data=row[5] or {}, source_event_ids=row[6] or [], trust=Trust(row[7]), valid_from=row[8], valid_to=row[9], supersedes_id=row[10], status=MemoryStatus(row[11]), embedding_model=row[12], embedding_dimension=row[13])
