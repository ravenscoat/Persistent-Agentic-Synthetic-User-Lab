# Task 02 — Controlled subscription application

Depends on: 01. Own: src/synthetic_lab/demo/, scenarios/specs/, tests/demo/.

Build an accessible local FastAPI web application with signup/login, trial access, plan selection, simulated purchase ledger, workspace membership, ownership transfer, and resumable onboarding. SQLite is its isolated business database. Use stable accessible names and visible controls suitable for Playwright.

Implement a fixture controller isolated from persona-facing routes. It creates a fresh database per run, seeds accounts, configures faults, advances business time, and exports read-only inspection snapshots. Wall time must not determine trial behavior.

Implement healthy and faulty variants of the four scenarios in ARCHITECTURE.md. Prioritize trial expiry and duplicate purchase. Duplicate purchases must reference the same operation identifier; two legitimate purchases are not a bug. Ownership permissions must be enforced server-side and checked through real endpoints.

Acceptance: deterministic tests distinguish healthy/faulty behavior for each invariant; cross-run data is isolated; advancing to day six reveals early-expiry fault but healthy trial remains active; repeated purchase operation yields one charge when healthy and two when faulty; fault configuration is absent from ordinary UI/API observations. Include manual demo instructions and test credentials for generated fixtures only.
