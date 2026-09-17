from datetime import datetime, timezone

import pytest

from synthetic_lab.browser.tools import BrowserToolRegistry, TOOL_SCHEMAS
from synthetic_lab.contracts import Action, ActionStatus, PersonaRecord, ToolResult


class FakeSession:
    def __init__(self) -> None:
        self.actions: list[Action] = []

    async def execute(self, action: Action) -> ToolResult:
        self.actions.append(action)
        return ToolResult(action_id=action.id, status=ActionStatus.SUCCESS, data={"ok": True}, observed_at=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_registry_exposes_only_allowed_tools_and_dispatches() -> None:
    fake = FakeSession()
    registry = BrowserToolRegistry({"p1": fake})
    persona = PersonaRecord(id="p1", run_id="r1", kind="new_customer", goal="test", application_account_id="a1", allowed_tool_names=["click", "fill"])
    assert [schema["name"] for schema in registry.list_allowed(persona)] == ["click", "fill"]
    result = await registry.dispatch(persona, Action(id="a1", tool_name="click", arguments={"element_id": "e1"}, observation_id="o1"))
    assert result.status is ActionStatus.SUCCESS
    assert fake.actions[0].tool_name == "click"


@pytest.mark.asyncio
async def test_registry_rejects_unauthorized_tool() -> None:
    registry = BrowserToolRegistry({"p1": FakeSession()})
    persona = PersonaRecord(id="p1", run_id="r1", kind="new_customer", goal="test", application_account_id="a1", allowed_tool_names=["observe_page"])
    result = await registry.dispatch(persona, Action(id="a1", tool_name="navigate", arguments={"url": "/admin"}))
    assert result.error_code == "unauthorized_tool"


def test_tool_schemas_are_closed() -> None:
    assert TOOL_SCHEMAS["fill"]["parameters"]["additionalProperties"] is False
