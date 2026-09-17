import asyncio

import httpx
import pytest

from synthetic_lab.contracts import DecisionKind
from synthetic_lab.llm.ollama import InvalidModelOutput, ModelUnavailable, OllamaModelClient


def response(content: str, **extra: object) -> httpx.Response:
    return httpx.Response(200, json={"model": "qwen3:8b", "message": {"content": content}, **extra})


@pytest.mark.asyncio
async def test_parses_structured_finish() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
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
