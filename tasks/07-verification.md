# Task 07 — Independent invariant verification

Depends on: 01,02. Own: src/synthetic_lab/verification/checks/, tests/verification/.

Implement checkers for trial entitlement, purchase idempotency, ownership authorization, and onboarding persistence. Expected behavior comes from trusted scenario specifications; actual behavior comes from independent business state and real permission endpoints. Do not read fault flags or model conclusions to decide verdicts.

Return confirmed, satisfied, or inconclusive outcomes mapped into the shared result contract; request any missing contract field through Task 01 coordination. Include evidence for the exact violating state. A later corrupted state must not be attributed to an earlier unrelated action.

Acceptance: healthy variants generate no confirmed bug; each triggered fault produces the correct invariant failure; untriggered faults are not counted as discoveries; legitimate repeated purchases with different operation IDs are allowed; missing evidence yields inconclusive. Verify old and new owner identities through protected operations without relying solely on displayed role labels.
