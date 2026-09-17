from datetime import datetime, timezone

import httpx
import pytest

from synthetic_lab.contracts import BudgetConfig, MemoryRecord, MemoryType, Observation, PersonaRecord, SessionRecord, Trust
from synthetic_lab.memory import MemoryContextAssembler, OllamaEmbeddingClient
from synthetic_lab.storage import InMemoryMemoryRepository


@pytest.mark.asyncio
async def test_context_keeps_current_observation_and_respects_budget() -> None:
    now = datetime.now(timezone.utc)
    repo = InMemoryMemoryRepository()
    await repo.append(MemoryRecord(id="m1", run_id="r1", persona_id="p1", type=MemoryType.ENTITY, text="account alpha has a pro plan", trust=Trust.OBSERVED, valid_from=now))
    await repo.append(MemoryRecord(id="m2", run_id="r1", persona_id="p1", type=MemoryType.SUMMARY, text="old " * 1000, trust=Trust.OBSERVED, valid_from=now))
    persona = PersonaRecord(id="p1", run_id="r1", kind="new_customer", goal="check my account plan", application_account_id="a1")
    session = SessionRecord(id="s1", run_id="r1", persona_id="p1", phase="check", due_business_time=now)
    observation = Observation(id="o1", run_id="r1", session_id="s1", url="http://demo/", title="Account", visible_text="account alpha old", captured_at=now)
    bundle = await MemoryContextAssembler(repo).build(persona, session, observation, BudgetConfig(context_tokens=500, output_tokens=100, safety_tokens=50))
    assert any("Current page" in str(message["content"]) for message in bundle.messages)
    assert bundle.estimated_tokens <= 350
    assert bundle.omitted_counts.get("summary", 0) == 1


@pytest.mark.asyncio
async def test_embedding_adapter_parses_ollama_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OllamaEmbeddingClient(client=client)
    assert await adapter.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    await client.aclose()
