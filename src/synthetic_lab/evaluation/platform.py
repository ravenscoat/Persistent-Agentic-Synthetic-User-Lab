"""Persistent evaluation campaigns composed of ordinary agent runs."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from synthetic_lab.contracts import Event, FindingStatus, RunRecord, RunStatus


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)
    trials: int = Field(default=20, ge=20, le=50)
    expect_fault: bool = True
    run_config: dict[str, Any] = Field(default_factory=dict)


class EvaluationExecutor:
    """Run a bounded memory A/B campaign and persist every trial as a child run."""

    def __init__(self, state: Any, child_executor: Any) -> None:
        self.state = state
        self.child_executor = child_executor

    async def __call__(self, campaign: RunRecord) -> None:
        config = campaign.config_snapshot["evaluation"]
        trials = int(config["trials"])
        for index in range(trials):
            memory_enabled = index % 2 == 1
            child_config = deepcopy(config.get("run_config") or {})
            child_config.update({"memory_enabled": memory_enabled, "evaluation_id": campaign.id, "trial_index": index, "trace_enabled": False})
            child = RunRecord(
                id=str(uuid4()), scenario_id=str(config["scenario_id"]),
                created_at=datetime.now(timezone.utc), business_time=campaign.business_time,
                config_snapshot=child_config,
            )
            await self.state.create_run(child)
            await self.state.transition_run(child.id, RunStatus.RUNNING)
            started = perf_counter()
            error = None
            try:
                await self.child_executor(child.model_copy(update={"status": RunStatus.RUNNING}))
                current = await self.state.get_run(child.id)
                if current.status is RunStatus.RUNNING:
                    await self.state.transition_run(child.id, RunStatus.COMPLETED)
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                current = await self.state.get_run(child.id)
                if current.status is RunStatus.RUNNING:
                    await self.state.transition_run(child.id, RunStatus.FAILED)
            elapsed_ms = round((perf_counter() - started) * 1000, 2)
            events = await self.state.list_events(child.id, limit=1000)
            findings = await self.state.list_findings(child.id)
            finished = [event for event in events if event.kind == "session_finished"]
            token_events = finished or [event for event in events if event.kind == "tool_result"]
            input_tokens = sum(int(event.payload.get("input_tokens") or 0) for event in token_events)
            output_tokens = sum(int(event.payload.get("output_tokens") or 0) for event in token_events)
            confirmed = any(finding.status is FindingStatus.CONFIRMED for finding in findings)
            completed = error is None and bool(finished)
            await self.state.append_event(Event(
                id=str(uuid4()), run_id=campaign.id,
                sequence=await self.state.reserve_event_sequences(campaign.id),
                kind="evaluation_trial_completed",
                wall_time=datetime.now(timezone.utc), business_time=campaign.business_time,
                payload={
                    "trial_index": index, "child_run_id": child.id, "memory_enabled": memory_enabled,
                    "completed": completed, "discovered": confirmed,
                    "false_positive": confirmed and not bool(config["expect_fault"]),
                    "latency_ms": elapsed_ms, "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "estimated_cost_usd": 0.0, "pricing_source": "local_model", "error": error,
                },
            ))


def summarize_evaluation(campaign: RunRecord, events: list[Event]) -> dict[str, Any]:
    trials = [event.payload for event in events if event.kind == "evaluation_trial_completed"]
    arms: dict[str, dict[str, Any]] = {}
    for enabled in (False, True):
        rows = [row for row in trials if bool(row.get("memory_enabled")) is enabled]
        count = len(rows)
        key = "memory_on" if enabled else "memory_off"
        arms[key] = {
            "trials": count,
            "completion_rate": sum(bool(row.get("completed")) for row in rows) / count if count else 0,
            "discovery_rate": sum(bool(row.get("discovered")) for row in rows) / count if count else 0,
            "false_positive_rate": sum(bool(row.get("false_positive")) for row in rows) / count if count else 0,
            "mean_latency_ms": round(sum(float(row.get("latency_ms") or 0) for row in rows) / count, 2) if count else 0,
            "input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
            "output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
            "estimated_cost_usd": round(sum(float(row.get("estimated_cost_usd") or 0) for row in rows), 6),
        }
    return {
        "id": campaign.id, "scenario_id": campaign.config_snapshot["evaluation"]["scenario_id"],
        "status": campaign.status.value, "requested_trials": campaign.config_snapshot["evaluation"]["trials"],
        "completed_trials": len(trials), "arms": arms,
    }
