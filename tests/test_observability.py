from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from synthetic_lab.config import Settings
from synthetic_lab.observability import LangfuseTracer, TracedModelClient, TracedToolRegistry


class BrokenExporter:
    @contextmanager
    def start_as_current_observation(self, **kwargs):
        yield SimpleNamespace(trace_id="trace-123")
        raise RuntimeError("exporter teardown failed")


class UpdatingObservation:
    trace_id = "trace-123"
    def update(self, **kwargs):
        self.updated = kwargs


class WorkingExporter:
    @contextmanager
    def start_as_current_observation(self, **kwargs):
        yield UpdatingObservation()


@pytest.mark.parametrize("scope", ["run", "generation"])
@pytest.mark.parametrize("application_error", [False, True])
def test_exporter_teardown_preserves_application_outcome(scope, application_error):
    tracer = LangfuseTracer(Settings(_env_file=None, langfuse_host=None))
    tracer.client = BrokenExporter()
    context = (tracer.run_trace("r", "p", "s") if scope == "run"
               else tracer.generation(model="test", messages=[]))
    def execute():
        with context as span:
            assert span.trace_id == "trace-123"
            if application_error:
                raise ValueError("original application failure")
        return "success"
    if application_error:
        with pytest.raises(ValueError, match="original application failure"):
            execute()
    else:
        assert execute() == "success"


def test_explicit_credentials_and_sdk_trace_getter(monkeypatch):
    langfuse = pytest.importorskip("langfuse")
    captured = {}
    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def get_current_trace_id(self):
            return "current-trace"
    monkeypatch.setattr(langfuse, "Langfuse", Client)
    tracer = LangfuseTracer(Settings(_env_file=None, langfuse_host="http://localhost:3000",
                                   langfuse_public_key="test-public", langfuse_secret_key="test-secret"))
    assert tracer.trace_id() == "current-trace"
    assert captured == dict(base_url="http://localhost:3000", public_key="test-public", secret_key="test-secret")
    assert tracer._propagate_attributes is not None


def test_trace_propagates_stable_environment_and_tags():
    captured = {}
    @contextmanager
    def propagate_attributes(**kwargs):
        captured.update(kwargs)
        yield
    tracer = LangfuseTracer(Settings(_env_file=None, langfuse_host=None, langfuse_environment="staging"))
    tracer.client = WorkingExporter()
    tracer._propagate_attributes = propagate_attributes
    with tracer.run_trace("run-1", "persona-1", "session-1"):
        pass
    assert captured["environment"] == "staging"
    assert captured["tags"] == ["synthetic-user-lab", "browser-agent"]


@pytest.mark.asyncio
async def test_failed_model_does_not_reuse_previous_trace():
    class Model:
        async def decide(self, *args, **kwargs):
            raise ValueError("model failed")
    tracer = LangfuseTracer(Settings(_env_file=None, langfuse_host=None))
    model = TracedModelClient(Model(), tracer)
    model.last_trace_id = "previous"
    with pytest.raises(ValueError, match="model failed"):
        await model.decide([])
    assert model.last_trace_id is None


@pytest.mark.asyncio
async def test_model_trace_id_is_available_to_durable_event_writer():
    from synthetic_lab.contracts import AgentDecision, DecisionKind, ModelResponse
    class Model:
        model_name = "test-model"
        async def decide(self, *args, **kwargs):
            return ModelResponse(decision=AgentDecision(kind=DecisionKind.FINISH, summary="done"), model_id=self.model_name, latency_ms=1)
    tracer = LangfuseTracer(Settings(_env_file=None, langfuse_host=None))
    tracer.client = WorkingExporter()
    tracer._get_current_trace_id = lambda: "trace-123"
    model = TracedModelClient(Model(), tracer)
    await model.decide([])
    assert model.last_trace_id == "trace-123"


@pytest.mark.asyncio
async def test_tool_dispatch_executes_once_when_trace_teardown_fails():
    from datetime import datetime, timezone
    from synthetic_lab.contracts import Action, ActionStatus, ToolResult
    class Registry:
        calls = 0
        async def dispatch(self, persona, action):
            self.calls += 1
            return ToolResult(action_id=action.id, status=ActionStatus.SUCCESS, observed_at=datetime.now(timezone.utc))
        def list_allowed(self, persona):
            return []
    tracer = LangfuseTracer(Settings(_env_file=None, langfuse_host=None))
    tracer.client = BrokenExporter()
    registry = Registry()
    result = await TracedToolRegistry(registry, tracer).dispatch(None, Action(id="a1", tool_name="charge"))
    assert result.status is ActionStatus.SUCCESS
    assert registry.calls == 1
