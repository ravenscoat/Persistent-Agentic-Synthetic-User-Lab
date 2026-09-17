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
from typing import Any, Callable

from synthetic_lab.contracts import MemoryRecord, MemoryStatus, MemoryType, Trust


class PostgresRepositoryError(RuntimeError):
    """A PostgreSQL operation failed or the optional driver is unavailable."""


class PostgresMemoryRepository:
    """MemoryRepository implementation using psycopg and a connection factory."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresMemoryRepository":
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise PostgresRepositoryError("install synthetic-user-lab[postgres] first") from exc
        return cls(lambda: psycopg.connect(dsn))

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
