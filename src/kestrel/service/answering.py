"""The answering coroutine: agent, evidence checks, one repair turn, and the bot's failure policy."""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from kestrel.agent.builder import ANSWER_TOOL, READ_FILE, build_agent
from kestrel.agent.context import Budget, RequestContext
from kestrel.agent.mcp_tools import load_mcp_tools
from kestrel.agent.runner import TurnResult, run_turn
from kestrel.agent.tools import build_builtin_tools
from kestrel.contracts import AgentAnswer, BotConfig, CheckIssue, McpServerSpec, ScopeDecision, Screened
from kestrel.errors import BudgetExceededError, ProviderUnavailableError
from kestrel.service.runtime import Runtime
from kestrel.verify import repair_message, run_checks


@dataclass
class Answered:
    """What one question produced before it is turned into an `AskResult`."""

    context: RequestContext
    answer: AgentAnswer | None = None
    issues: list[CheckIssue] = field(default_factory=list)
    repaired: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


def new_budget(bot: BotConfig, runtime: Runtime) -> Budget:
    """The one total budget for a question, from the bot's policy and settings."""
    return Budget(
        max_model_calls=bot.model.max_model_calls,
        max_searches=bot.model.max_searches,
        max_repairs=bot.verification.max_repairs,
        timeout_s=bot.model.timeout_s or runtime.settings.timeout_s,
    )


def _check(question: Screened, answer: AgentAnswer, ctx: RequestContext, scope: ScopeDecision) -> list[CheckIssue]:
    return run_checks(question.text, answer, dict(ctx.evidence), scope, tuple(ctx.calculations))


async def answer_question(
    runtime: Runtime,
    bot: BotConfig,
    question: Screened,
    scope: ScopeDecision,
    chat_model: BaseChatModel,
    specs: tuple[McpServerSpec, ...],
    outcome: Answered,
) -> Answered:
    """Run the agent, check the answer, and repair once if the checks fail.

    `outcome` is filled in place, so the caller keeps the evidence and token counts
    even when this raises (for example on a provider error or an exhausted budget).
    """
    ctx = outcome.context
    tools = build_builtin_tools(bot.tools, ctx, runtime.retriever)
    if specs:
        loaded = await load_mcp_tools(specs, runtime.screen, bot.privacy, reserved=[t.name for t in tools] + [ANSWER_TOOL, READ_FILE])
        tools += loaded.tools
        outcome.warnings += loaded.warnings
    agent = build_agent(bot, chat_model, tools, ctx.budget)
    turn = await run_turn(agent, [HumanMessage(question.text)], ctx.budget)
    if turn.answer is None:
        raise ProviderUnavailableError(
            "The model stopped without giving a final answer. Try again, or choose another model with --model.",
            detail="no structured response after one nudge",
        )
    outcome.answer = turn.answer
    outcome.issues = _check(question, turn.answer, ctx, scope)
    while not outcome.passed and ctx.budget.repairs < ctx.budget.max_repairs:
        repaired = await _repair(agent, turn, outcome.issues, ctx.budget)
        if repaired is None or repaired.answer is None:
            break
        turn = repaired
        outcome.answer = repaired.answer
        outcome.issues = _check(question, repaired.answer, ctx, scope)
        outcome.repaired = True
    return outcome


async def _repair(agent: object, turn: TurnResult, issues: list[CheckIssue], budget: Budget) -> TurnResult | None:
    """One repair turn in the same conversation. An exhausted budget ends repair; the last answer stands."""
    if budget.model_calls_left <= 0 or budget.remaining_s <= 0:
        return None
    budget.repairs += 1
    try:
        return await run_turn(agent, [*turn.messages, HumanMessage(repair_message(issues))], budget)
    except BudgetExceededError:
        return None
