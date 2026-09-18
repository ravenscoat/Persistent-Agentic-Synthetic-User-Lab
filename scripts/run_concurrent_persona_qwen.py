"""Run two isolated Qwen browser personas concurrently through the scheduler.

The harness provisions one controlled ownership transfer, then lets the former
and new owners independently return through the normal agent loop.  Their only
permitted browser action is navigation to the protected endpoint.  That keeps
the model task narrow enough to test concurrency, durable memory, and evidence
without disguising a deterministic policy as an LLM run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn

from synthetic_lab.browser.tools import BrowserToolRegistry, PlaywrightBrowserSession
from synthetic_lab.config import Settings
from synthetic_lab.contracts import (
    Action,
    AgentDecision,
    BudgetConfig,
    DecisionKind,
    Event,
    Finding,
    FindingStatus,
    MemoryRecord,
    MemoryType,
    ModelResponse,
    PersonaRecord,
    ReplayStatus,
    RunRecord,
    SessionRecord,
    Trust,
)
from synthetic_lab.demo import create_demo_app
from synthetic_lab.demo.postgres_store import PostgresDemoStore
from synthetic_lab.llm import build_local_model
from synthetic_lab.memory import with_optional_qdrant
from synthetic_lab.memory.context import MemoryContextAssembler
from synthetic_lab.reporting.reports import ReportBuilder
from synthetic_lab.runtime.agent import PersonaAgent
from synthetic_lab.runtime.scheduler import DurableScheduler
from synthetic_lab.storage import PostgresMemoryRepository, PostgresStateRepository
from synthetic_lab.verification import DemoReplayService, DemoVerificationContext, DemoVerifier


class DirectedCheckModel:
    """Explicit test-only policy for validating the harness without Ollama."""

    async def decide(self, messages, decision_schema=None, generation_options=None) -> ModelResponse:
        content = str(messages[-1]["content"])
        if "/api/owner-only" in content.split("URL: ", 1)[-1].split("\n", 1)[0]:
            decision = AgentDecision(kind=DecisionKind.FINISH, summary="Protected access check is complete.")
        else:
            decision = AgentDecision(
                kind=DecisionKind.ACTION,
                action=Action(id=str(uuid4()), tool_name="navigate", arguments={"url": "/api/owner-only"}),
            )
        return ModelResponse(decision=decision, model_id="directed-test-only", latency_ms=0)


async def _append_event(state: PostgresStateRepository, run_id: str, kind: str, payload: dict[str, Any]) -> Event:
    now = datetime.now(timezone.utc)
    event = Event(
        id=str(uuid4()), run_id=run_id, sequence=await state.reserve_event_sequences(run_id),
        kind=kind, wall_time=now, business_time=now, payload=payload,
    )
    await state.append_event(event)
    return event


def _owner_status(body: str) -> int | None:
    if "owner access granted" in body:
        return 200
    if "owner access required" in body:
        return 403
    return None


async def main(*, fault: str | None, scripted: bool, model_concurrency: int) -> int:
    dsn = os.getenv("SUL_POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("SUL_POSTGRES_DSN is required for the durable concurrent-persona proof")
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    settings = Settings().model_copy(update={"model_concurrency": model_concurrency})
    schema = f"sul_eval_{uuid4().hex}"
    run_id, now = str(uuid4()), datetime.now(timezone.utc)
    account_id = "account-1"
    old_id, new_id = "old-owner", "new-owner"
    store = PostgresDemoStore(dsn, fault=fault, schema=schema)
    state = PostgresStateRepository.from_dsn(dsn, schema=schema)
    memory: Any = PostgresMemoryRepository.from_dsn(dsn, schema=schema)
    memory = with_optional_qdrant(memory, settings)
    server = uvicorn.Server(uvicorn.Config(create_demo_app(store), host="127.0.0.1", port=8014, log_level="error"))
    server_task: asyncio.Task[Any] | None = None
    old_browser = new_browser = model = None
    try:
        await store.apply_migration(root / "migrations" / "003_demo_business_postgres.sql")
        await state.apply_migration(root / "migrations" / "002_initial_postgres.sql")
        await state.create_run(RunRecord(
            id=run_id, scenario_id="ownership_transfer", created_at=now, business_time=now,
            config_snapshot={"fault": fault, "model_concurrency": model_concurrency},
            model_metadata={"mode": "scripted" if scripted else "qwen"},
        ))
        server_task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.3)
        origin = "http://127.0.0.1:8014"

        # Test harness setup: this is deliberately outside the LLM evaluation.
        old_browser = PlaywrightBrowserSession(run_id, "old-owner-session", origin, artifact_root=root / "artifacts")
        await old_browser.start()
        await old_browser.page.goto(f"{origin}/signup")
        await old_browser.page.locator("input[name=email]").fill("owner@example.test")
        await old_browser.page.locator("input[name=password]").fill("not-a-real-password")
        await old_browser.page.locator("button[type=submit]").click()
        await old_browser.page.locator("button:has-text('Transfer ownership')").click()
        account_id = next(cookie["value"] for cookie in await old_browser.page.context.cookies() if cookie["name"] == "account_id")

        new_browser = PlaywrightBrowserSession(run_id, "new-owner-session", origin, artifact_root=root / "artifacts")
        await new_browser.start()
        await new_browser.page.context.add_cookies([
            {"name": "account_id", "value": account_id, "url": origin, "httpOnly": True},
            {"name": "member_id", "value": new_id, "url": origin, "httpOnly": True},
        ])
        await new_browser.page.goto(f"{origin}/dashboard")
        await _append_event(state, run_id, "ownership_transfer_prepared", {
            "account_id": account_id, "fault": fault,
            "source": "controlled_test_harness",
        })

        personas = {
            old_id: PersonaRecord(
                id=old_id, run_id=run_id, kind="former_owner", application_account_id=account_id,
                goal="Ownership has already been transferred. Navigate exactly once to /api/owner-only to check whether former-owner access is denied. Do not use any other route. Once that page is visible, finish.",
                allowed_tool_names=["navigate"],
            ),
            new_id: PersonaRecord(
                id=new_id, run_id=run_id, kind="new_owner", application_account_id=account_id,
                goal="Ownership has already been transferred to you. Navigate exactly once to /api/owner-only to check whether new-owner access is granted. Do not use any other route. Once that page is visible, finish.",
                allowed_tool_names=["navigate"],
            ),
        }
        sessions = {
            old_id: SessionRecord(id="old-owner-session", run_id=run_id, persona_id=old_id, phase="return_access_check", due_business_time=now),
            new_id: SessionRecord(id="new-owner-session", run_id=run_id, persona_id=new_id, phase="return_access_check", due_business_time=now),
        }
        for session in sessions.values():
            await state.enqueue_session(session)
        await memory.append(MemoryRecord(
            id="old-owner-transfer", run_id=run_id, persona_id=old_id, type=MemoryType.ENTITY,
            text="Ownership was transferred to new-owner. My former-owner access should be denied.", trust=Trust.VERIFIED, valid_from=now,
        ))
        await memory.append(MemoryRecord(
            id="new-owner-transfer", run_id=run_id, persona_id=new_id, type=MemoryType.ENTITY,
            text="Ownership was transferred to me. My new-owner access should be granted.", trust=Trust.VERIFIED, valid_from=now,
        ))

        browsers = {old_id: old_browser, new_id: new_browser}
        tools = BrowserToolRegistry(browsers)
        observations = {old_id: await old_browser.observe(), new_id: await new_browser.observe()}
        model = DirectedCheckModel() if scripted else build_local_model(settings)
        results: dict[str, Any] = {}

        def factory(leased: SessionRecord):
            persona = personas[leased.persona_id]
            browser = browsers[persona.id]

            async def execute(session: SessionRecord) -> None:
                async def check_complete() -> bool:
                    return browser.page is not None and browser.page.url.endswith("/api/owner-only")

                agent = PersonaAgent(
                    model=model,
                    context=MemoryContextAssembler(memory, memory_limit=1, tool_registry=tools),
                    tools=tools, state=state, memory=memory,
                    budgets=BudgetConfig(max_steps=4, max_model_requests=5, output_tokens=160),
                    completion_check=check_complete,
                )
                results[persona.id] = await agent.run(persona, session, observations[persona.id])

            return execute

        scheduler = DurableScheduler(state, factory, lease_seconds=120)
        leased = await asyncio.gather(
            scheduler.run_once("qwen-worker-old", now=now),
            scheduler.run_once("qwen-worker-new", now=now),
        )
        body_by_persona = {
            old_id: await old_browser.page.locator("body").inner_text(),
            new_id: await new_browser.page.locator("body").inner_text(),
        }
        statuses = {persona_id: _owner_status(body) for persona_id, body in body_by_persona.items()}
        verification = await DemoVerifier().check("ownership_transfer", DemoVerificationContext(store, account_id))
        events = await state.list_events(run_id, limit=1000)
        tool_events = [event for event in events if event.kind == "tool_result"]
        retrieved = {
            persona_id: sorted({memory_id for event in tool_events if event.persona_id == persona_id for memory_id in event.payload.get("retrieved_memory_ids", [])})
            for persona_id in personas
        }
        expected_verdict = "confirmed" if fault else "satisfied"
        expected_statuses = {old_id: 200 if fault else 403, new_id: 200}
        unique_sequences = len({event.sequence for event in events}) == len(events)
        # Later agent turns may retrieve the persona's own tool log as well as
        # its seeded transfer memory.  Isolation means each required memory is
        # present and the other persona's private transfer memory is absent.
        memory_isolated = (
            "old-owner-transfer" in retrieved[old_id]
            and "new-owner-transfer" in retrieved[new_id]
            and "new-owner-transfer" not in retrieved[old_id]
            and "old-owner-transfer" not in retrieved[new_id]
        )
        finding_report: dict[str, Any] | None = None
        if verification.verdict == "confirmed":
            evidence = [event.id for event in tool_events if event.persona_id == old_id]
            finding = Finding(
                id=str(uuid4()), run_id=run_id, session_id=sessions[old_id].id,
                invariant_id="ownership_transfer", status=FindingStatus.CONFIRMED,
                expected=verification.expected, actual=verification.actual, evidence_ids=evidence,
                verifier_version="demo-membership-check-v1",
            )
            replay = await DemoReplayService().replay(finding, fault=fault)
            finding = finding.model_copy(update={"replay_status": replay.status})
            await state.save_finding(finding)
            report = ReportBuilder().build(
                finding, events, [],
                explanation="The verifier read the authoritative membership state independently of the browser or model output.",
            )
            report_path = root / "artifacts" / f"two-persona-finding-{finding.id}.json"
            report_path.write_text(json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8")
            finding_report = {"path": str(report_path), "replay_status": replay.status.value, "replay_reason": replay.reason}
        await _append_event(state, run_id, "ownership_transfer_verified", {
            "verdict": verification.verdict, "expected": verification.expected, "actual": verification.actual,
        })
        old_state, new_state = await old_browser.save_state(), await new_browser.save_state()
        payload = {
            "run_id": run_id, "schema": schema, "fault": fault,
            "model_mode": "scripted" if scripted else "qwen",
            "configured_model_concurrency": model_concurrency,
            "scheduler_leases_acquired": leased,
            "agent_results": {persona_id: value.__dict__ for persona_id, value in results.items()},
            "endpoint_statuses": statuses,
            "retrieved_memory_ids": retrieved,
            "memory_isolated": memory_isolated,
            "event_count": len(events),
            "event_sequences": [event.sequence for event in events],
            "event_sequences_unique": unique_sequences,
            "model_failures": [event.payload for event in events if event.kind == "model_failed"],
            "verification": verification.model_dump(mode="json"),
            "finding_report": finding_report,
            "browser_states": {old_id: str(old_state), new_id: str(new_state)},
            "duration_seconds": round(time.monotonic() - started, 2),
        }
        artifact = root / "artifacts" / f"concurrent-persona-{run_id}.json"
        artifact.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(json.dumps(payload, indent=2, default=str))
        complete = set(results) == {old_id, new_id} and all(result.status == "completed" for result in results.values())
        return 0 if complete and all(leased) and statuses == expected_statuses and memory_isolated and unique_sequences and verification.verdict == expected_verdict else 1
    finally:
        if old_browser is not None:
            await old_browser.close()
        if new_browser is not None:
            await new_browser.close()
        if model is not None and hasattr(model, "aclose"):
            await model.aclose()
        server.should_exit = True
        if server_task is not None:
            await server_task
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", choices=["owner_transfer_leak", "none"], default="owner_transfer_leak")
    parser.add_argument("--scripted", action="store_true", help="test the harness without Ollama; never use this for a Qwen claim")
    parser.add_argument(
        "--model-concurrency", type=int, default=1,
        help="maximum simultaneous Ollama generations; use 1 on a single GPU while browser workers still run concurrently",
    )
    args = parser.parse_args()
    if args.model_concurrency < 1:
        parser.error("--model-concurrency must be at least 1")
    raise SystemExit(asyncio.run(main(fault=None if args.fault == "none" else args.fault, scripted=args.scripted, model_concurrency=args.model_concurrency)))
