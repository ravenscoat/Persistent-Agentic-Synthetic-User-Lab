from __future__ import annotations

from synthetic_lab.demo.scenarios import SCENARIOS
from synthetic_lab.demo.store import DemoStore
from synthetic_lab.verification.invariants import DemoVerificationContext, DemoVerifier

FAULTS = {"trial_return": (None, "trial_expires_day_5"), "payment_retry": (None, "duplicate_charge"), "ownership_transfer": (None, "owner_transfer_leak"), "interrupted_onboarding": (None, "onboarding_resets"), "stale_task_status": (None, "task_completion_stale")}


async def run_case(scenario_id: str, fault: str | None) -> dict[str, object]:
    store = DemoStore(fault=fault)
    account_id = "acct-eval"
    store.create_account(account_id, "eval@example.test", "not-a-real-password")
    operation_id = "purchase-1" if scenario_id == "payment_retry" else None
    try:
        result = SCENARIOS[scenario_id](store, account_id, **({"operation_id": operation_id} if operation_id else {}))
        verified = await DemoVerifier().check(result.invariant_id, DemoVerificationContext(store, account_id, operation_id))
        expected_verdict = "confirmed" if fault else "satisfied"
        return {"scenario_id": scenario_id, "fault": fault, "verdict": verified.verdict, "expected_verdict": expected_verdict, "expected": verified.expected, "actual": verified.actual, "detected": verified.verdict == expected_verdict, "healthy_false_positive": fault is None and verified.verdict != "satisfied"}
    finally:
        store.close()


async def run_suite() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scenario_id, (healthy, fault) in FAULTS.items():
        rows.append(await run_case(scenario_id, healthy))
        rows.append(await run_case(scenario_id, fault))
    return rows
