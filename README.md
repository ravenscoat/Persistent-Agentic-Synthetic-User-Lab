# Persistent Synthetic User Lab

A local application-testing system whose synthetic users remember earlier sessions, return later, and uncover failures spanning multiple visits.

Example: a user starts a seven-day trial, returns on day six, and discovers that access has already expired. The system preserves the browser actions and database evidence needed to reproduce the failure.

Status: architecture and implementation task specifications only. No application code has been implemented or benchmarked.

Start with [ARCHITECTURE.md](ARCHITECTURE.md), then [CONTRACTS.md](CONTRACTS.md), and assign work from [TASKS.md](TASKS.md). Each task has a separate handoff prompt under [tasks](tasks/).

The default design uses a single locally served Qwen3-8B model. External model APIs are optional future adapters. Oracle is the durable memory store; browser execution uses Playwright. A separate SQLite database holds the controlled application's business state.
