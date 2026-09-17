from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx


class EmbeddingUnavailable(RuntimeError):
    """The local embedding service could not answer."""


class OllamaEmbeddingClient:
    """Optional local embedding adapter; callers can fall back to lexical retrieval."""

    def __init__(self, base_url: str = "http://localhost:11434", model_name: str = "qwen3-embedding:0.6b", *, timeout_seconds: float = 60.0, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def embed(self, texts: str | Sequence[str]) -> list[list[float]]:
        value: str | list[str] = texts if isinstance(texts, str) else list(texts)
        try:
            client = self._client
            if client is None:
                client = httpx.AsyncClient(timeout=self.timeout_seconds)
                self._client = client
            response = await client.post(f"{self.base_url}/api/embed", json={"model": self.model_name, "input": value})
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            embeddings = body.get("embeddings")
            if not isinstance(embeddings, list) or not all(isinstance(item, list) for item in embeddings):
                raise EmbeddingUnavailable("embedding response did not contain embeddings")
            return [[float(value) for value in item] for item in embeddings]
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise EmbeddingUnavailable("embedding endpoint is unavailable or malformed") from exc
