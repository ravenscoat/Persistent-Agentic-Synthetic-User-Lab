"""Exercise ownership transfer through two isolated Playwright browser contexts."""
from __future__ import annotations

import asyncio
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import uvicorn

from synthetic_lab.browser.tools import PlaywrightBrowserSession
from synthetic_lab.contracts import BudgetConfig, MemoryRecord, MemoryType, Observation, PersonaRecord, SessionRecord, Trust
from synthetic_lab.demo import DemoStore, create_demo_app
from synthetic_lab.memory import MemoryContextAssembler
from synthetic_lab.storage import InMemoryMemoryRepository
from synthetic_lab.verification import DemoVerificationContext, DemoVerifier


async def main(fault: str | None = None) -> int:
    run_id, now = str(uuid4()), datetime.now(timezone.utc)
    store = DemoStore(fault=fault)
    memory = InMemoryMemoryRepository()
    server = uvicorn.Server(uvicorn.Config(create_demo_app(store), host="127.0.0.1", port=8013, log_level="error"))
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.25)
    owner = new_owner = None
    try:
        origin = "http://127.0.0.1:8013"
        owner = PlaywrightBrowserSession(run_id, "owner-session", origin, artifact_root="artifacts")
        await owner.start()
        await owner.page.goto(f"{origin}/signup")
        await owner.page.locator("input[name=email]").fill("owner@example.test")
        await owner.page.locator("input[name=password]").fill("not-a-real-password")
        await owner.page.locator("button[type=submit]").click()
        await owner.page.locator("button:has-text('Transfer ownership')").click()
        account_id = next(cookie["value"] for cookie in await owner.page.context.cookies() if cookie["name"] == "account_id")

        new_owner = PlaywrightBrowserSession(run_id, "new-owner-session", origin, artifact_root="artifacts")
        await new_owner.start()
        await new_owner.page.context.add_cookies([
            {"name": "account_id", "value": account_id, "url": origin, "httpOnly": True},
            {"name": "member_id", "value": "new-owner", "url": origin, "httpOnly": True},
        ])
        old_response = await owner.page.goto(f"{origin}/api/owner-only")
        new_response = await new_owner.page.goto(f"{origin}/api/owner-only")

        await memory.append(MemoryRecord(id="owner-transfer", run_id=run_id, persona_id="old-owner", type=MemoryType.ENTITY, text="Ownership moved to new-owner; my owner access should be denied.", trust=Trust.VERIFIED, valid_from=now))
        await memory.append(MemoryRecord(id="new-owner-transfer", run_id=run_id, persona_id="new-owner", type=MemoryType.ENTITY, text="Ownership moved to me; my owner access should be allowed.", trust=Trust.VERIFIED, valid_from=now))
        observation = Observation(id="return", run_id=run_id, session_id="return", url=f"{origin}/api/owner-only", title="Owner access", visible_text="Check owner-only access after transfer.", captured_at=now)
        def persona(member_id: str) -> PersonaRecord:
            return PersonaRecord(id=member_id, run_id=run_id, kind="administrator", goal="Verify owner access after transfer.", application_account_id=account_id)
        def session(member_id: str) -> SessionRecord:
            return SessionRecord(id=f"{member_id}-return", run_id=run_id, persona_id=member_id, phase="return", due_business_time=now)
        owner_context = await MemoryContextAssembler(memory).build(persona("old-owner"), session("old-owner"), observation, BudgetConfig(context_tokens=1200, output_tokens=128, safety_tokens=128))
        new_context = await MemoryContextAssembler(memory).build(persona("new-owner"), session("new-owner"), observation, BudgetConfig(context_tokens=1200, output_tokens=128, safety_tokens=128))
        verified = await DemoVerifier().check("ownership_transfer", DemoVerificationContext(store, account_id))
        owner_state = await owner.save_state()
        new_owner_state = await new_owner.save_state()
        payload = {
            "old_owner_http_status": old_response.status if old_response else None,
            "new_owner_http_status": new_response.status if new_response else None,
            "fault": fault,
            "old_owner_role": store.role(account_id, "old-owner"),
            "new_owner_role": store.role(account_id, "new-owner"),
            "old_owner_memory_ids": owner_context.included_memory_ids,
            "new_owner_memory_ids": new_context.included_memory_ids,
            "verifier_verdict": verified.verdict,
            "owner_browser_state": str(owner_state),
            "new_owner_browser_state": str(new_owner_state),
        }
        artifact = Path("artifacts") / f"two-persona-browser-{run_id}.json"
        artifact.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        expected_verdict = "confirmed" if fault else "satisfied"
        expected_old_status = 200 if fault else 403
        return 0 if verified.verdict == expected_verdict and payload["old_owner_http_status"] == expected_old_status and payload["new_owner_http_status"] == 200 and payload["old_owner_memory_ids"] == ["owner-transfer"] and payload["new_owner_memory_ids"] == ["new-owner-transfer"] else 1
    finally:
        if owner:
            await owner.close()
        if new_owner:
            await new_owner.close()
        server.should_exit = True
        await server_task
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", choices=["owner_transfer_leak"])
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.fault)))
