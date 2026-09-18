import runpy
from pathlib import Path

import pytest

runner = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "run_persona_campaign.py"))


def test_failed_agent_cannot_pass_on_verifier_confirmation():
    row = dict(leased=True, agent={"status": "failed"}, verdict="confirmed",
               expected_verdict="confirmed", event_sequences_unique=True,
               fault="duplicate_charge", replay="reproduced")
    assert not runner["case_passed"](row)
    row["agent"]["status"] = "completed"
    assert runner["case_passed"](row)
    row["replay"] = "not_reproduced"
    assert not runner["case_passed"](row)


@pytest.mark.asyncio
async def test_return_context_uses_observed_route_to_stop_navigation():
    from datetime import datetime, timezone
    from synthetic_lab.contracts import BudgetConfig, Observation, PersonaRecord, SessionRecord
    from synthetic_lab.storage import InMemoryMemoryRepository
    now = datetime.now(timezone.utc)
    persona = PersonaRecord(id="p", run_id="r", kind="customer", goal="Inspect billing.", application_account_id="a")
    session = SessionRecord(id="s", run_id="r", persona_id="p", phase="return", due_business_time=now)
    observation = Observation(id="o", run_id="r", session_id="s", url="http://demo/", captured_at=now)
    context = runner["ReturnVisitContext"](InMemoryMemoryRepository(), route="/billing", tool_registry=None)
    before = await context.build(persona, session, observation, BudgetConfig())
    assert 'arguments={"url":"/billing"}' in before.messages[1]["content"]
    after = await context.build(persona, session, observation.model_copy(update={"url": "http://demo/billing"}), BudgetConfig())
    assert 'kind="finish"' in after.messages[1]["content"]
    assert "No more navigation" in after.messages[1]["content"]
