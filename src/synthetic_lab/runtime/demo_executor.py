from __future__ import annotations

import ast
import asyncio
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn

from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.config import Settings
from synthetic_lab.contracts import Action, AgentDecision, BudgetConfig, DecisionKind, ModelResponse, PersonaRecord, RunRecord, SessionRecord
from synthetic_lab.demo import DemoStore, create_demo_app
from synthetic_lab.demo.postgres_store import PostgresDemoStore
from synthetic_lab.llm import build_local_model
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.observability import LangfuseTracer, TracedContextAssembler, TracedModelClient, TracedToolRegistry


class SignupSmokeModel:
    """Small deterministic fallback used when a local model is unavailable."""

    def __init__(self) -> None:
        self.step = 0

    @staticmethod
    def _element(content: str, name: str) -> str | None:
        marker = content.find("Elements: ")
        if marker < 0:
            return None
        try:
            elements = ast.literal_eval(content[marker + len("Elements: "):])
        except (SyntaxError, ValueError):
            return None
        for item in elements:
            if name.casefold() in str(item.get("name", "")).casefold():
                return str(item.get("target"))
        return None

    async def decide(self, messages, decision_schema=None, generation_options=None) -> ModelResponse:
        page = str(messages[1]["content"])
        url = page.split("URL: ", 1)[1].split("\n", 1)[0]
        def action(tool: str, target: str | None = None, value: str | None = None) -> AgentDecision:
            arguments: dict[str, Any] = {"target": target}
            if value is not None:
                arguments["value"] = value
            return AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name=tool, arguments=arguments))
        if url.endswith("/"):
            decision = action("navigate", value="/signup")
            decision = decision.model_copy(update={"action": decision.action.model_copy(update={"arguments": {"url": "/signup"}})})
        elif "/signup" in url:
            email = self._element(page, "email")
            password = self._element(page, "password")
            submit = self._element(page, "create account")
            if self.step == 0 and email:
                self.step = 1
                decision = action("fill", email, "operator-demo@example.test")
            elif self.step == 1 and password:
                self.step = 2
                decision = action("fill", password, "not-a-real-password")
            elif self.step == 2 and submit:
                self.step = 3
                decision = action("click", submit)
            else:
                decision = AgentDecision(kind=DecisionKind.FINISH, summary="signup workflow complete")
        else:
            decision = AgentDecision(kind=DecisionKind.FINISH, summary="account dashboard reached")
        return ModelResponse(decision=decision, model_id="signup-smoke", latency_ms=0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class DemoAgentExecutor:
    """Run one real browser persona for an API-created run.

    The browser and controlled demo application are isolated per run. The
    default model is local Ollama/Qwen; setting ``model_mode=smoke`` gives a
    deterministic fallback useful for UI demos and CI.
    """

    def __init__(self, state: Any, memory: InMemoryMemoryRepository, settings: Settings | None = None) -> None:
        self.state = state
        self.memory = memory
        self.settings = settings or Settings()

    async def __call__(self, run: RunRecord) -> None:
        now = datetime.now(timezone.utc)
        persona_id = f"operator-{run.id[:8]}"
        session_id = f"session-{run.id[:8]}"
        persona = PersonaRecord(
            id=persona_id,
            run_id=run.id,
            kind="new_customer",
            goal="Create an account using the signup page. Fill each empty required field once, submit the form, and finish only after the Account dashboard is visible.",
            application_account_id="account-1",
            allowed_tool_names=["observe_page", "navigate", "click", "fill"],
        )
        session = SessionRecord(id=session_id, run_id=run.id, persona_id=persona_id, phase="signup", due_business_time=now)
        try:
            await self.state.enqueue_session(session)
        except ValueError:
            session = await self.state.get_session(session.id)
        # Use the same durable business backend as the deployment when a DSN
        # is configured; retain SQLite for fast isolated local runs/tests.
        store = PostgresDemoStore(self.settings.postgres_dsn, fault=self.settings.business_fault) if self.settings.postgres_dsn else DemoStore(fault=self.settings.business_fault)
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(create_demo_app(store), host="127.0.0.1", port=port, log_level="error"))
        server_task = asyncio.create_task(server.serve())
        browser: PlaywrightBrowserSession | None = None
        model: Any | None = None
        try:
            await asyncio.sleep(0.25)
            origin = f"http://127.0.0.1:{port}"
            browser = PlaywrightBrowserSession(run.id, session_id, origin, artifact_root=self.settings.artifact_root)
            await browser.start()
            await browser.page.goto(f"{origin}/")
            observation = await browser.observe()
            raw_model = SignupSmokeModel() if self.settings.model_name.casefold() == "smoke" else build_local_model(self.settings)
            tracer = LangfuseTracer(self.settings)
            tools = TracedToolRegistry(BrowserToolRegistry({persona_id: browser}), tracer)
            model = TracedModelClient(raw_model, tracer)

            async def complete() -> bool:
                return store.account_exists() and browser is not None and browser.page.url.endswith("/dashboard")

            agent = PersonaAgent(
                model=model,
                context=TracedContextAssembler(MemoryContextAssembler(self.memory, tool_registry=tools), tracer),
                tools=tools,
                state=self.state,
                memory=self.memory,
                budgets=BudgetConfig(max_steps=10, max_model_requests=12, output_tokens=256),
                completion_check=complete,
            )
            with tracer.run_trace(run.id, persona_id, session_id) as trace:
                result = await agent.run(persona, session, observation)
                if trace is not None:
                    trace.update(output={"status": result.status, "reason": result.reason, "steps": result.steps, "model_requests": result.model_requests})
            # Browser state is useful evidence, but a read-only artifact
            # directory must not turn a successful agent run into a failure.
            try:
                await browser.save_state()
            except (OSError, PermissionError):
                pass
        finally:
            if browser is not None:
                await browser.close()
            if model is not None and hasattr(model, "aclose"):
                await model.aclose()
            if 'tracer' in locals():
                tracer.flush()
            server.should_exit = True
            await server_task
            store.close()
