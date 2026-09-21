<div align="center">

# Synthetic User Lab

### AI users that remember. Browser tests with evidence.

Describe a customer journey, review a plan, and test it in a real browser.

[Quick start](#quick-start) · [Features](#features) · [Architecture](#architecture) · [Tested examples](#tested-examples) · [Documentation](#documentation)

![Python](https://img.shields.io/badge/Python-3.11%2B-222633?style=flat-square&logo=python&logoColor=white)
![Browser](https://img.shields.io/badge/Browser-Playwright-222633?style=flat-square)
![Storage](https://img.shields.io/badge/Storage-PostgreSQL-222633?style=flat-square)
![Status](https://img.shields.io/badge/Status-Working_prototype-7865e9?style=flat-square)

</div>

![Synthetic User Lab: describe a customer journey](docs/images/workflow.png)

## What it does

Most bugs do not happen on a single page. They happen when a customer returns later, retries a payment, changes roles, or resumes an unfinished task.

Synthetic User Lab explores these journeys with **persistent personas, isolated browser sessions, and scoped memory**. It records actions and observations, separates suspected bugs from verified findings, and produces reports you can inspect.

> “Sign up, create a project, add and complete a task, then start and cancel a subscription. Check that each change persisted and the purchase was charged only once.”

There are two paths:

- **Your staging product:** provide a URL and goal, review the generated plan and visible assertions, then execute a bounded browser test.
- **The controlled demo:** use the included Flowboard project-management app to exercise billing, permissions, onboarding, and task-state bugs with independent business-state checks.

**Status:** a working local prototype, not a universal autonomous QA service. The controlled demo has deeper verification than arbitrary websites.

## Features

### A goal-first testing interface

Start with a customer journey instead of a script. Review the proposed plan before execution, follow the activity timeline, and inspect the outcome and evidence.

### Real browser execution

Playwright performs navigation, clicks, and form input. Fresh observations expose usable controls and safe input state; bounded retries and progress checks help prevent repeated or stale actions.

### Persistent synthetic users

Personas have isolated browser contexts and scoped memories. PostgreSQL stores run state, events, checkpoints, and memory records; an optional Qdrant index supports semantic recall. Recovery paths restore persisted execution state rather than treating every visit as a new user.

### Evidence before conclusions

An agent's suspicion is not automatically a confirmed bug. Controlled scenarios use independent invariant checks and replay; reports retain expected versus observed results and supporting actions.

![Actual payment-retry report with verification evidence](docs/images/verification.png)

*Actual local demo capture. The payment-retry scenario seeds a known fault: the same purchase operation creates two charges instead of one. This particular run does not demonstrate the agent independently performing an entire checkout.*

### Multi-persona workflows

Run separate users through shared scenarios, such as an owner and teammate, while keeping browser sessions and private memory isolated. Shared-run events support coordination without merging identities.

### Local models, optional hosted inference

Ollama is the default inference path, with Qwen3-8B used in local browser experiments. A Groq adapter and configurable fallback paths are also available. Structured action validation, action budgets, and deterministic checks remain important even with a larger model.

### Tracing and operational visibility

Inspect run timelines, model latency, token usage, retries, and tool failures. Optional Langfuse integration adds traces; health and metrics endpoints support local operations.

## How a test works

1. **Describe** the staging URL and customer journey, or select a controlled scenario.
2. **Review** the plan, allowed destination, credential mappings, and completion checks.
3. **Run** the persona in an isolated browser with a bounded action budget.
4. **Remember** relevant observations and execution state.
5. **Verify** outcomes against explicit assertions or scenario-specific invariants.
6. **Inspect** the report: actions, evidence, outcome, and execution details.

## Architecture

```mermaid
flowchart TD
    UI[Dashboard / REST API] --> PLAN[Reviewed plan or controlled scenario]
    PLAN --> RUN[Execution manager and scheduler]
    RUN --> AGENT[Bounded persona agent loop]
    AGENT <--> MODEL[Ollama / optional Groq]
    AGENT <--> BROWSER[Isolated Playwright browser]
    BROWSER --> APP[Staging app / Flowboard demo]
    AGENT <--> MEMORY[Scoped memory and context assembly]
    MEMORY <--> PG[(PostgreSQL: durable records)]
    MEMORY <--> QD[(Optional Qdrant: semantic index)]
    AGENT --> VERIFY[Assertion / invariant verification]
    VERIFY --> REPORT[Findings and replay evidence]
    RUN --> TRACE[Events, metrics, optional Langfuse]
```

This is **an orchestrated agent runtime**, not a collection of unconstrained agents debating every action. The scheduler coordinates work; each persona acts in its own browser; deterministic code enforces boundaries and checks supported outcomes.

| Layer | Responsibility |
| --- | --- |
| FastAPI + HTML/CSS/JavaScript | Dashboard, plans, runs, reports, and APIs |
| Agent runtime | Observation → context → action → validation → execution |
| Playwright | Browser interaction and session isolation |
| PostgreSQL | Durable run state and memory; optional demo business storage |
| Qdrant | Optional semantic retrieval, not the authoritative record store |
| Verification | Explicit assertions, controlled invariants, and replay checks |
| Langfuse | Optional model/run tracing |

## Quick start

Requirements: **Python 3.11+**, **Ollama**, and hardware capable of serving your chosen model. Docker is optional if PostgreSQL/Qdrant are already available. These commands use Windows PowerShell.

### 1. Install

```powershell
git clone https://github.com/ravenscoat/memory-aware-agent.git
cd memory-aware-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,browser,postgres,model,qdrant,observability]"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

Do not overwrite an existing `.env`. Keep credentials out of Git.

### 2. Start the model

```powershell
ollama pull qwen3:8b
ollama serve
```

If Ollama is already running, skip `ollama serve`. Set `SUL_MODEL_FALLBACK_NAMES=` in `.env` unless you have also installed the fallback models listed there.

### 3. Choose storage

For persistent runs, start the bundled development services:

```powershell
docker compose -f docker-compose.postgres.yml up -d
```

Configure `.env`:

```dotenv
# Development-only credentials from the bundled Compose file.
SUL_POSTGRES_DSN=postgresql://synthetic_lab:synthetic_lab@localhost:5432/synthetic_lab
SUL_MODEL_PROVIDER=ollama
SUL_MODEL_NAME=qwen3:8b
SUL_MODEL_FALLBACK_NAMES=
SUL_BROWSER_ORIGIN=http://127.0.0.1:8001
SUL_QDRANT_URL=http://localhost:6333
```

Already have PostgreSQL on port 5432? Use that instance and its DSN instead of starting a conflicting container.

For a lightweight, non-durable trial, leave `SUL_POSTGRES_DSN` and `SUL_QDRANT_URL` empty: the API uses in-memory repositories and the demo uses SQLite. Do not expect restart persistence in that mode.

### 4. Launch

```powershell
.\scripts\start_demo.ps1
```

| Address | What opens |
| --- | --- |
| http://127.0.0.1:8000/dashboard | **Synthetic User Lab** — configure and inspect tests |
| http://127.0.0.1:8001/dashboard | **Flowboard** — the sample product being tested |
| http://127.0.0.1:8000/docs | API documentation |

In the Lab, select **New test → Use sample product** for a first run. A seeded-bug scenario is expected to find an issue; that does not mean the testing platform failed.

The launcher is Windows-specific. For manual startup, inspect its two Uvicorn entry points in [start_demo.ps1](scripts/start_demo.ps1). See the [deployment guide](docs/deployment.md) for authentication and operational controls.

## Tested examples

These are recorded local experiments, **not a reliability benchmark across arbitrary products**.

| Experiment | Observed result |
| --- | --- |
| Healthy project-to-billing workflow with real Qwen3-8B | Signup, project creation, task completion, subscription start/cancel; six milestones verified, 14 browser actions |
| Same workflow with a seeded stale-task fault | Completion click did not persist; independent check confirmed pending instead of completed; replay reproduced the fault and the run stopped before billing |
| Controlled payment retry | Duplicate charge independently confirmed: two charges for one purchase operation |

The healthy and stale-task experiments use a **guided milestone controller**, not unrestricted planning. Automated checks at this README update: **110 passed, 5 skipped**. Skipped integration checks need opt-in services; a green unit suite alone does not prove live-model reliability.

```powershell
# Automated suite
.\.venv\Scripts\python.exe -m pytest -q

# Real browser + local model workflow (requires Ollama)
.\.venv\Scripts\python.exe scripts\run_e2e.py --workflow --real-model
```

See [evaluation](docs/evaluation.md) and [workflow reliability](docs/workflow-reliability.md).

## Current boundaries

- Generic product tests need explicit goals and visible assertions. They cannot infer every business rule or inspect an unrelated product's private database.
- Small-model execution can still repeat actions or exhaust its budget. Failure is reported rather than treated as a successful journey.
- CAPTCHA, complex SSO, real payments, and destructive production operations are not suitable unattended demo targets.
- Aggregate dashboard outcomes are not a calibrated release-quality score. Intentional seeded failures must be interpreted separately.
- Optional operator login is not complete multi-tenant authorization. Review deployment and network controls before exposing the service.
- Captured page text and screenshots are evidence, not a promise of full video playback or visual regression testing.

## Next directions

- Broader client-product adapters and richer business assertions.
- Clearer step-level evidence and test-history comparisons.
- More diverse live-model benchmarks, including long-running multi-user journeys.
- Stronger deployment isolation and production access controls.

These are development directions, not features claimed as complete.

## Documentation

| Guide | Contents |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Components and original system blueprint |
| [Contracts](CONTRACTS.md) | Shared data and interface contracts |
| [Operator console](docs/operator-console.md) | Dashboard and operator workflow |
| [Deployment](docs/deployment.md) | Authentication, configuration, and retention |
| [Evaluation](docs/evaluation.md) | Test harness and evaluation paths |
| [Workflow reliability](docs/workflow-reliability.md) | Browser-loop reliability |
| [Semantic memory](docs/qdrant-semantic-memory.md) | Qdrant integration |
| [Restart recovery](docs/restart-recovery.md) | Durable execution and recovery |
| [Shared persona runs](docs/shared-persona-run.md) | Multi-persona coordination |
| [Ownership transfer](docs/two-persona-transfer.md) | Two-user permission scenario |

<div align="center">

**Built to make agent behavior inspectable—not just impressive in a demo.**

</div>
