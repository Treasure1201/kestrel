"""Build the read-only answering agent for one bot and one request."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents import FilesystemMiddleware, create_deep_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from kestrel.agent.context import Budget
from kestrel.agent.middleware import BudgetMiddleware, MalformedReplyRetry, ToolChoiceFallback, retry_middleware
from kestrel.agent.prompt import system_prompt
from kestrel.agent.readonly import ToolAllowList, skills_backend
from kestrel.contracts import BUILTIN_TOOLS, AgentAnswer, BotConfig

ANSWER_TOOL = AgentAnswer.__name__
"""Name of the structured-answer tool the model calls to finish."""
READ_FILE = "read_file"


def build_agent(
    bot: BotConfig,
    chat_model: BaseChatModel,
    tools: Sequence[BaseTool],
    budget: Budget,
    *,
    max_retries: int = 2,
) -> Any:
    """A deep agent whose only abilities are `tools`, reading the bot's skills (if any), and answering.

    The final answer is returned through the `AgentAnswer` tool (tool-call structured
    output, which works alongside other tools on every supported provider); a
    malformed answer is sent back to the model with the validation error. Every model
    call is counted against `budget`, and transient provider errors are retried.
    """
    names = [tool.name for tool in tools]
    if len(set(names)) != len(names):
        raise ValueError(f"tool names must be unique: {names}")
    has_skills = bool(bot.skills)
    backend, skill_routes, permissions = skills_backend(bot.skills)
    allowed = names + ([READ_FILE] if has_skills else [])
    extra = [name for name in names if name not in BUILTIN_TOOLS]
    return create_deep_agent(
        model=chat_model,
        tools=list(tools),
        system_prompt=system_prompt(bot.prompt_text, extra_tools=extra, skills=has_skills, documents=bool(bot.knowledge.collections)),
        backend=backend,
        skills=skill_routes or None,
        permissions=permissions,
        middleware=[
            FilesystemMiddleware(backend=backend, tools=[READ_FILE], tool_token_limit_before_evict=None),
            ToolAllowList(allowed),
            MalformedReplyRetry(budget),
            BudgetMiddleware(budget),
            ToolChoiceFallback(),
            retry_middleware(max_retries),
        ],
        response_format=ToolStrategy(AgentAnswer),
        name=f"{bot.id}-answer",
    )
