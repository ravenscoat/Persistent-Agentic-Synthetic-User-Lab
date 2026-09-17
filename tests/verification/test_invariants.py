import pytest

from synthetic_lab.demo.store import DemoStore
from synthetic_lab.verification import DemoVerificationContext, DemoVerifier


def make_store(fault: str | None = None) -> DemoStore:
    store = DemoStore(fault=fault)
    store.create_account("a1", "demo@example.test", "pass")
    return store


@pytest.mark.asyncio
async def test_verifier_confirms_trial_fault() -> None:
    store = make_store("trial_expires_day_5")
    store.advance_days(6)
    result = await DemoVerifier().check("trial_access_seven_days", DemoVerificationContext(store, "a1"))
    assert result.verdict == "confirmed"
    store.close()


@pytest.mark.asyncio
async def test_verifier_confirms_duplicate_charge() -> None:
    store = make_store("duplicate_charge")
    store.purchase("a1", "op-1", 500)
    result = await DemoVerifier().check("purchase_idempotency", DemoVerificationContext(store, "a1", "op-1"))
    assert result.verdict == "confirmed"
    store.close()


@pytest.mark.asyncio
async def test_verifier_marks_missing_operation_inconclusive() -> None:
    store = make_store()
    result = await DemoVerifier().check("purchase_idempotency", DemoVerificationContext(store, "a1"))
    assert result.verdict == "inconclusive"
    store.close()
