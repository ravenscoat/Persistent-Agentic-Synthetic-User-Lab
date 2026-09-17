from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from synthetic_lab.contracts import Artifact, Event, Finding


@dataclass(frozen=True)
class FindingReport:
    finding: Finding
    events: tuple[Event, ...]
    artifacts: tuple[Artifact, ...]
    explanation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding": self.finding.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in self.events],
            "artifacts": [artifact.model_dump(mode="json") for artifact in self.artifacts],
            "explanation": self.explanation,
        }


class ReportBuilder:
    """Builds a report from persisted evidence; prose never changes the verdict."""

    def build(self, finding: Finding, events: Sequence[Event], artifacts: Sequence[Artifact], *, explanation: str | None = None) -> FindingReport:
        evidence = set(finding.evidence_ids)
        selected_artifacts = tuple(artifact for artifact in artifacts if artifact.id in evidence)
        selected_events = tuple(event for event in events if event.run_id == finding.run_id and (not finding.session_id or event.session_id == finding.session_id))
        return FindingReport(finding=finding, events=selected_events, artifacts=selected_artifacts, explanation=explanation)
