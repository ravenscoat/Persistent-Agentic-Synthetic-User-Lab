from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from synthetic_lab.contracts import AgentDecision, ModelResponse, ModelUsage


class ModelUnavailable(RuntimeError):
    """The configured model endpoint or model is unavailable."""


class ModelTimeout(RuntimeError):
    """The model did not answer before the configured deadline."""


class InvalidModelOutput(RuntimeError):
    """The model answered, but not with a valid decision."""


def _json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise InvalidModelOutput("model content is not JSON text")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidModelOutput("model content is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise InvalidModelOutput("model JSON must be an object")
    return parsed


class OllamaModelClient:
    """One shared, bounded-concurrency client for a local Ollama server."""

    def __init__(self, base_url: str = "http://localhost:11434", model_name: str = "qwen3:8b", *, concurrency: int = 1, timeout_seconds: float = 90.0, client: httpx.AsyncClient | None = None) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least one")
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def decide(self, messages: Sequence[dict[str, Any]], decision_schema: dict[str, Any] | None = None, generation_options: dict[str, Any] | None = None) -> ModelResponse:
        schema = decision_schema or AgentDecision.model_json_schema()
        options = dict(generation_options or {})
        # Action selection is latency-sensitive. Qwen3 thinking remains
        # available by passing ``think=True`` explicitly.
        think = bool(options.pop("think", False))
        payload: dict[str, Any] = {"model": self.model_name, "messages": list(messages), "stream": False, "think": think, "format": schema, "options": options}
        started = time.perf_counter()
        async with self._semaphore:
            try:
                client = self._client
                if client is None:
                    client = httpx.AsyncClient(timeout=self.timeout_seconds)
                    self._client = client
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
            except httpx.TimeoutException as exc:
                raise ModelTimeout("Ollama request timed out") from exc
            except httpx.HTTPError as exc:
                raise ModelUnavailable("Ollama endpoint is unreachable") from exc
        if response.status_code in {404, 408, 429, 500, 502, 503, 504}:
            raise ModelUnavailable(f"Ollama returned HTTP {response.status_code}")
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise InvalidModelOutput("Ollama returned an invalid response") from exc
        try:
            content = body["message"]["content"]
            decision = AgentDecision.model_validate(_json_object(content))
        except (KeyError, TypeError, ValidationError, InvalidModelOutput) as exc:
            raise InvalidModelOutput("Ollama response did not contain a valid decision") from exc
        prompt_tokens = body.get("prompt_eval_count")
        output_tokens = body.get("eval_count")
        estimated = not isinstance(prompt_tokens, int) or not isinstance(output_tokens, int)
        return ModelResponse(
            decision=decision,
            usage=ModelUsage(input_tokens=prompt_tokens if isinstance(prompt_tokens, int) else 0, output_tokens=output_tokens if isinstance(output_tokens, int) else 0, estimated=estimated),
            latency_ms=(time.perf_counter() - started) * 1000,
            model_id=str(body.get("model", self.model_name)),
        )

    async def decide_with_repair(self, messages: Sequence[dict[str, Any]], decision_schema: dict[str, Any] | None = None, generation_options: dict[str, Any] | None = None, *, repair_message: dict[str, str] | None = None) -> ModelResponse:
        try:
            return await self.decide(messages, decision_schema, generation_options)
        except InvalidModelOutput:
            repair = repair_message or {"role": "user", "content": "Return exactly one JSON object matching the required decision schema. Do not include markdown or explanation."}
            return await self.decide([*messages, repair], decision_schema, generation_options)
