# Task 10 — Evidence reports and isolated replay

Depends on: 06,07,08. Own: src/synthetic_lab/reporting/, src/synthetic_lab/verification/replay.py, tests/reporting/.

Build a deterministic report from findings, trusted expected behavior, event history, and artifacts. Optional model-written prose must remain separate from verdict and evidence. Include run configuration, application version, simulated timeline, action inputs, outcome uncertainty, and replay status.

Replay recorded browser actions against fresh scenario state with matching initial configuration and clock phases. Rebind semantic element references to new observations; do not reuse stale element IDs. Never modify the original run or mark unattempted replay as successful.

Acceptance: seeded trial and duplicate-charge findings replay on faulty versions; fixed versions do not reproduce; missing artifacts remain explicit; duplicate findings consolidate without losing evidence; generated reports link to verified artifact IDs; hashes detect modified artifacts. A replay failure is reported honestly, with its stopping point.
