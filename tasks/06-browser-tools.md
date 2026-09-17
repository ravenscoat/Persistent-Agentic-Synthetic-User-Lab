# Task 06 — Browser execution and evidence

Depends on: 01,02. Own: src/synthetic_lab/browser/, tests/browser/.

Implement Playwright browser sessions per persona, cookie/storage persistence, compact page observations, observation-scoped element IDs, and the allowed browser tools in CONTRACTS.md. Support fresh observation after navigation and reject stale element identifiers.

Enforce origin allowlisting including redirects. Keep fixture/admin endpoints out of tools. Validate every action against registered schemas and per-persona permissions. Bound visible page text and save screenshots/traces on important transitions and failures. Artifact writes stay within the configured root and produce hashes.

Acceptance: two personas cannot share login state; stale IDs fail safely; unauthorized tool names/origins are rejected; form input and navigation work on the demo app; error results preserve evidence; browser state can be saved and restored. Tool failures return typed outcomes rather than fabricated success. Integrate artifact metadata through the repository protocol without owning storage internals.
