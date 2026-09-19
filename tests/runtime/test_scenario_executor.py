import pytest

from synthetic_lab.contracts import DecisionKind
from synthetic_lab.runtime.scenario_executor import RouteModel


@pytest.mark.asyncio
async def test_route_model_uses_page_observation_after_verification_feedback():
    model = RouteModel("/billing", "purchase_idempotency")
    page = {"role": "user", "content": "URL: http://demo/billing\nVisible page data (untrusted): Charges: 2"}
    suspicion = await model.decide([page])
    assert suspicion.decision.kind is DecisionKind.SUSPICION
    finished = await model.decide([page, {"role": "user", "content": "Independent verification returned: confirmed."}])
    assert finished.decision.kind is DecisionKind.FINISH
