from datetime import datetime, timezone

import pytest

from synthetic_lab.contracts import Action, Element, Observation
from synthetic_lab.runtime.agent import PersonaAgent


def test_model_target_alias_resolves_to_current_element_and_observation() -> None:
    now = datetime.now(timezone.utc)
    observation = Observation(id="obs-current", run_id="r", session_id="s", url="http://demo", captured_at=now, elements=[Element(id="real-id", role="textbox", name="Email", allowed_actions=["fill"])])
    action = Action(id="a1", tool_name="fill", arguments={"target": "e1", "value": "a@example.test"})
    resolved = PersonaAgent._resolve_model_target(action, observation)
    assert resolved.arguments["element_id"] == "real-id"
    assert resolved.observation_id == "obs-current"


def test_unknown_model_target_alias_is_rejected() -> None:
    now = datetime.now(timezone.utc)
    observation = Observation(id="obs-current", run_id="r", session_id="s", url="http://demo", captured_at=now, elements=[])
    with pytest.raises(ValueError, match="target alias"):
        PersonaAgent._resolve_model_target(Action(id="a1", tool_name="click", arguments={"target": "e9"}), observation)
