from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from synthetic_lab.contracts import (
    Action,
    AgentDecision,
    BudgetConfig,
    DecisionKind,
    MemoryRecord,
    MemoryType,
    Trust,
)


def test_action_decision_requires_action() -> None:
    with pytest.raises(ValidationError):
        AgentDecision(kind=DecisionKind.ACTION)


def test_finish_requires_summary() -> None:
    with pytest.raises(ValidationError):
        AgentDecision(kind=DecisionKind.FINISH)


def test_decision_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AgentDecision(kind=DecisionKind.FINISH, summary="done", surprise=True)


def test_memory_can_be_serialized_with_scope() -> None:
    record = MemoryRecord(
        id="m1",
        run_id="r1",
        persona_id="p1",
        type=MemoryType.ENTITY,
        text="Plan is free",
        trust=Trust.OBSERVED,
        valid_from=datetime.now(timezone.utc),
    )
    assert record.model_dump(mode="json")["persona_id"] == "p1"


def test_budget_reserves_input_space() -> None:
    assert BudgetConfig(context_tokens=8192, output_tokens=1024, safety_tokens=512).input_tokens == 6656


def test_budget_rejects_no_input_space() -> None:
    with pytest.raises(ValueError):
        BudgetConfig(context_tokens=100, output_tokens=80, safety_tokens=30).input_tokens
