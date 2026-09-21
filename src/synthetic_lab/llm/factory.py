from __future__ import annotations

from synthetic_lab.config import Settings

from .ollama import OllamaModelClient
from .router import FallbackModelClient
from .groq import GroqModelClient


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
    if settings.model_provider.casefold() == "groq" and settings.groq_api_key:
        clients.insert(0, GroqModelClient(settings.groq_api_key, settings.groq_model_name, concurrency=settings.model_concurrency, timeout_seconds=settings.model_timeout_seconds))
    return FallbackModelClient(clients)
