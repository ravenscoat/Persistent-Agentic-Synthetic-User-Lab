"""Optional Langfuse tracing for agent runs.

The application deliberately treats tracing as an enhancement: the local demo
must keep working when the SDK is not installed or credentials are absent.
The adapter also keeps Langfuse-specific APIs out of the agent loop, which
makes the runtime easy to test with a fake model.
"""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
from time import perf_counter
from typing import Any, Iterator

from synthetic_lab.config import Settings


def _short(value: Any, limit: int = 8_000) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [_short(item, limit) for item in value]
    if isinstance(value, dict):
        return {str(key): _short(item, limit) for key, item in value.items()}
    return value


class LangfuseTracer:
    """Small no-op-safe facade over the Langfuse v4 Python SDK."""

    def __init__(self, settings: Settings) -> None:
        self.client: Any | None = None
        self._propagate_attributes: Any | None = None
        self._get_current_trace_id: Any | None = None
        if not settings.langfuse_enabled:
            return
        try:
            from langfuse import Langfuse

            self.client = Langfuse(public_key=settings.langfuse_public_key,
                                   secret_key=settings.langfuse_secret_key,
                                   base_url=settings.langfuse_host)
            self._get_current_trace_id = self.client.get_current_trace_id
            try:
                from langfuse import propagate_attributes

                self._propagate_attributes = propagate_attributes
            except ImportError:
                pass
        except Exception:
            # Missing optional package, malformed credentials, or an unavailable
            # SDK must never prevent the browser agent from running.
            self.client = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @contextmanager
    def run_trace(self, run_id: str, persona_id: str, session_id: str) -> Iterator[Any | None]:
        if not self.client:
            yield None
            return
        attributes = {
            "session_id": session_id,
            "user_id": persona_id,
            "metadata": {"run_id": run_id, "persona_id": persona_id, "component": "synthetic-user-lab"},
            "tags": ["synthetic-user-lab", "browser-agent"],
        }
        stack = ExitStack()
        try:
            if self._propagate_attributes:
                stack.enter_context(self._propagate_attributes(**attributes))
            span = stack.enter_context(self.client.start_as_current_observation(
                as_type="agent", name="synthetic-persona-session",
                input={"run_id": run_id, "persona_id": persona_id, "session_id": session_id}))
        except Exception:
            span = None
        try:
            yield span
        finally:
            # A tracer teardown error must never replace an application error
            # or turn a successful business action into a failed action.
            try:
                stack.close()
            except Exception:
                pass
            # Runs can be short-lived and the dashboard should expose their
            # trace as soon as the durable run is complete.
            self.flush()

    @contextmanager
    def generation(self, *, model: str, messages: Any) -> Iterator[Any | None]:
        if not self.client:
            yield None
            return
        stack = ExitStack()
        try:
            generation = stack.enter_context(self.client.start_as_current_observation(
                as_type="generation",
                name="agent-decision",
                model=model,
                # Deliberately do not export prompts, page observations, or tool
                # results. Only safe metadata is sent to the external tracer.
                input={"message_count": len(messages) if isinstance(messages, list) else None},
            ))
        except Exception:
            generation = None
        try:
            yield generation
        finally:
            try:
                stack.close()
            except Exception:
                pass

    def trace_id(self, observation: Any | None = None) -> str | None:
        try:
            if self._get_current_trace_id:
                return self._get_current_trace_id()
            value = getattr(observation, "trace_id", None)
            return str(value) if value else None
        except Exception:
            return None

    def flush(self) -> None:
        if self.client:
            try:
                self.client.flush()
            except Exception:
                pass


class TracedModelClient:
    """Delegate model calls while recording compact Langfuse generations."""

    def __init__(self, model: Any, tracer: LangfuseTracer) -> None:
        self.model = model
        self.tracer = tracer
        self.last_trace_id: str | None = None

    async def decide(self, messages: Any, decision_schema: Any = None, generation_options: Any = None) -> Any:
        return await self._call("decide", messages, decision_schema, generation_options)

    async def decide_with_repair(self, messages: Any, decision_schema: Any = None, generation_options: Any = None) -> Any:
        return await self._call("decide_with_repair", messages, decision_schema, generation_options)

    async def _call(self, method: str, messages: Any, decision_schema: Any, generation_options: Any) -> Any:
        self.last_trace_id = None
        fn = getattr(self.model, method, None) or self.model.decide
        started = perf_counter()
        with self.tracer.generation(model=getattr(self.model, "model_name", type(self.model).__name__), messages=messages) as generation:
            response = await fn(messages, decision_schema=decision_schema, generation_options=generation_options)
            self.last_trace_id = self.tracer.trace_id(generation)
            if generation is not None:
                try:
                    decision = getattr(response, "decision", None)
                    generation.update(
                        model=response.model_id,
                        output={"decision_kind": getattr(getattr(decision, "kind", None), "value", None)},
                        usage_details={"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
                        metadata={"latency_ms": round((perf_counter() - started) * 1000, 2), "usage_estimated": response.usage.estimated},
                    )
                except Exception:
                    pass
            return response

    async def aclose(self) -> None:
        close = getattr(self.model, "aclose", None)
        if close:
            await close()


class TracedToolRegistry:
    """Record safe tool metadata while dispatching each action exactly once."""

    def __init__(self, registry: Any, tracer: LangfuseTracer) -> None:
        self.registry = registry
        self.tracer = tracer

    def list_allowed(self, persona: Any) -> Any:
        return self.registry.list_allowed(persona)

    async def dispatch(self, persona: Any, action: Any) -> Any:
        stack = ExitStack()
        observation = None
        if self.tracer.client:
            try:
                observation = stack.enter_context(self.tracer.client.start_as_current_observation(
                    as_type="tool", name=f"browser-{action.tool_name}",
                    input={"tool_name": action.tool_name, "action_id": action.id},
                ))
            except Exception:
                observation = None
        try:
            result = await self.registry.dispatch(persona, action)
            if observation is not None:
                try:
                    observation.update(output={"status": result.status.value, "error_code": result.error_code, "artifact_count": len(result.artifact_ids)})
                except Exception:
                    pass
            return result
        finally:
            try:
                stack.close()
            except Exception:
                pass


class TracedContextAssembler:
    """Record memory retrieval without exporting memory text or page content."""

    def __init__(self, assembler: Any, tracer: LangfuseTracer) -> None:
        self.assembler = assembler
        self.tracer = tracer

    def model_tools(self, persona: Any) -> Any:
        return self.assembler.model_tools(persona)

    async def build(self, persona: Any, session: Any, observation: Any, budgets: Any) -> Any:
        stack = ExitStack()
        retrieval = None
        if self.tracer.client:
            try:
                retrieval = stack.enter_context(self.tracer.client.start_as_current_observation(
                    as_type="retriever", name="assemble-memory-context",
                    input={"run_id": persona.run_id, "persona_id": persona.id, "session_phase": session.phase},
                ))
            except Exception:
                retrieval = None
        try:
            bundle = await self.assembler.build(persona, session, observation, budgets)
            if retrieval is not None:
                try:
                    retrieval.update(output={"memory_ids": bundle.included_memory_ids, "memory_count": len(bundle.included_memory_ids), "context_tokens": bundle.estimated_tokens, "accounting_method": bundle.accounting_method})
                except Exception:
                    pass
            return bundle
        finally:
            try:
                stack.close()
            except Exception:
                pass
