"""Typed definitions for the real-model persona scenario matrix."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaScenario:
    """One returned persona and the invariant its visit makes observable."""

    scenario_id: str
    persona_kind: str
    fault: str
    invariant_id: str
    return_route: str
    goal: str


PERSONA_SCENARIOS: tuple[PersonaScenario, ...] = (
    PersonaScenario(
        scenario_id="trial_return",
        persona_kind="new_customer",
        fault="trial_expires_day_5",
        invariant_id="trial_access_seven_days",
        return_route="/dashboard",
        goal="Return on day six and inspect the dashboard trial status. Finish only after the dashboard is visible.",
    ),
    PersonaScenario(
        scenario_id="payment_retry",
        persona_kind="power_user",
        fault="duplicate_charge",
        invariant_id="purchase_idempotency",
        return_route="/billing",
        goal="Return to Billing and inspect the recorded charge count. Finish only after Billing is visible.",
    ),
    PersonaScenario(
        scenario_id="ownership_transfer",
        persona_kind="administrator",
        fault="owner_transfer_leak",
        invariant_id="ownership_transfer",
        return_route="/api/owner-only",
        goal="Return after ownership transfer and inspect former-owner access at /api/owner-only. Finish only after that endpoint is visible.",
    ),
    PersonaScenario(
        scenario_id="interrupted_onboarding",
        persona_kind="interrupted_user",
        fault="onboarding_resets",
        invariant_id="onboarding_persistence",
        return_route="/dashboard",
        goal="Return after completing onboarding and inspect the persisted onboarding step on the dashboard. Finish only after the dashboard is visible.",
    ),
    PersonaScenario(
        scenario_id="stale_task_status",
        persona_kind="project_contributor",
        fault="task_completion_stale",
        invariant_id="task_completion",
        return_route="/tasks",
        goal="Return after completing a task and inspect whether task-1 is shown as completed on the Tasks page.",
    ),
)


def scenario_by_id(scenario_id: str) -> PersonaScenario:
    """Return a campaign scenario or raise a clear configuration error."""
    for scenario in PERSONA_SCENARIOS:
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(f"unknown persona scenario: {scenario_id}")
