import asyncio

from synthetic_lab.evaluation import run_ownership_transfer


def test_two_personas_keep_memory_scoped_and_enforce_transferred_roles():
    result = asyncio.run(run_ownership_transfer())
    assert result["old_owner_status"] == 403
    assert result["new_owner_status"] == 200
    assert result["old_memory_ids"] == ["old-transfer"]
    assert result["new_memory_ids"] == ["new-transfer"]
    assert result["verifier_verdict"] == "satisfied"


def test_two_personas_expose_seeded_owner_permission_leak():
    result = asyncio.run(run_ownership_transfer(fault="owner_transfer_leak"))
    assert result["old_owner_status"] == 200
    assert result["new_owner_status"] == 200
    assert result["old_owner_leak"] is True
    assert result["verifier_verdict"] == "confirmed"
