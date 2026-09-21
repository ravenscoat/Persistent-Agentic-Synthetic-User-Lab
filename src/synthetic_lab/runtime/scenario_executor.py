"""Live scenario executor used by API runs beyond the signup smoke test.

The scenario controller prepares a deterministic business state, while each
persona still enters through a real Playwright context and a PersonaAgent loop.
This keeps seeded-fault evaluation reproducible while exercising browser,
memory, event, and verification boundaries end to end.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import uvicorn

from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.config import Settings
from synthetic_lab.contracts import (
    Action, AgentDecision, BudgetConfig, DecisionKind, Event, Finding,
    FindingStatus, MemoryRecord, MemoryType, ModelResponse, PersonaRecord,
    ReplayStatus, RunRecord, SessionRecord, Trust, VerificationResult,
)
from synthetic_lab.demo import DemoStore, create_demo_app
from synthetic_lab.demo.postgres_store import PostgresDemoStore
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository
from synthetic_lab.llm import build_local_model
from synthetic_lab.observability import LangfuseTracer, TracedContextAssembler, TracedModelClient, TracedToolRegistry
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.runtime.demo_executor import _free_port
from synthetic_lab.verification import DemoReplayService, DemoVerificationContext, DemoVerifier


class RouteModel:
    """Small deterministic policy for the seeded scenario acceptance path."""

    def __init__(self, route: str, invariant_id: str) -> None:
        self.route = route
        self.invariant_id = invariant_id
        self.suspicion_raised = False

    async def decide(self, messages: Any, decision_schema: Any = None, generation_options: Any = None) -> ModelResponse:
        content = next((str(message.get("content", "")) for message in reversed(messages) if "URL: " in str(message.get("content", ""))), "")
        url = content.split("URL: ", 1)[1].split("\n", 1)[0] if "URL: " in content else ""
        if self.route.rstrip("/") not in url.rstrip("/"):
            decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="navigate", arguments={"url": self.route}))
        elif not self.suspicion_raised:
            self.suspicion_raised = True
            decision = AgentDecision(kind=DecisionKind.SUSPICION, invariant_id=self.invariant_id, summary=f"Observed state at {self.route} may violate {self.invariant_id}; request independent verification.")
        else:
            decision = AgentDecision(kind=DecisionKind.FINISH, summary=f"Independent verification completed for {self.route}.")
        return ModelResponse(decision=decision, model_id="scenario-controller", latency_ms=0)

    async def aclose(self) -> None:
        return None


class ScenarioExecutor:
    """Execute one seeded business scenario with one or more browser personas."""

    def __init__(self, state: Any, memory: Any, settings: Settings | None = None) -> None:
        self.state = state
        self.memory = memory
        self.settings = settings or Settings()

    async def __call__(self, run: RunRecord) -> None:
        now = datetime.now(timezone.utc)
        scenario = run.scenario_id
        fault = (run.config_snapshot or {}).get("fault") or self.settings.business_fault
        run_memory = self.memory if (run.config_snapshot or {}).get("memory_enabled", True) else InMemoryMemoryRepository()
        store = PostgresDemoStore(self.settings.postgres_dsn, fault=fault) if self.settings.postgres_dsn else DemoStore(fault=fault)
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(create_demo_app(store), host="127.0.0.1", port=port, log_level="error"))
        server_task = asyncio.create_task(server.serve())
        browsers: dict[str, PlaywrightBrowserSession] = {}
        try:
            await asyncio.sleep(0.25)
            account_id = f"account-{run.id[:12]}"
            operation_id = f"purchase-{run.id[:12]}"
            store.create_account(account_id, f"{run.id[:12]}@synthetic.test", "not-a-real-password")
            specs = self._persona_specs(run, scenario, account_id, now)
            resuming = False
            for persona_id in specs:
                try:
                    await self.state.get_session(f"{persona_id}-{run.id}")
                    resuming = True
                except KeyError:
                    pass
            if not resuming:
                await self._prepare_business(store, scenario, account_id, operation_id)
            invariant = {"trial_return": "trial_access_seven_days", "payment_retry": "purchase_idempotency", "ownership_transfer": "ownership_transfer", "interrupted_onboarding": "onboarding_persistence", "stale_task_status": "task_completion"}.get(scenario)
            if invariant is None:
                raise ValueError(f"unsupported scenario: {scenario}")
            trace_settings = self.settings if (run.config_snapshot or {}).get("trace_enabled", True) else self.settings.model_copy(update={"langfuse_host": None, "langfuse_public_key": None, "langfuse_secret_key": None})
            tracer = LangfuseTracer(trace_settings)
            tools = TracedToolRegistry(BrowserToolRegistry(browsers), tracer)
            sessions: dict[str, SessionRecord] = {}
            for persona_id, (persona, route) in specs.items():
                session = SessionRecord(id=f"{persona_id}-{run.id}", run_id=run.id, persona_id=persona_id, phase=scenario, due_business_time=now)
                sessions[persona_id] = session
                try:
                    await self.state.enqueue_session(session)
                except Exception:
                    # PostgreSQL surfaces duplicate IDs as a driver error,
                    # while the in-memory backend uses ValueError. A restart
                    # can safely continue only when the durable checkpoint is
                    # readable after either signal.
                    try:
                        session = await self.state.get_session(session.id)
                    except KeyError:
                        raise
                try:
                    await run_memory.append(MemoryRecord(id=f"{persona_id}-{run.id}-expectation", run_id=run.id, persona_id=persona_id, type=MemoryType.ENTITY, text=persona.goal, trust=Trust.VERIFIED, valid_from=now))
                except ValueError:
                    pass
                browser = PlaywrightBrowserSession(run.id, session.id, f"http://127.0.0.1:{port}", artifact_root=self.settings.artifact_root)
                await browser.start()
                await browser.page.context.add_cookies([
                    {"name": "account_id", "value": account_id, "url": f"http://127.0.0.1:{port}", "httpOnly": True},
                    {"name": "member_id", "value": persona_id, "url": f"http://127.0.0.1:{port}", "httpOnly": True},
                ])
                await browser.page.goto(f"http://127.0.0.1:{port}/dashboard")
                browsers[persona_id] = browser

            async def execute(persona_id: str, pair: tuple[PersonaRecord, str]) -> None:
                persona, route = pair
                browser = browsers[persona_id]
                observation = await browser.observe()
                raw_model = build_local_model(self.settings) if (run.config_snapshot or {}).get("agent_model") == "qwen" else RouteModel(route, invariant)
                model = TracedModelClient(raw_model, tracer)
                context = TracedContextAssembler(MemoryContextAssembler(run_memory, memory_limit=2, tool_registry=tools), tracer)
                async def verify_suspicion(suspicion: Event) -> str:
                    requested = suspicion.payload.get("invariant_id")
                    operation = operation_id if scenario == "payment_retry" else None
                    if scenario == "ownership_transfer":
                        # This is a two-sided permission assertion, not a
                        # single global check duplicated by both personas.
                        expected = "member" if persona_id == "old-owner" else "owner"
                        actual = store.role(account_id, persona_id)
                        verification = VerificationResult(
                            verdict="satisfied" if actual == expected else "confirmed",
                            expected=expected,
                            actual=actual,
                            evidence_ids=[],
                        )
                    else:
                        verification = await DemoVerifier().check(requested, DemoVerificationContext(store, account_id, operation))
                    finding = None
                    if verification.verdict != "satisfied":
                        status = FindingStatus.CONFIRMED if verification.verdict == "confirmed" else FindingStatus.INCONCLUSIVE
                        finding = Finding(id=str(uuid4()), run_id=run.id, session_id=suspicion.session_id or sessions[persona_id].id, invariant_id=requested, status=status, expected=verification.expected, actual=verification.actual, evidence_ids=[suspicion.id, *verification.evidence_ids], verifier_version=DemoVerifier.version, replay_status=ReplayStatus.NOT_ATTEMPTED)
                    if finding is not None and finding.status is FindingStatus.CONFIRMED:
                        replay = await DemoReplayService().replay(finding, fault=fault)
                        finding = finding.model_copy(update={"replay_status": replay.status})
                    if finding is not None:
                        await self.state.save_finding(finding)
                    await self.state.append_event(Event(id=str(uuid4()), run_id=run.id, persona_id=persona_id, session_id=suspicion.session_id, sequence=await self.state.reserve_event_sequences(run.id), kind="verification_completed", wall_time=datetime.now(timezone.utc), business_time=datetime.now(timezone.utc), payload={"suspicion_event_id": suspicion.id, "finding_id": finding.id if finding else None, "invariant_id": requested, "verdict": verification.verdict, "expected": verification.expected, "actual": verification.actual}))
                    return verification.verdict
                decision_schema = AgentDecision.model_json_schema()
                decision_schema["properties"]["invariant_id"] = {"anyOf": [{"const": invariant}, {"type": "null"}], "default": None}
                qwen_mode = (run.config_snapshot or {}).get("agent_model") == "qwen"
                agent = PersonaAgent(model=model, context=context, tools=tools, state=self.state, memory=run_memory, budgets=BudgetConfig(max_steps=4, max_model_requests=5, output_tokens=256 if qwen_mode else 160), completion_check=lambda: _at_route(browser, route), suspicion_handler=verify_suspicion, decision_schema=decision_schema if qwen_mode else None)
                with tracer.run_trace(run.id, persona_id, sessions[persona_id].id) as trace:
                    agent.finish_after_verification = True
                    result = await agent.run(persona, sessions[persona_id], observation)
                    if trace is not None:
                        trace.update(output={"status": result.status, "reason": result.reason, "steps": result.steps, "model_requests": result.model_requests})
                await model.aclose()
                if result.status != "completed":
                    raise RuntimeError(f"Persona {persona_id} {result.status}: {result.reason}")

            await asyncio.gather(*(execute(persona_id, pair) for persona_id, pair in specs.items()))
        finally:
            for browser in browsers.values():
                await browser.close()
            if 'tracer' in locals():
                tracer.flush()
            server.should_exit = True
            await server_task
            store.close()

    async def _prepare_business(self, store: Any, scenario: str, account_id: str, operation_id: str) -> None:
        if scenario == "trial_return":
            # The business clock is controller-owned. A recovered run may
            # re-enter this setup, so do not apply the same transition twice
            # when the durable business store already contains its result.
            store.advance_days(6)
        elif scenario == "payment_retry":
            if store.ledger_for(operation_id).charges == 0:
                store.purchase(account_id, operation_id, 2500)
        elif scenario == "ownership_transfer":
            store.transfer_owner(account_id, "old-owner", "new-owner")
        elif scenario == "interrupted_onboarding":
            store.set_onboarding_step(account_id, 2)
        elif scenario == "stale_task_status":
            store.create_project(account_id, "project-1", "Payments migration")
            store.create_task("project-1", "task-1", "Verify payment retry")
            store.complete_task("task-1")

    @staticmethod
    def _persona_specs(run: RunRecord, scenario: str, account_id: str, now: datetime) -> dict[str, tuple[PersonaRecord, str]]:
        expectations = {
            "trial_return": "A seven-day trial must still be active on day six.",
            "payment_retry": "Retrying the same purchase operation must produce exactly one recorded charge.",
            "ownership_transfer": "After transfer, the former owner must be denied owner-only access and the new owner must be allowed.",
            "interrupted_onboarding": "The completed onboarding step must still be 2 after returning.",
            "stale_task_status": "A completed task must be displayed as completed rather than open.",
        }
        instruction = f" Expected invariant: {expectations.get(scenario, scenario)} If the visible state may violate it, return a suspicion decision for the relevant invariant; otherwise finish."
        if scenario == "ownership_transfer":
            return {pid: (PersonaRecord(id=pid, run_id=run.id, kind="administrator", goal=f"Navigate to /api/owner-only and check owner access as {pid} after transfer.{instruction}", application_account_id=account_id, allowed_tool_names=["navigate"]), "/api/owner-only") for pid in ("old-owner", "new-owner")}
        routes = {"trial_return": "/dashboard", "payment_retry": "/billing", "interrupted_onboarding": "/dashboard", "stale_task_status": "/tasks"}
        persona_id = f"persona-{run.id[:8]}"
        return {persona_id: (PersonaRecord(id=persona_id, run_id=run.id, kind=scenario, goal=f"Navigate to {routes[scenario]}, inspect the {scenario} workflow, and report what is visible.{instruction}", application_account_id=account_id, allowed_tool_names=["navigate"]), routes[scenario])}


async def _at_route(browser: PlaywrightBrowserSession, route: str) -> bool:
    return browser.page.url.rstrip("/").endswith(route.rstrip("/"))
