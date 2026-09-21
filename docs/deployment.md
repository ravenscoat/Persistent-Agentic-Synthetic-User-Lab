# Client deployment and operations

## Local demonstration

1. Create `.env` from `.env.example` and set the model and PostgreSQL values.
2. Start PostgreSQL (and optionally Qdrant) with `docker compose -f docker-compose.postgres.yml up -d`.
3. Run `scripts\start_demo.ps1` from PowerShell.
4. Open `http://127.0.0.1:8000/dashboard`. The separate Flowboard SaaS app at port 8001 is the controlled product under test.

For a shared deployment, set these values in the environment rather than in a committed file:

```text
SUL_POSTGRES_DSN=postgresql://...
SUL_GROQ_API_KEY=...
SUL_DASHBOARD_PASSWORD=<long random password>
SUL_DASHBOARD_SESSION_SECRET=<different long random secret>
SUL_DASHBOARD_COOKIE_SECURE=true
```

When both dashboard secrets are present, every dashboard and `/api/*` request
requires operator login. Health and Prometheus metrics remain available for
infrastructure monitoring. The login session is signed, HTTP-only, same-site,
and expires after `SUL_DASHBOARD_SESSION_TTL_SECONDS` (eight hours by default).
Run behind HTTPS before enabling `SUL_DASHBOARD_COOKIE_SECURE=true`.

## Retention

Runs, events, findings, and durable memories remain in PostgreSQL until a
retention policy removes them. Preview a cleanup first:

```powershell
.\.venv\Scripts\python.exe scripts\prune_postgres_data.py --older-than-days 30 --schema public
```

Only add `--apply` after reviewing the candidate count. The cleanup only
removes `sul_*` records belonging to old runs in the named schema. It never
prints connection secrets and never deletes unrelated application tables.

Artifacts are local files. Put the `artifacts/` directory on encrypted storage
and apply your organization’s backup/deletion policy; do not place customer
credentials, raw prompts, or production data in test goals.

## Production checklist

- Use a dedicated PostgreSQL role and database/schema for Synthetic User Lab.
- Allow browser navigation only to a staging hostname you control.
- Store Groq, Qdrant, Langfuse, and dashboard secrets in the deployment secret manager.
- Enable HTTPS and `SUL_DASHBOARD_COOKIE_SECURE=true`.
- Restrict `/health` and `/metrics` at the network layer if they reveal more than your monitoring system should see.
- Schedule the retention preview and an approved `--apply` run.
- Run the restart-recovery and two-persona integration proofs before each release.
