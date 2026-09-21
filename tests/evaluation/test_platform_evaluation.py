import asyncio
from datetime import datetime, timezone

from synthetic_lab.contracts import Event, RunRecord, RunStatus
from synthetic_lab.evaluation import EvaluationExecutor, summarize_evaluation
from synthetic_lab.storage import InMemoryStateRepository


def test_evaluation_runs_twenty_child_runs_and_splits_memory_arms() -> None:
    async def check() -> None:
        state = InMemoryStateRepository()
        now = datetime.now(timezone.utc)
        campaign = RunRecord(
            id="campaign", scenario_id="evaluation:trial_return", created_at=now,
            business_time=now, status=RunStatus.RUNNING,
            config_snapshot={"evaluation": {"scenario_id": "trial_return", "trials": 20, "expect_fault": True, "run_config": {}}},
        )
        await state.create_run(campaign)

        async def child_executor(run: RunRecord) -> None:
            await state.append_event(Event(
                id=f"finished-{run.id}", run_id=run.id,
                sequence=await state.reserve_event_sequences(run.id), kind="session_finished",
                wall_time=now, business_time=now,
                payload={"input_tokens": 10, "output_tokens": 2},
            ))

        await EvaluationExecutor(state, child_executor)(campaign)
        events = await state.list_events(campaign.id, limit=1000)
        result = summarize_evaluation(campaign, events)
        assert result["completed_trials"] == 20
        assert result["arms"]["memory_off"]["trials"] == 10
        assert result["arms"]["memory_on"]["trials"] == 10
        assert result["arms"]["memory_on"]["input_tokens"] == 100
        children = [run for run in await state.list_runs(limit=100) if run.id != campaign.id]
        assert all(run.status is RunStatus.COMPLETED for run in children)

    asyncio.run(check())
