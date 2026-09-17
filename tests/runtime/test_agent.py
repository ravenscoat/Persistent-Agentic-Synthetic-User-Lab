from datetime import datetime, timezone

import pytest

from synthetic_lab.contracts import Action, AgentDecision, BudgetConfig, DecisionKind, Observation, PersonaRecord, RunRecord, SessionRecord, SessionStatus, ToolResult, ActionStatus
from synthetic_lab.memory import MemoryContextAssembler
from synthetic_lab.runtime import PersonaAgent
from synthetic_lab.storage import InMemoryMemoryRepository, InMemoryStateRepository


class ScriptedModel:
    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = iter(decisions)

    async def decide(self, messages, decision_schema=None, generation_options=None):
        from synthetic_lab.contracts import ModelResponse
        return ModelResponse(decision=next(self.decisions), latency_ms=1, model_id="fake")


class FakeTools:
    async def dispatch(self, persona, action):
        return ToolResult(action_id=action.id, status=ActionStatus.SUCCESS, data={"ok": True}, observed_at=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_agent_checkpoints_tool_and_finish() -> None:
    now = datetime.now(timezone.utc)
    state = InMemoryStateRepository()
    await state.create_run(RunRecord(id="r1", scenario_id="trial_return", created_at=now, business_time=now))
    session = SessionRecord(id="s1", run_id="r1", persona_id="p1", phase="start", due_business_time=now)
    await state.enqueue_session(session)
    persona = PersonaRecord(id="p1", run_id="r1", kind="new_customer", goal="test", application_account_id="a1", allowed_tool_names=["observe_page"])
    observation = Observation(id="o1", run_id="r1", session_id="s1", url="http://demo/", captured_at=now)
    decisions = [AgentDecision(kind=DecisionKind.ACTION, action=Action(id="a1", tool_name="observe_page")), AgentDecision(kind=DecisionKind.FINISH, summary="done")]
    memory = InMemoryMemoryRepository()
    agent = PersonaAgent(model=ScriptedModel(decisions), context=MemoryContextAssembler(memory), tools=FakeTools(), state=state, memory=memory, budgets=BudgetConfig(max_steps=4))
    result = await agent.run(persona, session, observation)
    assert result.status == "completed"
    assert (await state.get_run("r1")).status.value == "CREATED"
    assert (await state.list_events("r1"))
    assert state.sessions["s1"].status is SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_agent_stops_at_step_budget() -> None:
    now = datetime.now(timezone.utc)
    state = InMemoryStateRepository()
    await state.create_run(RunRecord(id="r1", scenario_id="trial_return", created_at=now, business_time=now))
    session = SessionRecord(id="s1", run_id="r1", persona_id="p1", phase="start", due_business_time=now)
    await state.enqueue_session(session)
    persona = PersonaRecord(id="p1", run_id="r1", kind="new_customer", goal="test", application_account_id="a1", allowed_tool_names=["observe_page"])
    observation = Observation(id="o1", run_id="r1", session_id="s1", url="http://demo/", captured_at=now)
    action = AgentDecision(kind=DecisionKind.ACTION, action=Action(id="a1", tool_name="observe_page"))
    memory = InMemoryMemoryRepository()
    agent = PersonaAgent(model=ScriptedModel([action, action]), context=MemoryContextAssembler(memory), tools=FakeTools(), state=state, memory=memory, budgets=BudgetConfig(max_steps=1))
    result = await agent.run(persona, session, observation)
    assert result.reason == "step_budget_exhausted"
