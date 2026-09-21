"""Opt-in browser proof for a reusable client profile with two personas."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn

from synthetic_lab.config import Settings
from synthetic_lab.contracts import Action, AgentDecision, DecisionKind, ModelResponse, RunRecord
from synthetic_lab.demo import create_demo_app
from synthetic_lab.demo.store import DemoStore
from synthetic_lab.runtime.product_executor import ProductAdapterExecutor
from synthetic_lab.storage import InMemoryStateRepository
from synthetic_lab.storage.in_memory import InMemoryMemoryRepository


class _SignupModel:
    """Only chooses the visible submission; credentials come from env vars."""

    model_name = "custom-profile-browser-proof"

    async def decide(self, messages, decision_schema=None, generation_options=None) -> ModelResponse:
        content = "\n".join(str(message.get("content", "")) for message in messages)
        target = re.search(r"'target': '([^']+)', 'role': '[^']+', 'name': 'Create account'", content)
        if target:
            decision = AgentDecision(kind=DecisionKind.ACTION, action=Action(id=str(uuid4()), tool_name="click", arguments={"target": target.group(1)}))
        else:
            decision = AgentDecision(kind=DecisionKind.FINISH, summary="The expected dashboard is visible.")
        return ModelResponse(decision=decision, latency_ms=1, model_id=self.model_name)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_custom_profile_runs_two_isolated_browser_personas(tmp_path, monkeypatch) -> None:
    if os.getenv("SUL_RUN_BROWSER_INTEGRATION") != "1":
        pytest.skip("set SUL_RUN_BROWSER_INTEGRATION=1 to run the custom profile browser proof")
    monkeypatch.setenv("PRIMARY_TEST_EMAIL", "primary@example.test")
    monkeypatch.setenv("PRIMARY_TEST_PASSWORD", "safe-primary-password")
    monkeypatch.setenv("TEAMMATE_TEST_EMAIL", "teammate@example.test")
    monkeypatch.setenv("TEAMMATE_TEST_PASSWORD", "safe-teammate-password")

    store = DemoStore()
    server = uvicorn.Server(uvicorn.Config(create_demo_app(store), host="127.0.0.1", port=8017, log_level="error"))
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.25)
    try:
        state, memory = InMemoryStateRepository(), InMemoryMemoryRepository()
        now = datetime.now(timezone.utc)
        run = RunRecord(
            id="custom-profile-personas",
            scenario_id="custom:flowboard",
            created_at=now,
            business_time=now,
            config_snapshot={
                "trace_enabled": False,
                "authored_scenario": {
                    "product": {
                        "name": "Flowboard", "base_url": "http://127.0.0.1:8017", "start_path": "/signup",
                        "credential_fields": [
                            {"selector": "input[name=email]", "env_var": "PRIMARY_TEST_EMAIL"},
                            {"selector": "input[name=password]", "env_var": "PRIMARY_TEST_PASSWORD"},
                        ],
                    },
                    "persona": {"kind": "owner", "goal": "Create an owner workspace and reach its dashboard."},
                    "invariant": {"id": "owner_dashboard", "kind": "text_contains", "expected": "Good afternoon"},
                    "required_clicks": ["Create account"],
                    "max_steps": 5,
                    "additional_personas": [{
                        "id": "teammate",
                        "persona": {"kind": "teammate", "goal": "Create a separate teammate workspace and reach its dashboard."},
                        "invariant": {"id": "teammate_dashboard", "kind": "text_contains", "expected": "Good afternoon"},
                        "required_clicks": ["Create account"],
                        "start_path": "/signup",
                        "credential_fields": [
                            {"selector": "input[name=email]", "env_var": "TEAMMATE_TEST_EMAIL"},
                            {"selector": "input[name=password]", "env_var": "TEAMMATE_TEST_PASSWORD"},
                        ],
                    }],
                },
            },
        )
        await state.create_run(run)
        executor = ProductAdapterExecutor(state, memory, settings=Settings(artifact_root=tmp_path), model_factory=lambda _: _SignupModel())
        await executor(run)
        primary = await state.get_session("session-custom-p-primary")
        teammate = await state.get_session("session-custom-p-teammate")
        assert primary.status.value == teammate.status.value == "COMPLETED"
        events = await state.list_events(run.id, limit=100)
        screenshots = [event.payload for event in events if event.kind == "artifact_captured"]
        assert {item["persona_id"] for item in screenshots} == {"custom-custom-p-primary", "custom-custom-p-teammate"}
        assert (tmp_path / "custom-profile-personas-session-custom-p-primary-final-page.png").is_file()
        assert (tmp_path / "custom-profile-personas-session-custom-p-teammate-final-page.png").is_file()
        assert await state.list_findings(run.id) == []
    finally:
        server.should_exit = True
        await server_task
