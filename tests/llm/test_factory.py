from synthetic_lab.config import Settings
from synthetic_lab.llm import FallbackModelClient, build_local_model


def test_settings_deduplicates_primary_and_fallback_models() -> None:
    settings = Settings(model_name="qwen3:8b", model_fallback_names="qwen3:8b, qwen2.5:3b")
    assert settings.configured_model_names() == ["qwen3:8b", "qwen2.5:3b"]


def test_factory_builds_ordered_local_chain() -> None:
    model = build_local_model(Settings(model_fallback_names="qwen2.5:3b"))
    assert isinstance(model, FallbackModelClient)
    assert [client.model_name for client in model.clients] == ["qwen3:8b", "qwen2.5:3b"]
