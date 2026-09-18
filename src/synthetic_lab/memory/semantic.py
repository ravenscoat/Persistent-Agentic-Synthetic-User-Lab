"""Optional Qdrant indexing with PostgreSQL-backed, scope-safe retrieval."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from synthetic_lab.contracts import MemoryRecord
from .embeddings import OllamaEmbeddingClient


class SemanticIndexUnavailable(RuntimeError):
    """Qdrant or the embedding provider is unavailable for this request."""


class SemanticIndex(Protocol):
    async def upsert(self, record: MemoryRecord) -> None: ...
    async def search(self, run_id: str, query: str, limit: int) -> list[str]: ...
    async def delete(self, memory_id: str) -> None: ...


@dataclass(frozen=True)
class ReindexResult:
    indexed: int
    failed: int
    truncated: bool


class QdrantSemanticIndex:
    """Qdrant vector index. It stores no authoritative memory text.

    The payload is intentionally limited to an ID and routing scope. Full text,
    trust, lifecycle, and provenance stay in PostgreSQL and are checked again
    by ``HybridMemoryRepository`` before a result is returned.
    """

    def __init__(self, client: Any, embedder: Any, *, collection: str = "sul_memory", embedding_model: str = "local") -> None:
        self.client = client
        self.embedder = embedder
        self.collection = collection
        self.embedding_model = embedding_model
        self._dimension: int | None = None

    @classmethod
    def from_url(cls, url: str, embedder: Any, *, collection: str = "sul_memory", embedding_model: str = "local") -> "QdrantSemanticIndex":
        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise SemanticIndexUnavailable("install synthetic-user-lab[qdrant] first") from exc
        return cls(AsyncQdrantClient(url=url), embedder, collection=collection, embedding_model=embedding_model)

    async def upsert(self, record: MemoryRecord) -> None:
        vector = (await self.embedder.embed(record.text))[0]
        if not vector:
            raise SemanticIndexUnavailable("embedding provider returned an empty vector")
        await self._ensure_collection(len(vector))
        try:
            from qdrant_client.models import PointStruct
            await self.client.upsert(
                collection_name=self.collection,
                points=[PointStruct(id=_point_id(record.id), vector=vector, payload={"memory_id": record.id, "run_id": record.run_id, "persona_id": record.persona_id})],
            )
        except Exception as exc:  # pragma: no cover - network/client dependent
            raise SemanticIndexUnavailable("could not write Qdrant memory vector") from exc

    async def search(self, run_id: str, query: str, limit: int) -> list[str]:
        if limit <= 0:
            return []
        vector = (await self.embedder.embed(query))[0]
        if not vector:
            raise SemanticIndexUnavailable("embedding provider returned an empty vector")
        await self._ensure_collection(len(vector))
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue
            response = await self.client.query_points(
                collection_name=self.collection,
                query=vector,
                query_filter=Filter(must=[FieldCondition(key="run_id", match=MatchValue(value=run_id))]),
                limit=limit,
                with_payload=["memory_id"],
                with_vectors=False,
            )
            return [str(point.payload["memory_id"]) for point in response.points if point.payload and point.payload.get("memory_id")]
        except Exception as exc:  # pragma: no cover - network/client dependent
            raise SemanticIndexUnavailable("could not query Qdrant memory vectors") from exc

    async def delete(self, memory_id: str) -> None:
        try:
            from qdrant_client.models import PointIdsList
            await self.client.delete(collection_name=self.collection, points_selector=PointIdsList(points=[_point_id(memory_id)]))
        except Exception as exc:  # pragma: no cover - network/client dependent
            raise SemanticIndexUnavailable("could not delete Qdrant memory vector") from exc

    async def _ensure_collection(self, dimension: int) -> None:
        if self._dimension is not None:
            if self._dimension != dimension:
                raise SemanticIndexUnavailable("embedding dimension changed for the configured Qdrant collection")
            return
        try:
            from qdrant_client.models import Distance, VectorParams
            exists = await self.client.collection_exists(self.collection)
            if not exists:
                await self.client.create_collection(collection_name=self.collection, vectors_config=VectorParams(size=dimension, distance=Distance.COSINE))
            info = await self.client.get_collection(self.collection)
            vectors = info.config.params.vectors
            actual = vectors.size if hasattr(vectors, "size") else None
            if actual != dimension:
                raise SemanticIndexUnavailable(f"Qdrant collection dimension is {actual}, expected {dimension}")
            self._dimension = dimension
        except SemanticIndexUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - network/client dependent
            raise SemanticIndexUnavailable("could not initialise Qdrant collection") from exc


class HybridMemoryRepository:
    """Memory facade: PostgreSQL is authoritative; Qdrant only improves ranking."""

    def __init__(self, primary: Any, semantic_index: SemanticIndex) -> None:
        self.primary = primary
        self.semantic_index = semantic_index

    async def append(self, record: MemoryRecord) -> MemoryRecord:
        saved = await self.primary.append(record)
        try:
            await self.semantic_index.upsert(saved)
        except SemanticIndexUnavailable:
            # Do not lose an observed action because a secondary index is down.
            pass
        return saved

    async def list_recent(self, run_id: str, persona_id: str | None, memory_type: str, limit: int) -> list[MemoryRecord]:
        return await self.primary.list_recent(run_id, persona_id, memory_type, limit)

    async def get_by_ids(self, run_id: str, persona_id: str | None, ids: Sequence[str]) -> list[MemoryRecord]:
        return await self.primary.get_by_ids(run_id, persona_id, ids)

    async def search(self, run_id: str, persona_id: str | None, query: str, limit: int) -> list[MemoryRecord]:
        if limit <= 0:
            return []
        try:
            candidate_ids = await self.semantic_index.search(run_id, query, max(limit * 4, limit))
            hydrated = await self.primary.get_by_ids(run_id, persona_id, candidate_ids)
            # PostgreSQL scope and lifecycle checks have now been applied. Keep
            # Qdrant ranking order only for the records that passed those checks.
            by_id = {record.id: record for record in hydrated}
            ranked = [by_id[memory_id] for memory_id in candidate_ids if memory_id in by_id]
            if len(ranked) >= limit:
                return ranked[:limit]
            lexical = await self.primary.search(run_id, persona_id, query, limit)
            seen = {record.id for record in ranked}
            return (ranked + [record for record in lexical if record.id not in seen])[:limit]
        except SemanticIndexUnavailable:
            return await self.primary.search(run_id, persona_id, query, limit)

    async def supersede(self, run_id: str, persona_id: str | None, old_id: str, new_record: MemoryRecord) -> MemoryRecord:
        saved = await self.primary.supersede(run_id, persona_id, old_id, new_record)
        try:
            await self.semantic_index.delete(old_id)
            await self.semantic_index.upsert(saved)
        except SemanticIndexUnavailable:
            pass
        return saved


def with_optional_qdrant(primary: Any, settings: Any) -> Any:
    """Return the normal repository unless the operator configured Qdrant."""
    if not getattr(settings, "qdrant_url", None):
        return primary
    embedder = OllamaEmbeddingClient(settings.model_base_url, settings.embedding_model)
    index = QdrantSemanticIndex.from_url(
        settings.qdrant_url,
        embedder,
        collection=settings.qdrant_collection,
        embedding_model=settings.embedding_model,
    )
    return HybridMemoryRepository(primary, index)


async def reindex_run(primary: Any, semantic_index: SemanticIndex, run_id: str, *, limit: int = 10000) -> ReindexResult:
    """Index current PostgreSQL memories without changing their source records.

    This is an operator-only repair/backfill operation. It deliberately indexes
    only active records, so superseded and deleted facts do not return through
    semantic retrieval after an index rebuild.
    """
    if limit <= 0:
        return ReindexResult(indexed=0, failed=0, truncated=False)
    if not hasattr(primary, "list_active"):
        raise TypeError("primary repository does not support administrative active-memory listing")
    records = await primary.list_active(run_id, limit=limit + 1)
    truncated = len(records) > limit
    indexed = failed = 0
    for record in records[:limit]:
        try:
            await semantic_index.upsert(record)
            indexed += 1
        except SemanticIndexUnavailable:
            failed += 1
    return ReindexResult(indexed=indexed, failed=failed, truncated=truncated)


def _point_id(memory_id: str) -> str:
    """Qdrant point IDs are UUIDs/integers; contracts permit opaque IDs."""
    return str(uuid5(NAMESPACE_URL, memory_id))
