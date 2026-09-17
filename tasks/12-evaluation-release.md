# Task 12 — Evaluation, integration, and reproducible release

Depends on: 01–11. Own: scripts/, tests/e2e/, tests/evaluation/, docs/evaluation.md, docs/runbook.md. Coordinate shared dependency changes with the foundation owner.

Build a reproducible evaluation runner for healthy/faulty variants of all four scenarios, multiple repetitions, and memory-enabled/disabled arms. Hold model, budgets, fixtures, and verifier expectations constant. Persist raw outcomes and aggregate results including inconclusive and failed runs.

Measure triggered fault detection, healthy false positives, task completion, replay success, invalid actions, latency, model tokens, queue time, and memory retrieval behavior. Include restart, isolation, stale memory, malformed output, and unavailable-model cases. Clearly distinguish deterministic scripted-model integration tests from measured local-Qwen behavior.

Provide a clean local setup path with prerequisite checks for Oracle, model server/model availability, embeddings, and Playwright browser. Avoid automatic destructive resets. Add a short recruiter demo script: initial visit, simulated time jump, memory retrieval, violating action, verifier evidence, report, isolated replay, and healthy comparison.

Acceptance: full deterministic suite passes; a real Oracle/local-model run is executed when infrastructure is available; otherwise exact blocked checks are documented. Publish actual measured results without invented percentages or claims of memory improvement. Record model tag, quantization if known, hardware, context/concurrency settings, versions, dataset/scenario revision, and evaluation seed. Review implementation against architecture and document any deliberate deviation.
