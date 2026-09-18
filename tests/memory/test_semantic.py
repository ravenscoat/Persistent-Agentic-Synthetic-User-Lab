from datetime import datetime, timezone

import pytest

from synthetic_lab.contracts import MemoryRecord, MemoryType, Trust
from synthetic_lab.memory.semantic import HybridMemoryRepository, SemanticIndexUnavailable
from synthetic_lab.storage import InMemoryMemoryRepository


class FakeSemanticIndex:
    def __init__(self, ids=None, unavailable=False):
        self.ids = ids or []
        self.unavailable = unavailable
        self.indexed = []

    async def upsert(self, record):
        if self.unavailable:
            raise SemanticIndexUnavailable("offline")
        self.indexed.append(record.id)

    async def search(self, run_id, query, limit):
        if self.unavailable:
            raise SemanticIndexUnavailable("offline")
        return self.ids[:limit]

    async def delete(self, memory_id):
        return None


def record(memory_id, persona_id, text):
    return MemoryRecord(id=memory_id, run_id="run", persona_id=persona_id, type=MemoryType.SEMANTIC, text=text, trust=Trust.VERIFIED, valid_from=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_semantic_results_are_hydrated_with_postgres_scope_checks():
    primary = InMemoryMemoryRepository()
    own = record("own", "alice", "billing cancellation workflow")
    other = record("other", "bob", "billing cancellation workflow")
    shared = record("shared", None, "billing help")
    for item in (own, other, shared):
        await primary.append(item)
    memory = HybridMemoryRepository(primary, FakeSemanticIndex(["other", "shared", "own"]))
    values = await memory.search("run", "alice", "billing", 3)
    assert [item.id for item in values] == ["shared", "own"]


@pytest.mark.asyncio
async def test_secondary_index_failure_keeps_authoritative_write_and_uses_lexical_fallback():
    primary = InMemoryMemoryRepository()
    memory = HybridMemoryRepository(primary, FakeSemanticIndex(unavailable=True))
    saved = await memory.append(record("m1", "alice", "cancel an active subscription"))
    assert saved.id == "m1"
    values = await memory.search("run", "alice", "cancel subscription", 3)
    assert [item.id for item in values] == ["m1"]


@pytest.mark.asyncio
async def test_embedded_qdrant_indexes_and_retrieves_real_vectors():
    qdrant = pytest.importorskip("qdrant_client")

    class FixedEmbedder:
        async def embed(self, text):
            values = [text] if isinstance(text, str) else text
            return [[1.0, 0.0] if "billing" in value else [0.0, 1.0] for value in values]

    from synthetic_lab.memory.semantic import QdrantSemanticIndex

    client = qdrant.AsyncQdrantClient(location=":memory:")
    primary = InMemoryMemoryRepository()
    memory = HybridMemoryRepository(primary, QdrantSemanticIndex(client, FixedEmbedder(), collection="test_memory"))
    await memory.append(record("opaque-billing-id", "alice", "billing cancellation"))
    await memory.append(record("opaque-project-id", "alice", "project creation"))
    values = await memory.search("run", "alice", "billing", 1)
    assert [item.id for item in values] == ["opaque-billing-id"]
    await client.close()
