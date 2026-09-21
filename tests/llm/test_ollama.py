import asyncio
import json

import httpx
import pytest

from synthetic_lab.contracts import DecisionKind
from synthetic_lab.llm.ollama import InvalidModelOutput, ModelUnavailable, OllamaModelClient
from synthetic_lab.llm.router import FallbackModelClient
from synthetic_lab.llm.ollama import constrained_decision_schema
from synthetic_lab.contracts import AgentDecision


def test_generation_schema_requires_suspicion_evidence_and_preserves_invariant():
    schema = AgentDecision.model_json_schema()
    schema["properties"]["invariant_id"] = {"anyOf": [{"const": "purchase_idempotency"}, {"type": "null"}]}
    branches = constrained_decision_schema(schema)["oneOf"]
    suspicion = next(b for b in branches if b["properties"]["kind"]["const"] == "suspicion")
    assert set(suspicion["required"]) == {"kind", "invariant_id", "summary"}
    assert suspicion["properties"]["summary"] == {"type": "string", "minLength": 1}
    assert suspicion["properties"]["invariant_id"]["const"] == "purchase_idempotency"
    assert "anyOf" in schema["properties"]["invariant_id"]


def response(content: str, **extra: object) -> httpx.Response:
    return httpx.Response(200, json={"model": "qwen3:8b", "message": {"content": content}, **extra})


@pytest.mark.asyncio
async def test_parses_structured_finish() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["think"] is False
        return response('{"kind":"finish","summary":"completed"}', prompt_eval_count=12, eval_count=4)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OllamaModelClient(client=client)
    result = await model.decide([{"role": "user", "content": "finish"}])
    assert result.decision.kind is DecisionKind.FINISH
    assert result.usage.input_tokens == 12
    await model.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_repair_is_bounded_to_one_retry() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return response("not json")
        repair = json.loads(request.content)["messages"][-1]["content"]
        assert "non-empty summary" in repair
        return response('{"kind":"finish","summary":"repaired"}')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OllamaModelClient(client=client)
    result = await model.decide_with_repair([])
    assert result.decision.summary == "repaired"
    assert calls == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_http_failure_is_unavailable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OllamaModelClient(client=client)
    with pytest.raises(ModelUnavailable):
        await model.decide([])
    await client.aclose()


@pytest.mark.asyncio
async def test_concurrency_is_bounded() -> None:
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return response('{"kind":"finish","summary":"done"}')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OllamaModelClient(client=client, concurrency=1)
    await asyncio.gather(*(model.decide([]) for _ in range(4)))
    assert peak == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_decision_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response('{"kind":"action"}')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OllamaModelClient(client=client)
    with pytest.raises(InvalidModelOutput):
        await model.decide([])
    await client.aclose()


@pytest.mark.asyncio
async def test_fallback_router_uses_second_client_after_unavailable() -> None:
    class Failing:
        async def decide(self, *args, **kwargs):
            raise ModelUnavailable("throttled")

    async def handler(request: httpx.Request) -> httpx.Response:
        return response('{"kind":"finish","summary":"fallback"}')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    router = FallbackModelClient([Failing(), OllamaModelClient(client=client)])
    result = await router.decide([])
    assert result.decision.summary == "fallback"
    await router.aclose()
