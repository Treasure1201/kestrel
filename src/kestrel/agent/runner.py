"""Run the agent for one turn of a question, with one nudge when it stops without an answer."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import ValidationError

from kestrel.agent.context import Budget
from kestrel.agent.prompt import NUDGE
from kestrel.contracts import AgentAnswer
from kestrel.errors import BudgetExceededError

_STEPS_PER_MODEL_CALL = 8
"""Graph super-steps allowed per model call (model, tools and middleware nodes)."""


@dataclass(frozen=True)
class TurnResult:
    """The conversation after a turn, and the structured answer if the model gave one."""

    answer: AgentAnswer | None
    messages: list[BaseMessage]
    nudged: bool = False


async def _invoke(agent: Any, messages: Sequence[BaseMessage], budget: Budget) -> dict[str, Any]:
    if budget.remaining_s <= 0:
        raise BudgetExceededError(f"The question took longer than {budget.timeout_s:g} seconds.")
    config = {"recursion_limit": _STEPS_PER_MODEL_CALL * budget.max_model_calls + 20}
    try:
        state = await asyncio.wait_for(agent.ainvoke({"messages": list(messages)}, config=config), timeout=budget.remaining_s)
    except TimeoutError:
        raise BudgetExceededError(
            f"The question took longer than {budget.timeout_s:g} seconds. Try again or ask a more specific question."
        ) from None
    except Exception as exc:
        if type(exc).__name__ == "GraphRecursionError":
            raise BudgetExceededError("The assistant did not reach an answer within its step limit.") from None
        raise
    return state


def answer_from_text(text: str) -> AgentAnswer | None:
    """An answer some models write as JSON text instead of calling the answer tool.

    It is accepted only if it validates against the answer schema, and it then goes
    through the same evidence checks as any other answer, so nothing is trusted more.
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or "status" not in data:
        return None
    for citation in data.get("citations") or []:
        if isinstance(citation, dict):
            citation.setdefault("supports", "")
    try:
        return AgentAnswer.model_validate(data)
    except ValidationError:
        return None


def _answer(state: dict[str, Any]) -> AgentAnswer | None:
    value = state.get("structured_response")
    if isinstance(value, AgentAnswer):
        return value
    if value is not None:
        return AgentAnswer.model_validate(value)
    last = next((m for m in reversed(state.get("messages", [])) if isinstance(m, AIMessage)), None)
    if last is None or last.tool_calls or not isinstance(last.content, str):
        return None
    return answer_from_text(last.content)


async def run_turn(agent: Any, messages: Sequence[BaseMessage], budget: Budget) -> TurnResult:
    """Invoke the agent on `messages`. If it ends without the answer tool, nudge once in the same conversation."""
    state = await _invoke(agent, messages, budget)
    history = list(state.get("messages", messages))
    answer = _answer(state)
    if answer is not None or budget.nudges >= 1 or budget.model_calls_left <= 0:
        return TurnResult(answer=answer, messages=history)
    budget.nudges += 1
    state = await _invoke(agent, [*history, HumanMessage(NUDGE)], budget)
    return TurnResult(answer=_answer(state), messages=list(state.get("messages", history)), nudged=True)
