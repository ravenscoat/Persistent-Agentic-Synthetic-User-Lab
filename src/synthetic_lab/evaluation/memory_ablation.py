"""Matched, deterministic memory-on versus memory-off evaluation."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from synthetic_lab.contracts import BudgetConfig, MemoryRecord, MemoryType, Observation, PersonaRecord, SessionRecord, Trust
from synthetic_lab.demo.store import DemoStore
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.storage import InMemoryMemoryRepository
from synthetic_lab.verification.invariants import DemoVerificationContext, DemoVerifier


async def run_memory_trial(*, memory_enabled: bool, fault: str | None) -> dict[str, object]:
    """Run one arm of the same two-visit trial-return task."""
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    repository = InMemoryMemoryRepository()
    store = DemoStore(fault=fault)
    run_id, persona_id, account_id = "memory-eval", "returning-user", "acct-memory-eval"
    try:
        store.create_account(account_id, "memory-eval@example.test", "not-a-real-password")
        memory_id = "trial-expectation"
        if memory_enabled:
            await repository.append(MemoryRecord(id=memory_id, run_id=run_id, persona_id=persona_id, type=MemoryType.WORKFLOW, text="First visit: trial started today. On a return visit at day 6, the account should still have trial access. Inspect the trial status and report a suspicion only if it is inactive.", trust=Trust.VERIFIED, valid_from=now))
        store.advance_days(6)
        actual_active = store.trial_active(account_id)
        observation = Observation(id="return-observation", run_id=run_id, session_id="return-session", url="http://demo/dashboard", title="Account dashboard", visible_text=f"Trial active: {str(actual_active).lower()}", captured_at=now)
        persona = PersonaRecord(id=persona_id, run_id=run_id, kind="returning_customer", goal="Return on day 6 and check whether the trial remains active.", application_account_id=account_id)
        session = SessionRecord(id="return-session", run_id=run_id, persona_id=persona_id, phase="trial_return", due_business_time=now)
        bundle = await MemoryContextAssembler(repository).build(persona, session, observation, BudgetConfig(context_tokens=1200, output_tokens=128, safety_tokens=128))
        recalled = memory_id in bundle.included_memory_ids
        # This deterministic policy isolates retrieval from model variance. It
        # may compare expected versus observed status only when the previous
        # visit's expectation reached its current context.
        agent_suspicion = recalled and not actual_active
        verified = await DemoVerifier().check("trial_access_seven_days", DemoVerificationContext(store, account_id))
        return {"memory_enabled": memory_enabled, "fault": fault, "recalled_expected_memory": recalled, "agent_completed_return_inspection": recalled, "agent_suspicion": agent_suspicion, "verifier_verdict": verified.verdict, "confirmed_finding": verified.verdict == "confirmed", "healthy_false_positive": fault is None and agent_suspicion, "included_memory_count": len(bundle.included_memory_ids), "context_tokens": bundle.estimated_tokens, "duration_ms": round((time.monotonic() - started) * 1000, 2)}
    finally:
        store.close()


async def run_memory_ablation(trials: int = 3) -> dict[str, object]:
    if trials <= 0:
        raise ValueError("trials must be positive")
    rows: list[dict[str, object]] = []
    for memory_enabled in (False, True):
        for fault in (None, "trial_expires_day_5"):
            for _ in range(trials):
                rows.append(await run_memory_trial(memory_enabled=memory_enabled, fault=fault))

    def aggregate(enabled: bool) -> dict[str, object]:
        arm = [row for row in rows if row["memory_enabled"] is enabled]
        fault_rows = [row for row in arm if row["fault"]]
        healthy_rows = [row for row in arm if not row["fault"]]
        return {"trials": len(arm), "return_inspection_rate": sum(bool(row["agent_completed_return_inspection"]) for row in arm) / len(arm), "fault_suspicion_rate": sum(bool(row["agent_suspicion"]) for row in fault_rows) / len(fault_rows), "verifier_confirmation_rate": sum(bool(row["confirmed_finding"]) for row in fault_rows) / len(fault_rows), "healthy_false_positive_rate": sum(bool(row["healthy_false_positive"]) for row in healthy_rows) / len(healthy_rows), "mean_context_tokens": round(sum(float(row["context_tokens"]) for row in arm) / len(arm), 2), "mean_duration_ms": round(sum(float(row["duration_ms"]) for row in arm) / len(arm), 2)}

    return {"evaluation": "deterministic_two_visit_memory_ablation", "trials_per_fault_per_arm": trials, "arms": {"memory_off": aggregate(False), "memory_on": aggregate(True)}, "rows": rows, "limitations": ["Uses a deterministic policy to isolate context-retrieval behavior; it is not a Qwen benchmark.", "The independent verifier checks the business invariant in both arms, so verifier confirmation does not measure model discovery."]}
