"""Two-persona ownership-transfer scenario with isolated sessions and memory."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from synthetic_lab.contracts import BudgetConfig, MemoryRecord, MemoryType, Observation, PersonaRecord, SessionRecord, Trust
from synthetic_lab.demo import DemoStore, create_demo_app
from synthetic_lab.memory import MemoryContextAssembler
from synthetic_lab.storage import InMemoryMemoryRepository
from synthetic_lab.verification import DemoVerificationContext, DemoVerifier


async def run_ownership_transfer(*, fault: str | None = None) -> dict[str, object]:
    """Exercise two independent user identities against one account."""
    now = datetime.now(timezone.utc)
    run_id, account_id = "transfer-eval", "account-1"
    old_id, new_id = "old-owner", "new-owner"
    memory = InMemoryMemoryRepository()
    store = DemoStore(fault=fault)
    try:
        owner = TestClient(create_demo_app(store))
        owner.post("/signup", data={"email": "owner@example.test", "password": "not-a-real-password"})
        owner.cookies.set("account_id", account_id)
        owner.cookies.set("member_id", old_id)
        store.invite(account_id, "invite-1", "new-owner@example.test")
        owner.post("/transfer", data={"new_member": new_id})

        new_owner = TestClient(create_demo_app(store))
        new_owner.cookies.set("account_id", account_id)
        new_owner.cookies.set("member_id", new_id)
        old_response = owner.get("/api/owner-only")
        new_response = new_owner.get("/api/owner-only")

        await memory.append(MemoryRecord(id="old-transfer", run_id=run_id, persona_id=old_id, type=MemoryType.ENTITY, text="Ownership was transferred to new-owner. I should no longer have owner-only access.", trust=Trust.VERIFIED, valid_from=now))
        await memory.append(MemoryRecord(id="new-transfer", run_id=run_id, persona_id=new_id, type=MemoryType.ENTITY, text="Ownership was transferred to me. I should have owner-only access.", trust=Trust.VERIFIED, valid_from=now))
        observation = Observation(id="owner-only", run_id=run_id, session_id="return", url="http://demo/api/owner-only", title="Owner access", visible_text="Check protected owner-only access after transfer.", captured_at=now)
        old_persona = PersonaRecord(id=old_id, run_id=run_id, kind="administrator", goal="Check my role after ownership transfer.", application_account_id=account_id)
        new_persona = PersonaRecord(id=new_id, run_id=run_id, kind="administrator", goal="Check my role after ownership transfer.", application_account_id=account_id)
        old_bundle = await MemoryContextAssembler(memory).build(old_persona, SessionRecord(id="old-return", run_id=run_id, persona_id=old_id, phase="return", due_business_time=now), observation, BudgetConfig(context_tokens=1200, output_tokens=128, safety_tokens=128))
        new_bundle = await MemoryContextAssembler(memory).build(new_persona, SessionRecord(id="new-return", run_id=run_id, persona_id=new_id, phase="return", due_business_time=now), observation, BudgetConfig(context_tokens=1200, output_tokens=128, safety_tokens=128))
        verification = await DemoVerifier().check("ownership_transfer", DemoVerificationContext(store, account_id))
        return {
            "fault": fault,
            "old_owner_status": old_response.status_code,
            "new_owner_status": new_response.status_code,
            "old_owner_role": store.role(account_id, old_id),
            "new_owner_role": store.role(account_id, new_id),
            "old_memory_ids": old_bundle.included_memory_ids,
            "new_memory_ids": new_bundle.included_memory_ids,
            "verifier_verdict": verification.verdict,
            "old_owner_leak": old_response.status_code == 200,
            "new_owner_has_access": new_response.status_code == 200,
        }
    finally:
        store.close()
