from __future__ import annotations

from dataclasses import dataclass

from synthetic_lab.contracts import VerificationResult
from synthetic_lab.demo.store import DemoStore


@dataclass(frozen=True)
class DemoVerificationContext:
    store: DemoStore
    account_id: str
    operation_id: str | None = None


class DemoVerifier:
    """Checks business state directly, without model claims or fault flags."""

    version = "demo-verifier-v1"

    async def check(self, invariant_id: str, verification_context: DemoVerificationContext) -> VerificationResult:
        store = verification_context.store
        if invariant_id == "trial_access_seven_days":
            actual = store.trial_active(verification_context.account_id)
            return VerificationResult(verdict="satisfied" if actual else "confirmed", expected=True, actual=actual, evidence_ids=[])
        if invariant_id == "purchase_idempotency":
            if not verification_context.operation_id:
                return VerificationResult(verdict="inconclusive", expected=1, actual=None, evidence_ids=[])
            actual = store.ledger_for(verification_context.operation_id).charges
            return VerificationResult(verdict="satisfied" if actual == 1 else "confirmed", expected=1, actual=actual, evidence_ids=[])
        if invariant_id == "ownership_transfer":
            actual = store.role(verification_context.account_id, "old-owner")
            return VerificationResult(verdict="satisfied" if actual == "member" else "confirmed", expected="member", actual=actual, evidence_ids=[])
        if invariant_id == "onboarding_persistence":
            actual = store.account(verification_context.account_id)["onboarding_step"]
            return VerificationResult(verdict="satisfied" if actual == 2 else "confirmed", expected=2, actual=actual, evidence_ids=[])
        if invariant_id == "task_completion":
            actual = store.task_status("task-1")
            return VerificationResult(verdict="satisfied" if actual == "completed" else "confirmed", expected="completed", actual=actual, evidence_ids=[])
        return VerificationResult(verdict="inconclusive", expected=None, actual=None, evidence_ids=[])
