from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from synthetic_lab.contracts import AgentDecision, ModelResponse, ModelUsage
from .ollama import InvalidModelOutput, ModelTimeout, ModelUnavailable, _json_object


class GroqModelClient:
    """OpenAI-compatible Groq client for structured browser decisions."""

    def __init__(self, api_key: str, model_name: str = "openai/gpt-oss-120b", *, concurrency: int = 1, timeout_seconds: float = 60.0, client: httpx.AsyncClient | None = None) -> None:
        if not api_key:
            raise ValueError("Groq API key is required")
        self.api_key, self.model_name, self.timeout_seconds = api_key, model_name, timeout_seconds
        self._semaphore, self._client, self._owns_client = asyncio.Semaphore(concurrency), client, client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def decide(self, messages: Sequence[dict[str, Any]], decision_schema: dict[str, Any] | None = None, generation_options: dict[str, Any] | None = None) -> ModelResponse:
        constrained_messages = [
            {"role": "system", "content": "Return JSON only, no markdown. Valid decision shapes: {\"kind\":\"action\",\"action\":{\"id\":\"short-id\",\"tool_name\":\"click|fill|navigate|observe_page\",\"arguments\":{...}}}; {\"kind\":\"finish\",\"summary\":\"...\"}; {\"kind\":\"suspicion\",\"invariant_id\":\"...\",\"summary\":\"...\"}; {\"kind\":\"blocked\",\"summary\":\"...\"}; or {\"kind\":\"memory_query\",\"query\":\"...\"}. For click/fill use the target alias from the page, never a raw element id."},
            *list(messages),
        ]
        payload: dict[str, Any] = {"model": self.model_name, "messages": constrained_messages, "temperature": 0, "response_format": {"type": "json_object"}}
        if generation_options and generation_options.get("num_predict"):
            payload["max_tokens"] = generation_options["num_predict"]
        started = time.perf_counter()
        async with self._semaphore:
            try:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
                response = await self._client.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
                if response.status_code == 429:
                    retry_after = min(float(response.headers.get("retry-after", "3")), 15.0)
                    await asyncio.sleep(max(1.0, retry_after))
                    response = await self._client.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
            except httpx.TimeoutException as exc:
                raise ModelTimeout("Groq request timed out") from exc
            except httpx.HTTPError as exc:
                raise ModelUnavailable("Groq endpoint is unreachable") from exc
        if response.status_code in {401, 403, 404, 408, 429, 500, 502, 503, 504}:
            detail = response.text.replace("\n", " ")[:240]
            raise ModelUnavailable(f"Groq returned HTTP {response.status_code}: {detail}")
        try:
            body = response.json(); response.raise_for_status()
            content = body["choices"][0]["message"]["content"]
            decision = AgentDecision.model_validate(_json_object(content))
        except (httpx.HTTPStatusError, ValueError, KeyError, TypeError, ValidationError, InvalidModelOutput) as exc:
            raise InvalidModelOutput("Groq response did not contain a valid decision") from exc
        usage = body.get("usage", {})
        return ModelResponse(decision=decision, usage=ModelUsage(input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)), estimated=not bool(usage)), latency_ms=(time.perf_counter() - started) * 1000, model_id=str(body.get("model", self.model_name)))

    async def decide_with_repair(self, messages: Sequence[dict[str, Any]], decision_schema: dict[str, Any] | None = None, generation_options: dict[str, Any] | None = None) -> ModelResponse:
        try:
            return await self.decide(messages, decision_schema, generation_options)
        except InvalidModelOutput:
            return await self.decide([*messages, {"role": "user", "content": "Return exactly one valid JSON decision object. Do not add markdown or explanation."}], decision_schema, generation_options)
