"""Backfill active PostgreSQL memories into the configured Qdrant collection."""
from __future__ import annotations

import argparse
import asyncio

from synthetic_lab.config import Settings
from synthetic_lab.memory import OllamaEmbeddingClient, QdrantSemanticIndex, reindex_run
from synthetic_lab.storage import PostgresMemoryRepository


async def main(run_id: str, limit: int) -> int:
    settings = Settings()
    if not settings.postgres_dsn or not settings.qdrant_url:
        raise RuntimeError("SUL_POSTGRES_DSN and SUL_QDRANT_URL are required")
    primary = PostgresMemoryRepository.from_dsn(settings.postgres_dsn)
    embedder = OllamaEmbeddingClient(settings.model_base_url, settings.embedding_model)
    index = QdrantSemanticIndex.from_url(settings.qdrant_url, embedder, collection=settings.qdrant_collection, embedding_model=settings.embedding_model, api_key=settings.qdrant_api_key)
    try:
        result = await reindex_run(primary, index, run_id, limit=limit)
        print(f"indexed={result.indexed} failed={result.failed} truncated={result.truncated}")
        return 0 if result.failed == 0 and not result.truncated else 1
    finally:
        await embedder.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.run_id, args.limit)))
