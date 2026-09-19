import asyncio
from datetime import datetime, timezone

from synthetic_lab.config import Settings
from synthetic_lab.contracts import RunRecord
from synthetic_lab.runtime.scenario_executor import ScenarioExecutor
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository, InMemoryStateRepository


async def main() -> None:
    state = InMemoryStateRepository()
    memory = InMemoryMemoryRepository()
    now = datetime.now(timezone.utc)
    cases = {
        "trial_return": "trial_expires_day_5",
        "payment_retry": "duplicate_charge",
        "ownership_transfer": "owner_transfer_leak",
        "interrupted_onboarding": "onboarding_resets",
        "stale_task_status": "task_completion_stale",
    }
    results = {"faulted": {}, "healthy": {}}
    for arm, use_fault in (("faulted", True), ("healthy", False)):
        for index, (scenario_id, fault) in enumerate(cases.items()):
            run = RunRecord(
                id=f"scenario-check-{arm}-{index}",
                scenario_id=scenario_id,
                created_at=now,
                business_time=now,
                config_snapshot={"fault": fault if use_fault else None},
            )
            await state.create_run(run)
            await state.transition_run(run.id, "RUNNING")
            await ScenarioExecutor(state, memory, Settings(model_name="smoke", business_fault=None))(run)
            findings = await state.list_findings(run.id)
            events = await state.list_events(run.id, limit=100)
            suspicions = [event for event in events if event.kind == "agent_suspicion"]
            verifications = [event for event in events if event.kind == "verification_completed"]
            results[arm][scenario_id] = {
                "suspicions": len(suspicions),
                "verdicts": [event.payload["verdict"] for event in verifications],
                "findings": len(findings),
                "status": findings[0].status.value if findings else "none",
            }
    print(results)


if __name__ == "__main__":
    asyncio.run(main())
