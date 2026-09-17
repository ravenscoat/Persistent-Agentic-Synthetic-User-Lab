from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from synthetic_lab.contracts import ModelResponse

from .ollama import InvalidModelOutput, ModelTimeout, ModelUnavailable


class FallbackModelClient:
    """Try local model clients in order, keeping agent work available."""

    def __init__(self, clients: Sequence[Any]) -> None:
        if not clients:
            raise ValueError("at least one model client is required")
        self.clients = list(clients)

    async def decide(self, messages: Sequence[dict[str, Any]], decision_schema: dict[str, Any] | None = None, generation_options: dict[str, Any] | None = None) -> ModelResponse:
        last_error: Exception | None = None
        for client in self.clients:
            try:
                return await client.decide(messages, decision_schema, generation_options)
            except (ModelUnavailable, ModelTimeout, InvalidModelOutput) as exc:
                last_error = exc
        raise ModelUnavailable(f"all configured model clients failed: {last_error}") from last_error

    async def decide_with_repair(self, messages: Sequence[dict[str, Any]], decision_schema: dict[str, Any] | None = None, generation_options: dict[str, Any] | None = None, *, repair_message: dict[str, str] | None = None) -> ModelResponse:
        last_error: Exception | None = None
        for client in self.clients:
            try:
                method = getattr(client, "decide_with_repair", client.decide)
                return await method(messages, decision_schema, generation_options, **({"repair_message": repair_message} if repair_message is not None and hasattr(client, "decide_with_repair") else {}))
            except (ModelUnavailable, ModelTimeout, InvalidModelOutput) as exc:
                last_error = exc
        raise ModelUnavailable(f"all configured model clients failed: {last_error}") from last_error

    async def aclose(self) -> None:
        for client in self.clients:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()
