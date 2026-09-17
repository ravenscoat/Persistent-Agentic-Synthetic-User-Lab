# Shared contracts, version 1

Task 01 implements these contracts before downstream code begins. Use Pydantic models with forbidden unknown fields at execution boundaries. IDs are opaque strings; persisted times are timezone-aware UTC. JSON payloads use JSON-compatible values only.

## Core records

* Run: id, scenario_id, status, created_at, business_time, config_snapshot, model_metadata.
* Persona: id, run_id, kind, goal, application_account_id, allowed_tool_names.
* Session: id, run_id, persona_id, status, phase, due_business_time, lease_owner, lease_expires_at, step_count.
* Observation: id, run_id, session_id, url, title, visible_text, elements, captured_at, artifact_ids.
* Element: id, role, name, allowed_actions. IDs are scoped to an observation.
* AgentDecision: kind(action|memory_query|finish|blocked), action optional, query optional, summary optional. Validate required and forbidden fields by kind.
* Action: id, tool_name, arguments, observation_id optional. Browser actions require observation_id; args validated against the registered tool schema.
* ToolResult: action_id, status(success|error|uncertain), data, error_code optional, artifact_ids, observed_at.
* MemoryRecord: id, run_id, persona_id nullable, type, text, structured_data, source_event_ids, trust(observed|verified|candidate), valid_from, valid_to nullable, supersedes_id nullable, status(active|superseded|deleted), embedding_model nullable, embedding_dimension nullable.
* Expectation: id, run_id, persona_id, invariant_id, entity_ids, expected_values, due_business_time, source_spec_id, status(pending|satisfied|violated|inconclusive).
* Finding: id, run_id, session_id, invariant_id, status(suspicion|confirmed|inconclusive), expected, actual, evidence_ids, verifier_version, replay_status.
* Event: id, run_id, persona_id nullable, session_id nullable, sequence, kind, wall_time, business_time, payload, artifact_ids.
* Artifact: id, run_id, relative_path, media_type, sha256, byte_count, created_at.

## Component protocols

All runtime I/O protocols are async. Concrete SQL/embedding implementations must avoid blocking the event loop by using supported async APIs or bounded worker threads.

* ModelClient.decide(messages, decision_schema, generation_options) -> ModelResponse(decision, usage, latency_ms, model_id). Typed failures: ModelUnavailable, ModelTimeout, InvalidModelOutput. No automatic infinite retries.
* MemoryRepository.append(record), search(scope, query, limit), list_recent(scope, type, limit), get_by_ids(scope, ids), supersede(scope, old_id, new_record). Scope must always include run_id and persona_id or an explicitly permitted shared scope.
* ContextAssembler.build(persona, session, observation, budgets) -> ContextBundle(messages, included_memory_ids, omitted_counts, estimated_tokens, accounting_method).
* BrowserSession.observe() -> Observation; execute(action) -> ToolResult; save_state() -> Artifact; close().
* ToolRegistry.list_allowed(persona) -> tool schemas; dispatch(persona, action) -> ToolResult. Unknown and unauthorized names fail before execution.
* StateRepository.create_run, get_run, transition_run, enqueue_session, lease_ready_session, checkpoint_step, append_event, list_events, save_expectation, save_finding. Define typed argument/result models in Task 01.
* ScenarioController.initialize(run), advance_phase(run), reset(run), get_spec(scenario_id). Controller interface is never a persona tool.
* Verifier.check(invariant_id, verification_context) -> VerificationResult(verdict, expected, actual, evidence_ids). VerificationContext includes trusted specification and inspection handles, excluding seeded-fault labels.
* ReplayService.replay(finding_id) -> ReplayResult(status, evidence_ids, reason).

## Tool set for MVP

observe_page, navigate, click, fill, select_option, back, search_memory, report_suspicion. Finish is a decision kind, not a browser tool. Screenshots are captured by the runtime at important boundaries. Avoid arbitrary JavaScript, shell, SQL, filesystem, or admin API tools for personas.

## Control API

POST /api/runs creates a run from scenario ID and validated budgets. POST /api/runs/{id}/start, /pause, /resume, /cancel control execution. GET /api/runs/{id} returns status and metrics. GET /api/runs/{id}/events supports an after_sequence cursor and SSE streaming. GET /api/runs/{id}/findings lists findings. GET /api/findings/{id} returns report metadata. POST /api/findings/{id}/replay requests isolated replay. GET /api/artifacts/{id} resolves only recorded artifact paths under the configured artifact root.

Requests that change state carry a request_id and are idempotent. Return structured errors with code, message, and retryable. Default binding is localhost.

## Persistence requirements

Migrations are versioned and non-destructive by default. Tables cover runs, personas, sessions, events, expectations, findings, artifacts, memory records and tool registry versions. Tool logs/conversation can be event-backed with typed projections; document this mapping. Separate vector payloads by embedding version or reject incompatible dimensions. Never concatenate user values into SQL.

checkpoint_step atomically writes step state and corresponding event within Oracle. Cross-database actions cannot be atomic; reconcile uncertain outcomes. Enforce unique event sequence per run and unique action ID per run. Lease acquisition must prevent duplicate session execution. Leases use wall time, due phases use business time.

## Changes to contracts

Downstream tasks may propose contract changes but must not silently change shared interfaces. Record necessary changes and their affected consumers. The integrating agent updates contract definitions, mocks, and affected tests together.
