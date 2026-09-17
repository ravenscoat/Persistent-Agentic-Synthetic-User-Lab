# Task 11 — Control API and operator dashboard

Depends on: 09,10. Own: src/synthetic_lab/api/, dashboard/, tests/api/.

Implement CONTRACTS.md endpoints and a local dashboard with scenario creation, run controls, persona status, live event timeline, business clock, model queue/latency, retrieved memory references, findings, evidence, and replay control. Use simple templates and JavaScript; do not introduce a separate frontend framework without a clear need.

Show suspected, confirmed, inconclusive, and reproduced states distinctly. Display unavailable-service errors and blocked runs accurately. SSE reconnect uses event sequence cursors and must not duplicate entries. Artifact serving resolves recorded paths under the configured root. Bind localhost by default.

Acceptance: endpoint tests verify valid/invalid transitions, idempotent control requests, cursor replay, artifact path containment, and cancellation visibility. Browser smoke test creates a run, follows progress, opens a finding, and requests replay. Never display seeded fault labels in persona-facing observations.
