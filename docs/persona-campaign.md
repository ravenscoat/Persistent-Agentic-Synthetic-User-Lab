# Returned-persona campaign

With PostgreSQL configured in `SUL_POSTGRES_DSN` and local Ollama serving
`qwen3:8b`, run from the project environment:

```powershell
.venv\Scripts\python.exe -u scripts\run_persona_campaign.py
```

The campaign runs trial return, billing inspection, former-owner access, and
onboarding and task-workflow inspection against healthy and faulty state: ten cases in total.
Each case has a separate PostgreSQL schema and browser context, and runs through
the scheduler and Qwen agent loop. Model requests are serialized. This runner
does not put four personas into one shared run.

Setup actions and simulated elapsed time are supplied by the test harness.
Qwen navigates to the return page and describes its observed state. The
independent database verifier establishes whether the invariant is violated.
These results measure guided return visits, not autonomous discovery or the
benefit of memory over a memory-disabled control. Payment setup currently
performs one purchase with a seeded duplicate-charge fault, not a browser retry.
Replay repeats business operations in fresh SQLite state, not a browser trace.

The runner prints progress to stderr and saves `artifacts/persona-campaign.json`
before starting, after each case, and on completion. A partial report has
`complete: false` and cannot pass. Case exceptions are recorded by type and
remaining cases continue. Findings and their replay statuses are persisted in
PostgreSQL and individual JSON evidence reports. Evaluation schemas are retained.

On 2026-09-18, the corrected runner completed all eight real-Qwen cases:
five healthy checks must satisfy their invariants and five faults must be confirmed,
and all four fault replays reproduced. Every agent completed its return visit.
This is one local campaign, not a reliability-rate estimate.

The earlier execution lost the shell session identifier and misreported silence
as a startup failure. Tracking the process to exit exposed repeated navigation
and unauthorized tool choices. Explicit target routes and completion guidance
based on the current observation resolved those failures. Regression tests cover
the guidance and prevent a failed agent or failed replay from passing a case.
