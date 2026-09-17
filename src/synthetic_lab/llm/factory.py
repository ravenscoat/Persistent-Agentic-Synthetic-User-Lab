from __future__ import annotations

from synthetic_lab.config import Settings

from .ollama import OllamaModelClient
from .router import FallbackModelClient


def build_local_model(settings: Settings) -> FallbackModelClient:
    """Build the configured local Ollama chain without contacting the server."""
    clients = [
        OllamaModelClient(
            base_url=settings.model_base_url,
            model_name=name,
            concurrency=settings.model_concurrency,
            timeout_seconds=settings.model_timeout_seconds,
        )
        for name in settings.configured_model_names()
    ]
    return FallbackModelClient(clients)
