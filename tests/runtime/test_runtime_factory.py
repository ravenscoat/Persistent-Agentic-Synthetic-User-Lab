from synthetic_lab.config import Settings
from synthetic_lab.runtime import build_local_persona_agent


def test_local_persona_factory_wires_configured_model_chain() -> None:
    agent = build_local_persona_agent(
        Settings(model_fallback_names="qwen2.5:3b"),
        context=object(), tools=object(), state=object(), memory=object(), budgets=object()
    )
    assert [client.model_name for client in agent.model.clients] == ["qwen3:8b", "qwen2.5:3b"]
