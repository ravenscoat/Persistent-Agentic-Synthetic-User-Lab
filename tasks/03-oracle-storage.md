# Task 03 — Oracle repositories and migrations

Depends on: 01. Own: src/synthetic_lab/storage/, migrations/, tests/storage/.

Implement StateRepository and MemoryRepository with parameterized SQL, versioned migrations, transactions, durable events, artifact metadata, and typed reads. Preserve all seven memory categories through documented tables/projections. Provide relational-only retrieval and a capability-checked VECTOR retrieval path using a fixed embedding dimension and cosine distance.

Store leases, action IDs, checkpoints, memory provenance, validity intervals, and tombstones. Scope all reads by run and persona with explicit permission for shared records. Use deterministic tie-breaking and stable event ordering. Implement bounded connection pooling and clear unavailable-service errors.

Acceptance: integration tests verify transaction rollback, restart persistence, idempotent checkpoint insertion, mutually exclusive lease acquisition, persona isolation, historical fact supersession, and rejection of incompatible embeddings. Fake tests run without Oracle; gated real-Oracle tests identify the actual version/capabilities used. No destructive drop-all migration on startup.
