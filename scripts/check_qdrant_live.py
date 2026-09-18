"""Validate live Qdrant Cloud indexing, scope filtering, and deletion."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from synthetic_lab.config import Settings
from synthetic_lab.contracts import MemoryRecord, MemoryType, Trust
from synthetic_lab.memory import OllamaEmbeddingClient, QdrantSemanticIndex


async def main() -> int:
    settings = Settings()
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise RuntimeError("SUL_QDRANT_URL and SUL_QDRANT_API_KEY are required")
    embedder = OllamaEmbeddingClient(settings.model_base_url, settings.embedding_model)
    collection = f"{settings.qdrant_collection}_check_{uuid4().hex[:10]}"
    index = QdrantSemanticIndex.from_url(settings.qdrant_url, embedder, collection=collection, embedding_model=settings.embedding_model, api_key=settings.qdrant_api_key)
    now = datetime.now(timezone.utc)
    run_a, run_b = str(uuid4()), str(uuid4())
    first = MemoryRecord(id=str(uuid4()), run_id=run_a, persona_id="persona-a", type=MemoryType.SEMANTIC, text="The user prefers PostgreSQL database examples.", trust=Trust.VERIFIED, valid_from=now)
    second = MemoryRecord(id=str(uuid4()), run_id=run_b, persona_id="persona-b", type=MemoryType.SEMANTIC, text="The user prefers unrelated billing examples.", trust=Trust.VERIFIED, valid_from=now)
    try:
        await index.upsert(first); await index.upsert(second)
        same_scope = await index.search(run_a, "Which database does the user prefer?", 5)
        other_scope = await index.search(run_b, "Which database does the user prefer?", 5)
        await index.delete(first.id)
        deleted = await index.search(run_a, "Which database does the user prefer?", 5)
        payload = {"collection": collection, "embedding_model": settings.embedding_model, "same_scope_ids": same_scope, "other_scope_ids": other_scope, "after_delete_ids": deleted, "scope_filter_passed": first.id in same_scope and first.id not in other_scope, "delete_passed": first.id not in deleted}
        print(json.dumps(payload, indent=2)); return 0 if payload["scope_filter_passed"] and payload["delete_passed"] else 1
    finally:
        await embedder.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
