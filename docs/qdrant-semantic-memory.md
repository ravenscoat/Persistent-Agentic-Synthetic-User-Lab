# Qdrant semantic memory

PostgreSQL is the authoritative memory store. It contains the text, trust
level, provenance, lifecycle status, run scope, and persona scope. Qdrant is a
secondary index: it stores a vector, memory ID, run ID, and persona ID only.

This separation matters. If Qdrant is unavailable, an action checkpoint still
writes to PostgreSQL and retrieval falls back to lexical search. If Qdrant
returns a vector candidate, the system loads that ID from PostgreSQL again with
the requesting run and persona scope. A vector from another persona can never
reach the model merely because it was semantically similar.

Start the optional service alongside PostgreSQL:

```powershell
docker compose -f docker-compose.postgres.yml up -d
.venv\Scripts\python.exe -m pip install -e ".[qdrant]"
```

Configure `SUL_QDRANT_URL=http://localhost:6333` and, for Qdrant Cloud,
`SUL_QDRANT_API_KEY` through the environment. The application should wrap
its PostgreSQL memory repository with `HybridMemoryRepository` and a
`QdrantSemanticIndex` using the configured embedding client. The collection is
created only after the first embedding and rejects an embedding-dimension change
instead of silently mixing incompatible vectors.

## Backfill after enabling Qdrant

Existing PostgreSQL memory is intentionally not copied during normal agent
startup. Run this explicit, non-destructive operation per run after Qdrant and
the embedding model are available:

```powershell
.venv\Scripts\python.exe scripts\reindex_qdrant.py <run-id>
```

Only active memory is indexed. The command reports `truncated=true` when the
chosen limit would leave records behind, so an operator cannot mistake a partial
backfill for a complete one.

Validate a live Cloud or server deployment with:

```powershell
.venv\Scripts\python.exe scripts\check_qdrant_live.py
```

This creates a temporary collection, verifies same-run retrieval, verifies
cross-run filtering, and verifies deletion. Collection initialization creates
keyword payload indexes for `run_id` and `persona_id`, which Qdrant Cloud
requires for filtered search.
