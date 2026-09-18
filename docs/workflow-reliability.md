# Qwen workflow reliability

Validated locally on 2026-09-18: three fresh PostgreSQL runs with Qwen3-8B
completed all six milestones (3/3). Each used 14 browser actions and 16 model
decisions; process runtimes were 39.53, 39.82 and 39.73 seconds. All had exactly
one 2500-cent charge and a cancelled subscription. The real-Qwen seeded task
fault stopped after 9 actions, correctly kept the task milestone incomplete,
and reported expected completed / actual pending with action index 8 as
evidence. This is a small local sample, not a general reliability guarantee.

The multi-page evaluation uses six database-verified milestones: account,
project, completed task, subscription creation, exactly one 2500-cent charge,
and subscription cancellation. The context assembler selects the first
unfinished milestone before every model decision. Qwen still chooses and
executes browser actions; the milestone controller supplies the current goal.
This is a guided agent evaluation, not evidence of unrestricted planning.

Each PostgreSQL evaluation creates a separate `sul_eval_<uuid>` schema with a
connection-local search path. Existing public demo records cannot satisfy its
checks. Schemas are retained for inspection; no existing schema is reset or
deleted. The demo remains a single-account simulation within each schema.

Task and billing pages display actual business status and expose controls that
apply to it. In particular, completing a nonexistent task is no longer offered.
Every run writes a unique `artifacts/e2e-<run-id>.json` with its schema, actual
business state, model mode, action trace, milestone checks and findings. A tool
returning success is not sufficient for workflow completion.

With the local PostgreSQL DSN configured, reproduce the real-model evaluation:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_real.py --workflow --runs 3 --timeout 180 --output artifacts/workflow-benchmark.json
```

To check the seeded task fault, set `SUL_BUSINESS_FAULT=task_completion_stale`
and run `scripts/run_e2e.py --workflow --real-model`. The expected outcome is
nonzero exit, incomplete task milestone and a finding linked to the attempted
completion action. Clear the variable before healthy runs. Run browser scripts
sequentially because they currently share port 8011.

These evaluations use PostgreSQL for application state and in-memory agent
events/memories. They do not yet validate durable agent restart recovery or
Qdrant retrieval. Database access by the milestone controller and verifier is
privileged test-harness access, not an LLM tool.
