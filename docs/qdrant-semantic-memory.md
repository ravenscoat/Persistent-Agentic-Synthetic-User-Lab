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

Configure `SUL_QDRANT_URL=http://localhost:6333`. The application should wrap
its PostgreSQL memory repository with `HybridMemoryRepository` and a
`QdrantSemanticIndex` using the configured embedding client. The collection is
created only after the first embedding and rejects an embedding-dimension change
instead of silently mixing incompatible vectors.
