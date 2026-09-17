from __future__ import annotations

from synthetic_lab.contracts import Finding, ReplayResult, ReplayStatus
from synthetic_lab.demo.scenarios import SCENARIOS
from synthetic_lab.demo.store import DemoStore

from .invariants import DemoVerificationContext, DemoVerifier


class DemoReplayService:
    """Replay a finding against a fresh controlled demo state."""

    async def replay(self, finding: Finding, *, fault: str | None) -> ReplayResult:
        scenario_id = finding.invariant_id
        scenario_by_invariant = {
            "trial_access_seven_days": "trial_return",
            "purchase_idempotency": "payment_retry",
            "ownership_transfer": "ownership_transfer",
            "onboarding_persistence": "interrupted_onboarding",
        }
        scenario_id = scenario_by_invariant.get(scenario_id, scenario_id)
        scenario = SCENARIOS.get(scenario_id)
        if scenario is None:
            return ReplayResult(status=ReplayStatus.NOT_ATTEMPTED, reason=f"unknown scenario: {scenario_id}")
        store = DemoStore(fault=fault)
        account_id = "acct-replay"
        store.create_account(account_id, "replay@example.test", "not-a-real-password")
        operation_id = "purchase-1" if scenario_id == "payment_retry" else None
        try:
            result = scenario(store, account_id, **({"operation_id": operation_id} if operation_id else {}))
            verified = await DemoVerifier().check(result.invariant_id, DemoVerificationContext(store, account_id, operation_id))
            status = ReplayStatus.REPRODUCED if verified.verdict == "confirmed" else ReplayStatus.NOT_REPRODUCED
            return ReplayResult(status=status, reason=f"verifier verdict: {verified.verdict}")
        finally:
            store.close()
