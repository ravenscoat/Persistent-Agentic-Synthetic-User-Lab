# Persistent Synthetic User Lab

A local application-testing system whose synthetic users remember earlier sessions, return later, and uncover failures spanning multiple visits.

Example: a user starts a seven-day trial, returns on day six, and discovers that access has already expired. The system preserves the browser actions and database evidence needed to reproduce the failure.

Status: working local prototype with a controlled demo application, browser tools,
durable-storage adapters, memory/context assembly, bounded agent runtime, API,
and deterministic evaluation harness.

Start with [ARCHITECTURE.md](ARCHITECTURE.md), then [CONTRACTS.md](CONTRACTS.md), and assign work from [TASKS.md](TASKS.md). Each task has a separate handoff prompt under [tasks](tasks/).

The default design uses a single locally served Qwen3-8B model. External model APIs
are optional adapters. PostgreSQL is the durable repository for runs and memories,
while Qdrant is used for semantic/vector retrieval. Browser execution uses
Playwright. The controlled application's business state currently uses SQLite for
the deterministic demo; it can be moved to PostgreSQL as the SaaS simulation grows.
PostgreSQL is the active durable backend; the prototype falls back to in-memory
repositories for local tests. The historical Oracle adapter is retained only for
backward compatibility and is not part of the active deployment path.

Set `SUL_MODEL_FALLBACK_NAMES` to a comma-separated list of additional Ollama
models (for example `qwen2.5:3b`). The configured chain tries the primary model
first and only uses a fallback after timeout, endpoint failure, throttling, or
invalid structured output.

## Start PostgreSQL

Install the optional driver and start the local database:

\`\`\`powershell
.\.venv\Scripts\python.exe -m pip install -e ".[postgres]"
docker compose -f docker-compose.postgres.yml up -d
$env:SUL_POSTGRES_DSN = "postgresql://synthetic_lab:synthetic_lab@localhost:5432/synthetic_lab"
.\.venv\Scripts\python.exe scripts/prepare_postgres.py
\`\`\`

With SUL_POSTGRES_DSN set, the demo app uses PostgreSQL for business state.
The same seeded faults can be selected with SUL_BUSINESS_FAULT, for example
duplicate_charge or owner_transfer_leak.

Agent state and memory also use PostgreSQL during scripts/run_e2e.py runs
when this variable is set. See [restart recovery](docs/restart-recovery.md) for
the crash-and-resume proof.

To verify the live API repository path across two API instances, run:

    .\.venv\Scripts\python.exe scripts\check_postgres_live.py

## Run locally

Create the environment and install the test dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,browser,model,oracle]"
```

Run the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn synthetic_lab.api.app:app --reload
```

Run the controlled application separately when exercising browser personas:

```powershell
.\.venv\Scripts\python.exe -m uvicorn synthetic_lab.demo.app:create_demo_app --factory --port 8001
```

Run tests and the deterministic scenario evaluation:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\evaluate.py --output outputs\evaluation.json
```

The evaluation executes each scenario once against healthy state and once with
its seeded fault. The independent verifier must report `satisfied` for healthy
state and `confirmed` for the fault. See [docs/evaluation.md](docs/evaluation.md).

Run the real browser suspicion-to-verifier matrix and the Qwen memory ablation:

```powershell
.\.venv\Scripts\python.exe scripts\check_scenario_executor.py
.\.venv\Scripts\python.exe scripts\evaluate_memory.py --real-model --trials 3 --output outputs\memory-ablation-qwen.json
```

The browser matrix persists the agent suspicion before verification and links
the verifier result to that event. The Qwen report compares memory on/off and
records completion, bug discovery, false positives, model latency, and the
input/output token counts reported by Ollama.

For the concurrent two-browser Qwen proof, see
[docs/two-persona-transfer.md](docs/two-persona-transfer.md). It demonstrates
two concurrent scheduler leases, isolated persistent memories, unique run-wide
event sequences, and an independent PostgreSQL-backed permission verifier.
