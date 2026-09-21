"""Executor for user-authored product adapters and browser scenarios."""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.config import Settings
from synthetic_lab.contracts import BudgetConfig, Event, Finding, FindingStatus, PersonaRecord, ReplayStatus, RunRecord, SessionRecord
from synthetic_lab.llm import build_local_model
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.observability import LangfuseTracer, TracedContextAssembler, TracedModelClient, TracedToolRegistry
from synthetic_lab.product_adapter import AuthoredScenarioSpec, PersonaJourneySpec, authored_scenario
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository


class ChecklistContext:
    """Expose missing required submissions on every decision, outside retrieval."""

    def __init__(self, context, state, required):
        self.context, self.state, self.required = context, state, required

    async def build(self, persona, session, observation, budgets):
        bundle = await self.context.build(persona, session, observation, budgets)
        events = await self.state.list_events(persona.run_id, limit=1000)
        done = Counter(e.payload.get("target_name") for e in events if e.session_id == session.id and e.kind == "tool_result" and e.payload.get("status") == "success" and e.payload.get("tool_name") == "click")
        remaining = []
        for name in self.required:
            if done[name]:
                done[name] -= 1
            else:
                remaining.append(name)
        if remaining:
            bundle.messages.append({"role": "user", "content": f"Required clicks still missing: {remaining}. Next required submission: {remaining[0]}. Navigate to its page if necessary, then perform it. Do not finish or raise a suspicion before these requested actions are done."})
        return bundle


class ProductAdapterExecutor:
    """Drive a real product using a validated adapter instead of hard-coded routes."""

    def __init__(self, state: Any, memory: Any, settings: Settings | None = None, model_factory: Callable[[Settings], Any] | None = None) -> None:
        self.state = state
        self.memory = memory
        self.settings = settings or Settings()
        # Injection keeps the production model choice in one place while
        # letting recovery tests exercise the real browser executor without a
        # network model call.
        self.model_factory = model_factory or build_local_model

    async def __call__(self, run: RunRecord) -> None:
        spec = authored_scenario(run.config_snapshot)
        if spec is None:
            raise ValueError("run does not contain an authored scenario")
        # A multi-persona profile is one shared run composed of independently
        # checkpointed browser sessions. Execute in the declared order: this
        # supports owner→teammate workflows while preserving persona-scoped
        # memory and safe restart behavior for every session.
        if spec.additional_personas:
            primary = spec.model_copy(update={"additional_personas": []})
            await self._execute_profile_journey(run, primary, "primary")
            for journey in spec.additional_personas:
                await self._execute_profile_journey(run, self._journey_spec(primary, journey), journey.id)
            return
        await self._execute_single(run, spec)

    async def _execute_profile_journey(self, run: RunRecord, spec: AuthoredScenarioSpec, persona_key: str) -> None:
        snapshot = dict(run.config_snapshot)
        snapshot["authored_scenario"] = spec.model_dump(mode="json")
        snapshot["_persona_key"] = persona_key
        await self._execute_single(run.model_copy(update={"config_snapshot": snapshot}), spec)

    @staticmethod
    def _journey_spec(primary: AuthoredScenarioSpec, journey: PersonaJourneySpec) -> AuthoredScenarioSpec:
        product = primary.product.model_copy(update={
            "start_path": journey.start_path or primary.product.start_path,
            "credential_fields": journey.credential_fields or primary.product.credential_fields,
        })
        return primary.model_copy(update={
            "product": product,
            "persona": journey.persona,
            "invariant": journey.invariant,
            "required_clicks": journey.required_clicks,
            "additional_personas": [],
        })

    async def _execute_single(self, run: RunRecord, spec: AuthoredScenarioSpec) -> None:
        now = datetime.now(timezone.utc)
        run_memory = self.memory if (run.config_snapshot or {}).get("memory_enabled", True) else InMemoryMemoryRepository()
        persona_key = str((run.config_snapshot or {}).get("_persona_key") or "")
        if persona_key and not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", persona_key):
            raise ValueError("invalid profile persona key")
        suffix = f"-{persona_key}" if persona_key else ""
        persona_id = f"custom-{run.id[:8]}{suffix}"
        session_id = f"session-{run.id[:8]}{suffix}"
        persona = PersonaRecord(
            id=persona_id,
            run_id=run.id,
            kind=spec.persona.kind,
            goal=spec.persona.goal,
            application_account_id=f"external-{run.id[:12]}",
            allowed_tool_names=list(dict.fromkeys(spec.persona.allowed_tools)),
        )
        session = SessionRecord(id=session_id, run_id=run.id, persona_id=persona_id, phase="authored", due_business_time=now)
        try:
            await self.state.enqueue_session(session)
        except Exception:
            # In-memory storage raises ValueError for an existing session;
            # PostgreSQL raises a driver-specific unique-constraint error.
            # Treat either as normal recovery only if the durable session can
            # actually be read back. Other storage failures still surface.
            try:
                session = await self.state.get_session(session_id)
            except KeyError:
                raise

        # A completed durable session means the process stopped after the
        # agent finished but before the run-level status was updated. Do not
        # replay it and risk a second purchase or submission.
        if session.status.value == "COMPLETED":
            return

        state_path = self._browser_state_path(run.id, session_id)
        browser = PlaywrightBrowserSession(
            run.id,
            session_id,
            spec.product.base_url,
            artifact_root=self.settings.artifact_root,
            storage_state_path=state_path if state_path.is_file() else None,
        )
        trace_settings = self.settings if (run.config_snapshot or {}).get("trace_enabled", True) else self.settings.model_copy(update={"langfuse_host": None, "langfuse_public_key": None, "langfuse_secret_key": None})
        tracer = LangfuseTracer(trace_settings)
        model: Any | None = None
        try:
            await browser.start()
            requested_url = await self._resume_url(run.id, session, spec, fallback=spec.product.start_path)
            await browser.page.goto(requested_url)
            await self._apply_credentials(browser, spec, allow_absent=bool(session.step_count and state_path.is_file()))
            observation = await browser.observe()
            if self._requires_login_setup(spec, requested_url, observation):
                screenshot = await self._capture_screenshot(browser, run.id, session_id if persona_key else None)
                await self.state.append_event(Event(
                    id=str(uuid4()), run_id=run.id, persona_id=persona_id, session_id=session_id,
                    sequence=await self.state.reserve_event_sequences(run.id), kind="login_setup_required",
                    wall_time=datetime.now(timezone.utc), business_time=datetime.now(timezone.utc),
                    payload={
                        "summary": "The product redirected to an authentication form. Add secure login field mappings before this journey can run.",
                        "requested_url": requested_url,
                        "observed_url": observation.url,
                        "credential_values_persisted": False,
                    },
                ))
                await self.state.append_event(Event(
                    id=str(uuid4()), run_id=run.id, persona_id=persona_id, session_id=session_id,
                    sequence=await self.state.reserve_event_sequences(run.id), kind="artifact_captured",
                    wall_time=datetime.now(timezone.utc), business_time=datetime.now(timezone.utc),
                    payload={"kind": "screenshot", "persona_id": persona_id, "url": f"/api/runs/{run.id}/artifacts/{session_id}" if persona_key else f"/api/runs/{run.id}/artifacts/final-page", "byte_count": screenshot.stat().st_size},
                ))
                failed = session.model_copy(update={"status": "FAILED"})
                await self.state.checkpoint_step(session.id, Event(
                    id=str(uuid4()), run_id=run.id, persona_id=persona_id, session_id=session_id,
                    sequence=await self.state.reserve_event_sequences(run.id), kind="session_failed",
                    wall_time=datetime.now(timezone.utc), business_time=datetime.now(timezone.utc),
                    payload={"reason": "login_setup_required"},
                ), failed)
                raise RuntimeError("login_setup_required: configure login selectors and environment-variable credentials")
            tools = TracedToolRegistry(BrowserToolRegistry({persona_id: browser}), tracer)
            model = TracedModelClient(self.model_factory(self.settings), tracer)
            persisted_verdict = None
            async def prerequisites() -> bool:
                events = await self.state.list_events(run.id, limit=1000)
                clicks = Counter(e.payload.get("target_name") for e in events if e.session_id == session_id and e.kind == "tool_result" and e.payload.get("status") == "success" and e.payload.get("tool_name") == "click")
                return all(clicks[name] >= count for name, count in Counter(spec.required_clicks).items())
            async def verify_now() -> bool:
                if not await prerequisites():
                    return False
                verdict, _ = await self._verify(browser, spec)
                return verdict == "satisfied"

            async def handle_suspicion(event: Event) -> str:
                nonlocal persisted_verdict
                if not await prerequisites():
                    return "inconclusive"
                verdict, actual = await self._verify(browser, spec)
                await self._persist_verification(
                    run, session_id, persona_id, spec, verdict, actual,
                    str(event.payload.get("observation", {}).get("id", event.id)),
                )
                persisted_verdict = verdict
                return verdict

            agent = PersonaAgent(
                model=model,
                context=TracedContextAssembler(ChecklistContext(MemoryContextAssembler(run_memory, tool_registry=tools), self.state, spec.required_clicks), tracer),
                tools=tools,
                state=self.state,
                memory=run_memory,
                budgets=BudgetConfig(max_steps=spec.max_steps, max_model_requests=spec.max_steps + 4, output_tokens=96),
                completion_check=verify_now,
                suspicion_handler=handle_suspicion,
                finish_after_verification=True,
                after_action_checkpoint=lambda durable_session, event: self._save_browser_checkpoint(browser, durable_session, event),
            )
            agent.stop_on_completion = bool(spec.required_clicks)
            with tracer.run_trace(run.id, persona_id, session_id) as trace:
                result = await agent.run(persona, session, observation)
                if result.status != "completed":
                    raise RuntimeError(f"Persona {persona_id} {result.status}: {result.reason}")
                final_observation = await browser.observe()
                screenshot = await self._capture_screenshot(browser, run.id, session_id if persona_key else None)
                verdict, actual = await self._verify(browser, spec)
                if persisted_verdict is None:
                    await self._persist_verification(run, session_id, persona_id, spec, verdict, actual, final_observation.id)
                await self.state.append_event(Event(
                    id=str(uuid4()), run_id=run.id, persona_id=persona_id, session_id=session_id,
                    sequence=await self.state.reserve_event_sequences(run.id), kind="artifact_captured",
                    wall_time=datetime.now(timezone.utc), business_time=datetime.now(timezone.utc),
                    payload={"kind": "screenshot", "persona_id": persona_id, "url": f"/api/runs/{run.id}/artifacts/{session_id}" if persona_key else f"/api/runs/{run.id}/artifacts/final-page", "byte_count": screenshot.stat().st_size},
                ))
                if trace is not None:
                    trace.update(output={"status": result.status, "reason": result.reason, "steps": result.steps, "model_requests": result.model_requests, "invariant_verdict": verdict})
        finally:
            await browser.close()
            if model is not None:
                await model.aclose()
            tracer.flush()

    async def _apply_credentials(self, browser: PlaywrightBrowserSession, spec: AuthoredScenarioSpec, *, allow_absent: bool = False) -> None:
        for mapping in spec.product.credential_fields:
            value = os.getenv(mapping.env_var)
            if value is None:
                raise RuntimeError(f"missing credential environment variable: {mapping.env_var}")
            locator = browser.page.locator(mapping.selector)
            if await locator.count() == 0:
                if allow_absent:
                    # A resumed authenticated session often opens directly on
                    # an internal page, where login fields should not exist.
                    continue
                raise RuntimeError(f"credential selector was not found: {mapping.selector}")
            await locator.fill(value)

    @staticmethod
    def _requires_login_setup(spec: AuthoredScenarioSpec, requested_url: str, observation: Any) -> bool:
        """Refuse to invent credentials after an authentication redirect.

        A client must explicitly map selectors to environment variables.  We
        only block when the approved target actually redirects away and the
        page exposes a required password field, avoiding a false block for a
        normal public sign-up journey.
        """
        if spec.product.credential_fields:
            return False
        copy = f"{observation.title}\n{observation.visible_text}".casefold()
        auth_page = any(marker in copy for marker in ("sign in required", "login required", "access required"))
        redirected_to_password_form = (
            urlparse(requested_url).path != urlparse(observation.url).path
            and any(element.input_type == "password" and element.required for element in observation.elements)
        )
        return auth_page or redirected_to_password_form

    @staticmethod
    async def _verify(browser: PlaywrightBrowserSession, spec: AuthoredScenarioSpec) -> tuple[str, Any]:
        invariant = spec.invariant
        if invariant.kind == "url_contains":
            actual = browser.page.url
            return ("satisfied" if invariant.expected in actual else "confirmed", actual)
        if invariant.kind == "text_contains":
            actual = (await browser.page.locator("body").inner_text())[:12000]
            return ("satisfied" if invariant.expected in actual else "confirmed", {"text_present": invariant.expected in actual})
        visible = await browser.page.locator(invariant.expected).first.is_visible()
        return ("satisfied" if visible else "confirmed", {"visible": visible})

    async def _persist_verification(self, run: RunRecord, session_id: str, persona_id: str, spec: AuthoredScenarioSpec, verdict: str, actual: Any, observation_id: str) -> None:
        evidence_id = f"observation:{observation_id}"
        if verdict == "confirmed":
            await self.state.save_finding(Finding(
                id=str(uuid4()), run_id=run.id, session_id=session_id,
                invariant_id=spec.invariant.id, status=FindingStatus.CONFIRMED,
                expected={spec.invariant.kind: spec.invariant.expected}, actual=actual,
                evidence_ids=[evidence_id], verifier_version="dom-invariant-v1",
                replay_status=ReplayStatus.NOT_ATTEMPTED,
            ))
        await self.state.append_event(Event(
            id=str(uuid4()), run_id=run.id, persona_id=persona_id, session_id=session_id,
            sequence=await self.state.reserve_event_sequences(run.id), kind="verification_completed",
            wall_time=datetime.now(timezone.utc), business_time=datetime.now(timezone.utc),
            payload={"invariant_id": spec.invariant.id, "verdict": verdict, "expected": {spec.invariant.kind: spec.invariant.expected}, "actual": actual, "evidence_ids": [evidence_id]},
        ))

    def _screenshot_path(self, run_id: str, session_id: str | None = None):
        root = Path(self.settings.artifact_root)
        root.mkdir(parents=True, exist_ok=True)
        return root / (f"{run_id}-{session_id}-final-page.png" if session_id else f"{run_id}-final-page.png")

    def _browser_state_path(self, run_id: str, session_id: str) -> Path:
        root = Path(self.settings.artifact_root)
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{run_id}-{session_id}-state.json"

    async def _resume_url(self, run_id: str, session: SessionRecord, spec: AuthoredScenarioSpec, *, fallback: str) -> str:
        """Reopen the last *durably checkpointed* in-origin page on restart.

        The browser's cookies/local storage live in its saved Playwright state;
        the action event supplies the page URL.  We deliberately ignore URLs
        from incomplete actions and reject a URL outside the client-approved
        origin.
        """
        default = urljoin(spec.product.base_url + "/", fallback.lstrip("/"))
        if session.step_count == 0:
            return default
        expected = urlparse(spec.product.base_url)
        events = await self.state.list_events(run_id, limit=1000)
        for event in reversed(events):
            if event.session_id != session.id or event.kind != "tool_result":
                continue
            if event.payload.get("status") != "success":
                continue
            candidate = event.payload.get("data", {}).get("url") if isinstance(event.payload.get("data"), dict) else None
            if not isinstance(candidate, str):
                continue
            parsed = urlparse(candidate)
            if parsed.scheme == expected.scheme and parsed.netloc == expected.netloc:
                return candidate
        return default

    async def _save_browser_checkpoint(self, browser: PlaywrightBrowserSession, session: SessionRecord, event: Event) -> None:
        """Persist browser cookies/local storage after the matching DB checkpoint."""
        path = await browser.save_state()
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("browser checkpoint state was not persisted")

    async def _capture_screenshot(self, browser: PlaywrightBrowserSession, run_id: str, session_id: str | None = None):
        path = self._screenshot_path(run_id, session_id)
        await browser.page.screenshot(path=str(path), full_page=True)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("browser screenshot was not persisted")
        return path
