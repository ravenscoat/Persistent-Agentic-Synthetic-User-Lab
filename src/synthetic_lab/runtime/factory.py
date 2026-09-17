from __future__ import annotations

from typing import Any

from synthetic_lab.config import Settings
from synthetic_lab.llm import build_local_model

from .agent import PersonaAgent


def build_local_persona_agent(
    settings: Settings,
    *,
    context: Any,
    tools: Any,
    state: Any,
    memory: Any,
    budgets: Any,
    clock: Any | None = None,
) -> PersonaAgent:
    """Construct the normal local agent while retaining dependency injection."""
    return PersonaAgent(
        model=build_local_model(settings),
        context=context,
        tools=tools,
        state=state,
        memory=memory,
        budgets=budgets,
        clock=clock,
    )
