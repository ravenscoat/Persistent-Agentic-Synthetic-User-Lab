-- Apply this migration explicitly. Startup never drops or recreates user data.
CREATE TABLE sul_runs (
  id VARCHAR2(128) PRIMARY KEY,
  scenario_id VARCHAR2(128) NOT NULL,
  status VARCHAR2(32) NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  business_time TIMESTAMP WITH TIME ZONE NOT NULL,
  config_snapshot CLOB CHECK (config_snapshot IS JSON),
  model_metadata CLOB CHECK (model_metadata IS JSON)
);
CREATE TABLE sul_sessions (
  id VARCHAR2(128) PRIMARY KEY,
  run_id VARCHAR2(128) NOT NULL REFERENCES sul_runs(id),
  persona_id VARCHAR2(128) NOT NULL,
  status VARCHAR2(32) NOT NULL,
  phase VARCHAR2(128) NOT NULL,
  due_business_time TIMESTAMP WITH TIME ZONE NOT NULL,
  lease_owner VARCHAR2(256),
  lease_expires_at TIMESTAMP WITH TIME ZONE,
  step_count NUMBER(10) DEFAULT 0 NOT NULL
);
CREATE INDEX sul_sessions_due_ix ON sul_sessions(status, due_business_time);
CREATE TABLE sul_events (
  id VARCHAR2(128) PRIMARY KEY,
  run_id VARCHAR2(128) NOT NULL REFERENCES sul_runs(id),
  persona_id VARCHAR2(128),
  session_id VARCHAR2(128),
  sequence_no NUMBER(19) NOT NULL,
  kind VARCHAR2(128) NOT NULL,
  wall_time TIMESTAMP WITH TIME ZONE NOT NULL,
  business_time TIMESTAMP WITH TIME ZONE NOT NULL,
  payload CLOB CHECK (payload IS JSON),
  artifact_ids CLOB CHECK (artifact_ids IS JSON),
  CONSTRAINT sul_events_run_seq_uq UNIQUE(run_id, sequence_no)
);
CREATE TABLE sul_memory (
  id VARCHAR2(128) PRIMARY KEY,
  run_id VARCHAR2(128) NOT NULL REFERENCES sul_runs(id),
  persona_id VARCHAR2(128),
  memory_type VARCHAR2(32) NOT NULL,
  text_value CLOB NOT NULL,
  structured_data CLOB CHECK (structured_data IS JSON),
  source_event_ids CLOB CHECK (source_event_ids IS JSON),
  trust VARCHAR2(32) NOT NULL,
  valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
  valid_to TIMESTAMP WITH TIME ZONE,
  supersedes_id VARCHAR2(128),
  status VARCHAR2(32) NOT NULL,
  embedding_model VARCHAR2(256),
  embedding_dimension NUMBER(10)
);
CREATE INDEX sul_memory_scope_ix ON sul_memory(run_id, persona_id, memory_type, status, valid_from);
CREATE TABLE sul_expectations (id VARCHAR2(128) PRIMARY KEY, run_id VARCHAR2(128) NOT NULL, persona_id VARCHAR2(128) NOT NULL, invariant_id VARCHAR2(128) NOT NULL, entity_ids CLOB CHECK (entity_ids IS JSON), expected_values CLOB CHECK (expected_values IS JSON), due_business_time TIMESTAMP WITH TIME ZONE NOT NULL, source_spec_id VARCHAR2(128) NOT NULL, status VARCHAR2(32) NOT NULL);
CREATE TABLE sul_findings (id VARCHAR2(128) PRIMARY KEY, run_id VARCHAR2(128) NOT NULL, session_id VARCHAR2(128) NOT NULL, invariant_id VARCHAR2(128) NOT NULL, status VARCHAR2(32) NOT NULL, expected CLOB CHECK (expected IS JSON), actual CLOB CHECK (actual IS JSON), evidence_ids CLOB CHECK (evidence_ids IS JSON), verifier_version VARCHAR2(128) NOT NULL, replay_status VARCHAR2(32) NOT NULL);
CREATE TABLE sul_artifacts (id VARCHAR2(128) PRIMARY KEY, run_id VARCHAR2(128) NOT NULL, relative_path VARCHAR2(1024) NOT NULL, media_type VARCHAR2(256) NOT NULL, sha256 VARCHAR2(128) NOT NULL, byte_count NUMBER(19) NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL);
