from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SessionStatus(StrEnum):
    READY = "READY"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DecisionKind(StrEnum):
    SUSPICION = "suspicion"
    ACTION = "action"
    MEMORY_QUERY = "memory_query"
    FINISH = "finish"
    BLOCKED = "blocked"


class ActionStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    UNCERTAIN = "uncertain"


class MemoryType(StrEnum):
    CONVERSATION = "conversation"
    SEMANTIC = "semantic"
    WORKFLOW = "workflow"
    TOOLBOX = "toolbox"
    ENTITY = "entity"
    SUMMARY = "summary"
    TOOL_LOG = "tool_log"


class Trust(StrEnum):
    OBSERVED = "observed"
    VERIFIED = "verified"
    CANDIDATE = "candidate"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class FindingStatus(StrEnum):
    SUSPICION = "suspicion"
    CONFIRMED = "confirmed"
    INCONCLUSIVE = "inconclusive"


class ReplayStatus(StrEnum):
    REPRODUCED = "reproduced"
    NOT_REPRODUCED = "not_reproduced"
    NOT_ATTEMPTED = "not_attempted"


class RunRecord(StrictModel):
    id: str
    scenario_id: str
    status: RunStatus = RunStatus.CREATED
    created_at: datetime
    business_time: datetime
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    model_metadata: dict[str, Any] = Field(default_factory=dict)


class PersonaRecord(StrictModel):
    id: str
    run_id: str
    kind: str
    goal: str
    application_account_id: str
    allowed_tool_names: list[str] = Field(default_factory=list)


class SessionRecord(StrictModel):
    id: str
    run_id: str
    persona_id: str
    status: SessionStatus = SessionStatus.READY
    phase: str
    due_business_time: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    step_count: int = Field(default=0, ge=0)


class Element(StrictModel):
    id: str
    role: str
    name: str
    allowed_actions: list[str] = Field(default_factory=list)
    input_type: str | None = None
    filled: bool | None = None
    required: bool = False
    enabled: bool = True


class Observation(StrictModel):
    id: str
    run_id: str
    session_id: str
    url: str
    title: str = ""
    visible_text: str = ""
    elements: list[Element] = Field(default_factory=list)
    captured_at: datetime
    artifact_ids: list[str] = Field(default_factory=list)


class Action(StrictModel):
    id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    observation_id: str | None = None


class AgentDecision(StrictModel):
    kind: DecisionKind
    action: Action | None = None
    query: str | None = None
    summary: str | None = None
    invariant_id: str | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "AgentDecision":
        if self.kind is DecisionKind.SUSPICION and (not self.invariant_id or not self.summary):
            raise ValueError("suspicion requires invariant_id and summary")
        if self.kind is not DecisionKind.SUSPICION and self.invariant_id is not None:
            raise ValueError("only suspicion decisions may contain invariant_id")
        if self.kind is DecisionKind.ACTION and self.action is None:
            raise ValueError("action decision requires action")
        if self.kind is not DecisionKind.ACTION and self.action is not None:
            raise ValueError("only action decisions may contain action")
        if self.kind is DecisionKind.MEMORY_QUERY and not self.query:
            raise ValueError("memory_query decision requires query")
        if self.kind is not DecisionKind.MEMORY_QUERY and self.query is not None:
            raise ValueError("only memory_query decisions may contain query")
        if self.kind in {DecisionKind.FINISH, DecisionKind.BLOCKED} and not self.summary:
            raise ValueError("finish and blocked decisions require summary")
        return self


class ToolResult(StrictModel):
    action_id: str
    status: ActionStatus
    data: Any = None
    error_code: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    observed_at: datetime


class MemoryRecord(StrictModel):
    id: str
    run_id: str
    persona_id: str | None = None
    type: MemoryType
    text: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    source_event_ids: list[str] = Field(default_factory=list)
    trust: Trust
    valid_from: datetime
    valid_to: datetime | None = None
    supersedes_id: str | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    embedding_model: str | None = None
    embedding_dimension: int | None = Field(default=None, gt=0)


class Expectation(StrictModel):
    id: str
    run_id: str
    persona_id: str
    invariant_id: str
    entity_ids: list[str] = Field(default_factory=list)
    expected_values: dict[str, Any] = Field(default_factory=dict)
    due_business_time: datetime
    source_spec_id: str
    status: Literal["pending", "satisfied", "violated", "inconclusive"] = "pending"


class Finding(StrictModel):
    id: str
    run_id: str
    session_id: str
    invariant_id: str
    status: FindingStatus
    expected: Any
    actual: Any
    evidence_ids: list[str] = Field(default_factory=list)
    verifier_version: str
    replay_status: ReplayStatus = ReplayStatus.NOT_ATTEMPTED


class Event(StrictModel):
    id: str
    run_id: str
    persona_id: str | None = None
    session_id: str | None = None
    sequence: int = Field(ge=0)
    kind: str
    wall_time: datetime
    business_time: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    artifact_ids: list[str] = Field(default_factory=list)


class Artifact(StrictModel):
    id: str
    run_id: str
    relative_path: str
    media_type: str
    sha256: str
    byte_count: int = Field(ge=0)
    created_at: datetime


class ModelUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated: bool = False


class ModelResponse(StrictModel):
    decision: AgentDecision
    usage: ModelUsage = Field(default_factory=ModelUsage)
    latency_ms: float = Field(ge=0)
    model_id: str


class BudgetConfig(StrictModel):
    max_steps: int = Field(default=30, gt=0)
    max_model_requests: int = Field(default=40, gt=0)
    max_retries: int = Field(default=1, ge=0)
    context_tokens: int = Field(default=8192, gt=0)
    output_tokens: int = Field(default=1024, gt=0)
    safety_tokens: int = Field(default=512, ge=0)

    @property
    def input_tokens(self) -> int:
        available = self.context_tokens - self.output_tokens - self.safety_tokens
        if available <= 0:
            raise ValueError("context_tokens must exceed output_tokens + safety_tokens")
        return available


class ContextBundle(StrictModel):
    messages: list[dict[str, Any]]
    included_memory_ids: list[str] = Field(default_factory=list)
    omitted_counts: dict[str, int] = Field(default_factory=dict)
    estimated_tokens: int = Field(ge=0)
    accounting_method: Literal["backend", "conservative_estimate"]


class VerificationResult(StrictModel):
    verdict: Literal["satisfied", "confirmed", "inconclusive"]
    expected: Any
    actual: Any
    evidence_ids: list[str] = Field(default_factory=list)


class ReplayResult(StrictModel):
    status: ReplayStatus
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
