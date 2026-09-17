# Persistent Synthetic User Lab

A local application-testing system whose synthetic users remember earlier sessions, return later, and uncover failures spanning multiple visits.

Example: a user starts a seven-day trial, returns on day six, and discovers that access has already expired. The system preserves the browser actions and database evidence needed to reproduce the failure.

Status: working local prototype with a controlled demo application, browser tools,
durable-storage adapters, memory/context assembly, bounded agent runtime, API,
and deterministic evaluation harness.

Start with [ARCHITECTURE.md](ARCHITECTURE.md), then [CONTRACTS.md](CONTRACTS.md), and assign work from [TASKS.md](TASKS.md). Each task has a separate handoff prompt under [tasks](tasks/).

The default design uses a single locally served Qwen3-8B model. External model APIs
are optional adapters. Oracle is the durable memory store; browser execution uses
Playwright. A separate SQLite database holds the controlled application's business
state. The prototype falls back to in-memory repositories for local tests.

Set `SUL_MODEL_FALLBACK_NAMES` to a comma-separated list of additional Ollama
models (for example `qwen2.5:3b`). The configured chain tries the primary model
first and only uses a fallback after timeout, endpoint failure, throttling, or
invalid structured output.

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
