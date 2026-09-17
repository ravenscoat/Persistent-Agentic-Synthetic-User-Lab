-- PostgreSQL schema for the durable synthetic-user lab repositories.
CREATE TABLE IF NOT EXISTS sul_runs (
  id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  business_time TIMESTAMPTZ NOT NULL,
  config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  model_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

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
