# Operator console

Start the API from the repository root:

```powershell
\.venv\Scripts\python.exe -m uvicorn synthetic_lab.api.app:app --reload --port 8000
```

Then open [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard).

The console is intentionally served by FastAPI, so a separate frontend build is
not required for a local demo. It provides:

- run creation and lifecycle controls;
- active/completed run metrics;
- persona and event-stream visibility;
- findings, expected/actual values, replay status, and memory evidence;
- automatic refresh every five seconds;
- a safe Langfuse workspace link when `SUL_LANGFUSE_HOST`,
  `SUL_LANGFUSE_PUBLIC_KEY`, and `SUL_LANGFUSE_SECRET_KEY` are configured.

The event stream is the durable agent log: it is read from the repository and
includes model decisions, tool results, memory queries, checkpoints, and finish
or failure events. Langfuse remains the detailed trace view. The integration
exports only safe metadata (run/persona IDs, model name, decision kind, and
latency); prompts, page observations, and tool outputs stay local. The browser
is never given a Langfuse secret key.
