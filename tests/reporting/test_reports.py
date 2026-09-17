from datetime import datetime, timezone

from synthetic_lab.contracts import Artifact, Event, Finding, FindingStatus, ReplayStatus
from synthetic_lab.reporting import ReportBuilder


def test_report_keeps_verdict_and_filters_evidence() -> None:
    now = datetime.now(timezone.utc)
    finding = Finding(id="f1", run_id="r1", session_id="s1", invariant_id="purchase_idempotency", status=FindingStatus.CONFIRMED, expected=1, actual=2, evidence_ids=["a1"], verifier_version="v1", replay_status=ReplayStatus.NOT_ATTEMPTED)
    event = Event(id="e1", run_id="r1", session_id="s1", sequence=0, kind="tool_result", wall_time=now, business_time=now)
    wanted = Artifact(id="a1", run_id="r1", relative_path="r1/a.png", media_type="image/png", sha256="abc", byte_count=1, created_at=now)
    other = Artifact(id="a2", run_id="r1", relative_path="r1/b.png", media_type="image/png", sha256="def", byte_count=1, created_at=now)
    report = ReportBuilder().build(finding, [event], [wanted, other], explanation="model text")
    data = report.as_dict()
    assert data["finding"]["status"] == "confirmed"
    assert [item["id"] for item in data["artifacts"]] == ["a1"]
    assert data["explanation"] == "model text"
