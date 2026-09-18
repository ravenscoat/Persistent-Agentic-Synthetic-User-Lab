-- PostgreSQL schema for the durable synthetic-user lab repositories.
CREATE TABLE IF NOT EXISTS sul_runs (
  id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  business_time TIMESTAMPTZ NOT NULL,
  config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  model_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  next_event_sequence BIGINT NOT NULL DEFAULT 0
);

-- Safe for databases created by an older version of this migration.
ALTER TABLE sul_runs ADD COLUMN IF NOT EXISTS next_event_sequence BIGINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS sul_memory (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES sul_runs(id),
  persona_id TEXT,
  memory_type TEXT NOT NULL,
  text_value TEXT NOT NULL,
  structured_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_event_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  trust TEXT NOT NULL,
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ,
  supersedes_id TEXT,
  status TEXT NOT NULL,
  embedding_model TEXT,
  embedding_dimension INTEGER
);

CREATE INDEX IF NOT EXISTS sul_memory_scope_ix
  ON sul_memory (run_id, persona_id, memory_type, status, valid_from DESC);

CREATE TABLE IF NOT EXISTS sul_sessions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES sul_runs(id),
  persona_id TEXT NOT NULL,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  due_business_time TIMESTAMPTZ NOT NULL,
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  step_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS sul_sessions_ready_ix
  ON sul_sessions (status, due_business_time, lease_expires_at);

CREATE TABLE IF NOT EXISTS sul_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES sul_runs(id),
  persona_id TEXT,
  session_id TEXT,
  sequence_no BIGINT NOT NULL,
  kind TEXT NOT NULL,
  wall_time TIMESTAMPTZ NOT NULL,
  business_time TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  artifact_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  UNIQUE (run_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS sul_events_run_sequence_ix
  ON sul_events (run_id, sequence_no);

-- Existing event history must advance the allocator before new workers run.
UPDATE sul_runs AS run
SET next_event_sequence = GREATEST(
  run.next_event_sequence,
  COALESCE((SELECT MAX(event.sequence_no) + 1 FROM sul_events AS event WHERE event.run_id = run.id), 0)
);

CREATE TABLE IF NOT EXISTS sul_expectations (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES sul_runs(id),
  persona_id TEXT NOT NULL,
  invariant_id TEXT NOT NULL,
  entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  expected_values JSONB NOT NULL DEFAULT '{}'::jsonb,
  due_business_time TIMESTAMPTZ NOT NULL,
  source_spec_id TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sul_findings (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES sul_runs(id),
  session_id TEXT NOT NULL,
  invariant_id TEXT NOT NULL,
  status TEXT NOT NULL,
  expected JSONB,
  actual JSONB,
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  verifier_version TEXT NOT NULL,
  replay_status TEXT NOT NULL
);
