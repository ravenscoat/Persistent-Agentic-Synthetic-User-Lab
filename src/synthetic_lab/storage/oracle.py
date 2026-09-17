from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from synthetic_lab.contracts import Event, MemoryRecord, MemoryStatus, RunRecord, RunStatus


class OracleRepositoryError(RuntimeError):
    """A database operation failed or Oracle is unavailable."""


class OracleStateRepository:
    """Async facade over a python-oracledb connection pool."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @classmethod
    def from_dsn(cls, user: str, password: str, dsn: str, *, pool_min: int = 1, pool_max: int = 4) -> "OracleStateRepository":
        try:
            import oracledb
            pool = oracledb.create_pool(user=user, password=password, dsn=dsn, min=pool_min, max=pool_max, increment=1)
        except Exception as exc:
            raise OracleRepositoryError("could not create Oracle connection pool") from exc
        return cls(pool)

    async def close(self) -> None:
        await asyncio.to_thread(self.pool.close)

    async def apply_migration(self, migration_path: str | Path) -> None:
        sql = Path(migration_path).read_text(encoding="utf-8")
        # Remove SQL comment-only lines before splitting. Otherwise a comment
        # immediately before the first CREATE would cause that whole statement
        # to be discarded by the old prefix check.
        clean_sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        statements = [statement.strip() for statement in clean_sql.split(";") if statement.strip()]

        def apply() -> None:
            with self.pool.acquire() as connection:
                with connection.cursor() as cursor:
                    for statement in statements:
                        cursor.execute(statement)
                connection.commit()

        try:
            await asyncio.to_thread(apply)
        except Exception as exc:
            raise OracleRepositoryError("Oracle migration failed") from exc

    async def create_run(self, run: RunRecord) -> RunRecord:
        def create() -> None:
            with self.pool.acquire() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO sul_runs (id, scenario_id, status, created_at, business_time, config_snapshot, model_metadata) VALUES (:1,:2,:3,:4,:5,:6,:7)", [run.id, run.scenario_id, run.status.value, run.created_at, run.business_time, json.dumps(run.config_snapshot), json.dumps(run.model_metadata)])
                connection.commit()
        try:
            await asyncio.to_thread(create)
        except Exception as exc:
            raise OracleRepositoryError("could not create run") from exc
        return run

    async def get_run(self, run_id: str) -> RunRecord:
        def get() -> RunRecord:
            with self.pool.acquire() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id, scenario_id, status, created_at, business_time, config_snapshot, model_metadata FROM sul_runs WHERE id = :1", [run_id])
                    row = cursor.fetchone()
            if row is None:
                raise KeyError(run_id)
            return RunRecord(id=row[0], scenario_id=row[1], status=RunStatus(row[2]), created_at=row[3], business_time=row[4], config_snapshot=json.loads(row[5] or "{}"), model_metadata=json.loads(row[6] or "{}"))
        try:
            return await asyncio.to_thread(get)
        except KeyError:
            raise
        except Exception as exc:
            raise OracleRepositoryError("could not read run") from exc

    async def transition_run(self, run_id: str, status: RunStatus | str) -> RunRecord:
        run = await self.get_run(run_id)
        updated = run.model_copy(update={"status": RunStatus(status)})
        def update() -> None:
            with self.pool.acquire() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE sul_runs SET status = :1 WHERE id = :2", [updated.status.value, run_id])
                connection.commit()
        await asyncio.to_thread(update)
        return updated


class OracleMemoryRepository:
    """Scoped Oracle memory operations; vector search can be added beside lexical fallback."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def append(self, record: MemoryRecord) -> MemoryRecord:
        def insert() -> None:
            with self.pool.acquire() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO sul_memory (id, run_id, persona_id, memory_type, text_value, structured_data, source_event_ids, trust, valid_from, valid_to, supersedes_id, status, embedding_model, embedding_dimension) VALUES (:1,:2,:3,:4,:5,:6,:7,:8,:9,:10,:11,:12,:13,:14)", [record.id, record.run_id, record.persona_id, record.type.value, record.text, json.dumps(record.structured_data), json.dumps(record.source_event_ids), record.trust.value, record.valid_from, record.valid_to, record.supersedes_id, record.status.value, record.embedding_model, record.embedding_dimension])
                connection.commit()
        try:
            await asyncio.to_thread(insert)
            return record
        except Exception as exc:
            raise OracleRepositoryError("could not append memory") from exc

    async def list_recent(self, run_id: str, persona_id: str | None, memory_type: str, limit: int) -> list[MemoryRecord]:
        def read() -> list[MemoryRecord]:
            with self.pool.acquire() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id,run_id,persona_id,memory_type,text_value,structured_data,source_event_ids,trust,valid_from,valid_to,supersedes_id,status,embedding_model,embedding_dimension FROM sul_memory WHERE run_id=:1 AND (persona_id=:2 OR persona_id IS NULL) AND memory_type=:3 AND status='active' ORDER BY valid_from DESC", [run_id, persona_id, memory_type])
                    rows = cursor.fetchmany(max(0, limit))
            return [_memory(row) for row in rows]
        return await asyncio.to_thread(read)


def _memory(row: Sequence[Any]) -> MemoryRecord:
    from synthetic_lab.contracts import MemoryType, Trust
    return MemoryRecord(id=row[0], run_id=row[1], persona_id=row[2], type=MemoryType(row[3]), text=row[4], structured_data=json.loads(row[5] or "{}"), source_event_ids=json.loads(row[6] or "[]"), trust=Trust(row[7]), valid_from=row[8], valid_to=row[9], supersedes_id=row[10], status=MemoryStatus(row[11]), embedding_model=row[12], embedding_dimension=row[13])
