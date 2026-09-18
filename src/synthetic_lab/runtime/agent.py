from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from synthetic_lab.contracts import (
    Action,
    AgentDecision,
    Event,
    MemoryRecord,
    MemoryType,
    PersonaRecord,
    SessionRecord,
    SessionStatus,
    ToolResult,
    Trust,
)
from synthetic_lab.memory.context import MemoryContextAssembler


@dataclass(frozen=True)
class AgentRunResult:
    status: str
    reason: str
    steps: int
    model_requests: int
    last_observation_id: str


class PersonaAgent:
    """Executes one persona session with hard budgets and durable checkpoints."""

    def __init__(self, *, model: Any, context: MemoryContextAssembler, tools: Any, state: Any, memory: Any, budgets: Any, clock: Any | None = None, completion_check: Any | None = None) -> None:
        self.model = model
        self.context = context
        self.tools = tools
        self.state = state
        self.memory = memory
        self.budgets = budgets
        self.clock = clock
        self.completion_check = completion_check

    async def run(
        self,
        persona: PersonaRecord,
        session: SessionRecord,
        observation: Any,
        *,
        stop_after_steps: int | None = None,
    ) -> AgentRunResult:
        """Run or resume a session from its last durable checkpoint.

        ``stop_after_steps`` is a test-only crash boundary. It exits only after
        the action checkpoint and memory write succeed, while leaving the
        durable session RUNNING so a different worker can reclaim it.
        """
        if stop_after_steps is not None and stop_after_steps <= session.step_count:
            raise ValueError("stop_after_steps must be greater than the persisted step count")
        steps = session.step_count
        requests = 0
        feedback = ""
        corrections = 0
        last_fill = None
        last_click = None
        current = session.model_copy(update={"status": SessionStatus.RUNNING})
        if steps == 0:
            await self.state.append_event(await self._new_event(current, "session_started", {"phase": current.phase}))
        while steps < self.budgets.max_steps and requests < self.budgets.max_model_requests:
            bundle = await self.context.build(persona, current, observation, self.budgets)
            if feedback:
                bundle.messages.append({"role": "user", "content": feedback})
            try:
                if hasattr(self.model, "decide_with_repair"):
                    response = await self.model.decide_with_repair(bundle.messages, generation_options={"num_predict": self.budgets.output_tokens})
                else:
                    response = await self.model.decide(bundle.messages, generation_options={"num_predict": self.budgets.output_tokens})
                requests += 1
            except Exception as exc:
                failed = current.model_copy(update={"status": SessionStatus.FAILED})
                await self.state.append_event(await self._new_event(current, "model_failed", {"error": type(exc).__name__}))
                await self.state.checkpoint_step(current.id, await self._new_event(current, "session_failed", {"reason": "model_unavailable"}), failed)
                return AgentRunResult("failed", "model_unavailable", steps, requests, observation.id)
            decision: AgentDecision = response.decision
            if decision.kind.value == "finish":
                if self.completion_check and not await self.completion_check():
                    corrections += 1
                    if corrections > 2:
                        return await self._fail(current, observation, steps, requests, "progress_repair_exhausted")
                    feedback = f"Completion check failed. You are still at {observation.url}. Filling fields does not submit the form. If required fields are filled, choose the submit button. Return an action decision until the requested destination is visible."
                    continue
                completed = current.model_copy(update={"status": SessionStatus.COMPLETED, "step_count": steps})
                await self.state.checkpoint_step(current.id, await self._new_event(current, "session_finished", {"summary": decision.summary}), completed)
                return AgentRunResult("completed", decision.summary or "finished", steps, requests, observation.id)
            if decision.kind.value == "blocked":
                failed = current.model_copy(update={"status": SessionStatus.FAILED, "step_count": steps})
                await self.state.checkpoint_step(current.id, await self._new_event(current, "session_blocked", {"summary": decision.summary}), failed)
                return AgentRunResult("blocked", decision.summary or "blocked", steps, requests, observation.id)
            if decision.kind.value == "memory_query":
                records = await self.memory.search(persona.run_id, persona.id, decision.query or "", 5)
                query_event = await self._new_event(
                    current,
                    "memory_queried",
                    {"query": decision.query or "", "result_count": len(records)},
                )
                await self.state.append_event(query_event)
                await self._write_memory(persona, current, MemoryType.CONVERSATION, f"Memory query: {decision.query}; returned {len(records)} records", query_event.sequence)
                steps += 1
                current = current.model_copy(update={"step_count": steps})
                continue
            action = decision.action
            if action is None:
                return await self._fail(current, observation, steps, requests, "missing_action")
            try:
                action = self._resolve_model_target(action, observation)
            except ValueError:
                return await self._fail(current, observation, steps, requests, "invalid_action_target")
            target_element = next((element for element in observation.elements if element.id == action.arguments.get("element_id")), None)
            if action.tool_name == "click" and target_element and target_element.role == "button":
                missing = [element.name for element in observation.elements if element.required and element.filled is False]
                if missing and "account" in target_element.name.casefold():
                    corrections += 1
                    if corrections > 4:
                        return await self._fail(current, observation, steps, requests, "progress_repair_exhausted")
                    feedback = f"Do not submit yet. Required fields still empty: {missing}. Fill those fields before clicking {target_element.name}."
                    continue
            signature = (observation.url, action.arguments.get("element_id"), action.arguments.get("value"))
            click_signature = (observation.url, action.arguments.get("element_id"))
            if action.tool_name == "click" and click_signature == last_click:
                corrections += 1
                if corrections > 4:
                    return await self._fail(current, observation, steps, requests, "progress_repair_exhausted")
                feedback = "That exact click already succeeded on this page. Choose the next incomplete step or navigate to the next page; do not repeat it."
                continue
            if action.tool_name == "fill" and signature == last_fill:
                corrections += 1
                if corrections > 2:
                    return await self._fail(current, observation, steps, requests, "progress_repair_exhausted")
                feedback = "That exact fill already succeeded. Choose another incomplete field or submit the completed form. Do not repeat the same value in the same field."
                continue
            feedback = ""
            result: ToolResult = await self.tools.dispatch(persona, action)
            last_fill = signature if action.tool_name == "fill" and result.status.value == "success" else None
            last_click = click_signature if action.tool_name == "click" and result.status.value == "success" else None
            target_name = target_element.name if target_element else action.tool_name
            steps += 1
            event = await self._new_event(
                current,
                "tool_result",
                {
                    "tool_name": action.tool_name,
                    "target_name": target_name,
                    "action_id": action.id,
                    "status": result.status.value,
                    "data": result.data,
                    "error_code": result.error_code,
                    # IDs, not memory text: enough for evidence/retrieval
                    # audits without duplicating potentially sensitive context.
                    "retrieved_memory_ids": bundle.included_memory_ids,
                    "context_tokens": bundle.estimated_tokens,
                    "context_accounting_method": bundle.accounting_method,
                    "model_latency_ms": response.latency_ms,
                },
            )
            next_status = SessionStatus.RUNNING if result.status.value == "success" else SessionStatus.WAITING
            current = current.model_copy(update={"status": next_status, "step_count": steps})
            await self.state.checkpoint_step(current.id, event, current)
            await self._write_memory(persona, current, MemoryType.TOOL_LOG, f"Step {steps}: {action.tool_name} on {target_name!r}: {result.status.value}. Use the CURRENT observation for field state and targets.", event.sequence)
            if stop_after_steps is not None and steps >= stop_after_steps:
                return AgentRunResult("interrupted", "simulated_crash", steps, requests, observation.id)
            if isinstance(result.data, dict) and result.data.get("id") and result.data.get("url"):
                try:
                    from synthetic_lab.contracts import Observation
                    observation = Observation.model_validate(result.data)
                except Exception:
                    pass
            if result.status.value == "error" and result.error_code == "stale_observation":
                # Refresh before asking the model to retry; stale element IDs
                # are a recoverable browser synchronization issue.
                refreshed = await self.tools.dispatch(persona, Action(id=str(uuid.uuid4()), tool_name="observe_page"))
                if isinstance(refreshed.data, dict) and refreshed.data.get("id") and refreshed.data.get("url"):
                    from synthetic_lab.contracts import Observation
                    observation = Observation.model_validate(refreshed.data)
                continue
            if result.status.value != "success":
                return AgentRunResult("blocked", result.error_code or "tool_failed", steps, requests, observation.id)
        reason = "step_budget_exhausted" if steps >= self.budgets.max_steps else "model_request_budget_exhausted"
        exhausted = current.model_copy(update={"status": SessionStatus.FAILED, "step_count": steps})
        await self.state.checkpoint_step(current.id, await self._new_event(current, "budget_exhausted", {"reason": reason}), exhausted)
        return AgentRunResult("failed", reason, steps, requests, observation.id)

    @staticmethod
    def _resolve_model_target(action: Action, observation: Any) -> Action:
        """Resolve compact eN targets against this exact observation."""
        if action.tool_name not in {"click", "fill", "select_option"}:
            return action
        arguments = dict(action.arguments)
        target = arguments.pop("target", None)
        if target is None and isinstance(arguments.get("element_id"), str) and arguments["element_id"].startswith("e") and arguments["element_id"][1:].isdigit():
            target = arguments["element_id"]
        if target is not None:
            if not isinstance(target, str) or not target.startswith("e") or not target[1:].isdigit():
                raise ValueError("invalid model target alias")
            index = int(target[1:]) - 1
            if index < 0 or index >= len(observation.elements):
                raise ValueError("model target alias is not in the current observation")
            arguments["element_id"] = observation.elements[index].id
        if "element_id" not in arguments:
            raise ValueError("element action requires target")
        if action.observation_id is None:
            return action.model_copy(update={"arguments": arguments, "observation_id": observation.id})
        return action.model_copy(update={"arguments": arguments})

    async def _fail(self, session: SessionRecord, observation: Any, steps: int, requests: int, reason: str) -> AgentRunResult:
        failed = session.model_copy(update={"status": SessionStatus.FAILED, "step_count": steps})
        await self.state.checkpoint_step(session.id, await self._new_event(session, "session_failed", {"reason": reason}), failed)
        return AgentRunResult("failed", reason, steps, requests, observation.id)

    async def _write_memory(self, persona: PersonaRecord, session: SessionRecord, memory_type: MemoryType, text: str, sequence: int) -> None:
        now = datetime.now(timezone.utc)
        await self.memory.append(MemoryRecord(id=str(uuid.uuid4()), run_id=persona.run_id, persona_id=persona.id, type=memory_type, text=text, structured_data={}, source_event_ids=[f"{session.id}:{sequence}"], trust=Trust.OBSERVED, valid_from=now))

    async def _new_event(self, session: SessionRecord, kind: str, payload: dict[str, Any]) -> Event:
        sequence = await self.state.reserve_event_sequences(session.run_id)
        now = datetime.now(timezone.utc)
        return Event(id=str(uuid.uuid4()), run_id=session.run_id, persona_id=session.persona_id, session_id=session.id, sequence=sequence, kind=kind, wall_time=now, business_time=now, payload=payload)
