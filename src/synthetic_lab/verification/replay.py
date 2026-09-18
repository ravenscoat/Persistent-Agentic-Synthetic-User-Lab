from __future__ import annotations

from synthetic_lab.contracts import Finding, ReplayResult, ReplayStatus
from synthetic_lab.demo.scenarios import SCENARIOS
from synthetic_lab.demo.store import DemoStore

from .invariants import DemoVerificationContext, DemoVerifier


class DemoReplayService:
    """Replay a finding against a fresh controlled demo state."""

    async def replay(self, finding: Finding, *, fault: str | None) -> ReplayResult:
        if finding.invariant_id == "task_completion":
            return await self._replay_task_completion(fault)
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

    @staticmethod
    async def _replay_task_completion(fault: str | None) -> ReplayResult:
        """Fresh deterministic replay for the browser workflow's task invariant.

        This replays the business operation, not a browser trace. Its result is
        therefore explicitly an invariant reproduction check, while the
        original finding retains the browser-event evidence.
        """
        store = DemoStore(fault=fault)
        try:
            store.create_account("acct-replay", "replay@example.test", "not-a-real-password")
            store.create_project("acct-replay", "project-1", "Payments migration")
            store.create_task("project-1", "task-1", "Verify payment retry")
            store.complete_task("task-1")
            actual = store.workflow_snapshot()["task"]
            status = ReplayStatus.REPRODUCED if actual != "completed" else ReplayStatus.NOT_REPRODUCED
            return ReplayResult(status=status, reason=f"task completion replay actual={actual}")
        finally:
            store.close()
