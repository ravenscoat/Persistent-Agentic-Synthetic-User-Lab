import asyncio
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
@pytest.mark.parametrize("repeat_fill", [True, False])
async def test_progress_repairs_are_bounded_and_do_not_repeat_writes(repeat_fill):
    from synthetic_lab.contracts import Element
    now = datetime.now(timezone.utc)
    state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
    await state.create_run(RunRecord(id="r", scenario_id="signup", created_at=now, business_time=now))
    session = SessionRecord(id="s", run_id="r", persona_id="p", phase="signup", due_business_time=now)
    await state.enqueue_session(session)
    persona = PersonaRecord(id="p", run_id="r", kind="test", goal="signup", application_account_id="a")
    observation = Observation(id="o", run_id="r", session_id="s", url="http://demo/signup", captured_at=now, elements=[Element(id="email", role="textbox", name="Email", allowed_actions=["fill"])])
    decision = AgentDecision(kind="action", action=Action(id="a", tool_name="fill", arguments={"target": "e1", "value": "example"})) if repeat_fill else AgentDecision(kind="finish", summary="done")
    async def incomplete():
        return False
    agent = PersonaAgent(model=ScriptedModel([decision] * 5), context=MemoryContextAssembler(memory), tools=FakeTools(), state=state, memory=memory, budgets=BudgetConfig(), completion_check=incomplete)
    result = await agent.run(persona, session, observation)
    assert result.reason == "progress_repair_exhausted"
    assert result.steps == (1 if repeat_fill else 0)
    assert result.model_requests == (4 if repeat_fill else 3)
    assert len([e for e in await state.list_events("r") if e.kind == "tool_result"]) == result.steps


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
    tool_event = next(event for event in await state.list_events("r1") if event.kind == "tool_result")
    assert tool_event.payload["retrieved_memory_ids"] == []
    assert tool_event.payload["context_tokens"] > 0
    assert tool_event.payload["model_latency_ms"] == 1


@pytest.mark.asyncio
async def test_agent_suspicion_is_checkpointed_before_verification() -> None:
    now = datetime.now(timezone.utc)
    state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
    await state.create_run(RunRecord(id="suspect-run", scenario_id="trial_return", created_at=now, business_time=now))
    session = SessionRecord(id="suspect-session", run_id="suspect-run", persona_id="p", phase="return", due_business_time=now)
    await state.enqueue_session(session)
    persona = PersonaRecord(id="p", run_id="suspect-run", kind="returning", goal="check trial", application_account_id="a")
    observation = Observation(id="o", run_id="suspect-run", session_id=session.id, url="http://demo/dashboard", visible_text="Trial active: false", captured_at=now)
    decisions = [
        AgentDecision(kind=DecisionKind.SUSPICION, invariant_id="trial_access_seven_days", summary="Trial appears inactive on day six."),
        AgentDecision(kind=DecisionKind.FINISH, summary="verification complete"),
    ]
    handled = []
    async def verify(event):
        handled.append(event)
        assert event.kind == "agent_suspicion"
        assert event.payload["observation"]["visible_text"] == "Trial active: false"
        return "confirmed"
    agent = PersonaAgent(model=ScriptedModel(decisions), context=MemoryContextAssembler(memory), tools=FakeTools(), state=state, memory=memory, budgets=BudgetConfig(max_steps=3), suspicion_handler=verify)
    result = await agent.run(persona, session, observation)
    assert result.status == "completed"
    assert len(handled) == 1
    events = await state.list_events("suspect-run")
    assert [event.kind for event in events] == ["session_started", "agent_suspicion", "session_finished"]


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


@pytest.mark.asyncio
async def test_agent_resumes_from_durable_step_without_duplicate_start_event() -> None:
    now = datetime.now(timezone.utc)
    state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
    await state.create_run(RunRecord(id="resume-run", scenario_id="trial_return", created_at=now, business_time=now))
    initial = SessionRecord(id="resume-session", run_id="resume-run", persona_id="p", phase="start", due_business_time=now)
    await state.enqueue_session(initial)
    persona = PersonaRecord(id="p", run_id="resume-run", kind="new_customer", goal="test", application_account_id="a", allowed_tool_names=["observe_page"])
    observation = Observation(id="o", run_id="resume-run", session_id="resume-session", url="http://demo/", captured_at=now)
    action = AgentDecision(kind=DecisionKind.ACTION, action=Action(id="first", tool_name="observe_page"))
    first = PersonaAgent(model=ScriptedModel([action]), context=MemoryContextAssembler(memory), tools=FakeTools(), state=state, memory=memory, budgets=BudgetConfig(max_steps=4))
    interrupted = await first.run(persona, initial, observation, stop_after_steps=1)
    assert interrupted.status == "interrupted"
    persisted = await state.get_session("resume-session")
    assert persisted.step_count == 1
    resumed = PersonaAgent(model=ScriptedModel([AgentDecision(kind=DecisionKind.FINISH, summary="done")]), context=MemoryContextAssembler(memory), tools=FakeTools(), state=state, memory=memory, budgets=BudgetConfig(max_steps=4))
    result = await resumed.run(persona, persisted, observation)
    assert result.status == "completed"
    events = await state.list_events("resume-run", limit=20)
    assert [event.sequence for event in events] == [0, 1, 2]
    assert [event.kind for event in events].count("session_started") == 1


@pytest.mark.asyncio
async def test_concurrent_personas_allocate_unique_run_wide_event_sequences() -> None:
    now = datetime.now(timezone.utc)
    state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
    await state.create_run(RunRecord(id="shared-run", scenario_id="trial_return", created_at=now, business_time=now))
    personas = [
        PersonaRecord(id="p-one", run_id="shared-run", kind="customer", goal="test", application_account_id="a-one", allowed_tool_names=["observe_page"]),
        PersonaRecord(id="p-two", run_id="shared-run", kind="customer", goal="test", application_account_id="a-two", allowed_tool_names=["observe_page"]),
    ]
    sessions = [
        SessionRecord(id="s-one", run_id="shared-run", persona_id="p-one", phase="start", due_business_time=now),
        SessionRecord(id="s-two", run_id="shared-run", persona_id="p-two", phase="start", due_business_time=now),
    ]
    for session in sessions:
        await state.enqueue_session(session)
    observations = [
        Observation(id=f"o-{index}", run_id="shared-run", session_id=session.id, url="http://demo/", captured_at=now)
        for index, session in enumerate(sessions, start=1)
    ]

    async def run_persona(persona, session, observation) -> None:
        decisions = [
            AgentDecision(kind=DecisionKind.ACTION, action=Action(id=f"act-{persona.id}", tool_name="observe_page")),
            AgentDecision(kind=DecisionKind.FINISH, summary="done"),
        ]
        agent = PersonaAgent(model=ScriptedModel(decisions), context=MemoryContextAssembler(memory), tools=FakeTools(), state=state, memory=memory, budgets=BudgetConfig(max_steps=3))
        result = await agent.run(persona, session, observation)
        assert result.status == "completed"

    await asyncio.gather(*(run_persona(persona, session, observation) for persona, session, observation in zip(personas, sessions, observations)))
    events = await state.list_events("shared-run", limit=20)
    assert len(events) == 6
    assert [event.sequence for event in events] == list(range(6))
    assert len({event.sequence for event in events}) == len(events)
