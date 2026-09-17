# Task 01 — Foundation and shared contracts

Dependencies: none. Read ARCHITECTURE.md and CONTRACTS.md completely.

Own: pyproject.toml, lockfile, .gitignore, .env.example, src/synthetic_lab/contracts/, config.py, tests/contracts/, docs/setup.md.

Implement the package skeleton, Pydantic data models, enums, async protocols, configuration validation, logging setup, and deterministic fake implementations for downstream contract tests. Define budgets for actions, elapsed time, model requests, retries, and context. Configuration includes Oracle DSN/user/password, artifact root, model endpoint/tag, browser origin, and embedding settings. Store no secrets in example values.

Provide documented install/test commands and dependency groups for browser, Oracle, embeddings, and local inference client. Add ignores for secrets, model weights, databases, artifacts, and browser state. No model download or Oracle provisioning is required for unit tests.

Acceptance: imports work; decision discriminators reject inconsistent fields; invalid transitions and budgets fail; fake protocols support minimal run creation and action recording; ordinary unit tests need no network/model/database. Document exact dependency versions selected and unresolved service prerequisites.

Handoff: publish the finalized signatures and example serialized records so later agents can build against them. Do not implement the agent loop.
