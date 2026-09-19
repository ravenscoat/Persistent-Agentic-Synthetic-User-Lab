from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from synthetic_lab.config import Settings
from synthetic_lab.observability import LangfuseTracer, TracedModelClient


class BrokenExporter:
    @contextmanager
    def start_as_current_observation(self, **kwargs):
        yield SimpleNamespace(trace_id="trace-123")
        raise RuntimeError("exporter teardown failed")


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
