from datetime import datetime, timezone

import pytest

from synthetic_lab.contracts import Finding, FindingStatus
from synthetic_lab.verification import DemoReplayService


@pytest.mark.asyncio
async def test_replay_reproduces_seeded_trial_fault() -> None:
    finding = Finding(id="f1", run_id="r1", session_id="s1", invariant_id="trial_access_seven_days", status=FindingStatus.CONFIRMED, expected=True, actual=False, verifier_version="demo")
    result = await DemoReplayService().replay(finding, fault="trial_expires_day_5")
    assert result.status.value == "reproduced"


@pytest.mark.asyncio
async def test_replay_does_not_confirm_healthy_state() -> None:
    finding = Finding(id="f1", run_id="r1", session_id="s1", invariant_id="purchase_idempotency", status=FindingStatus.CONFIRMED, expected=1, actual=2, verifier_version="demo")
    result = await DemoReplayService().replay(finding, fault=None)
    assert result.status.value == "not_reproduced"
