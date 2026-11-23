"""The answering coroutine: agent, evidence checks, one repair turn, and the bot's failure policy.

Unsupported caveats are removed (and reported as warnings) before each round of checks,
so optional commentary never costs the repair turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from kestrel.agent.builder import ANSWER_TOOL, READ_FILE, build_agent
from kestrel.agent.context import Budget, RequestContext
from kestrel.agent.mcp_tools import load_mcp_tools
from kestrel.agent.runner import TurnResult, run_turn
from kestrel.agent.tools import build_builtin_tools
from kestrel.contracts import REPAIRED_PREFIX, AgentAnswer, BotConfig, CheckIssue, McpServerSpec, ScopeDecision, Screened
from kestrel.errors import BudgetExceededError, ProviderUnavailableError
from kestrel.service.runtime import Runtime
from kestrel.verify import WITHDRAWN_REASON, check_sources_still_current, clean_label, repair_message, run_checks
from kestrel.verify.caveats import prune_caveats, pruned_summary


@dataclass
class Answered:
    """What one question produced before it is turned into an `AskResult`."""

    context: RequestContext
    answer: AgentAnswer | None = None
    issues: list[CheckIssue] = field(default_factory=list)
    repaired: bool = False
    warnings: list[str] = field(default_factory=list)
    withheld_reason: str | None = None
    """Set when a specific final check, rather than the evidence checks in general, stopped the answer."""

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


def _prune(question: Screened, answer: AgentAnswer, ctx: RequestContext, scope: ScopeDecision, removed: list[CheckIssue]) -> AgentAnswer:
    """Drop unsupported caveats before the checks, recording each removal as a step and a warning."""
    pruned, issues = prune_caveats(question.text, answer, dict(ctx.evidence), scope, tuple(ctx.calculations), dict(ctx.tool_results))
    if issues:
        ctx.record_step("checks", "Removed unsupported caveats", pruned_summary(issues))
        removed += issues
    return pruned


def _check(question: Screened, answer: AgentAnswer, ctx: RequestContext, scope: ScopeDecision, bot: BotConfig) -> list[CheckIssue]:
    return run_checks(question.text, answer, dict(ctx.evidence), scope, tuple(ctx.calculations), dict(ctx.tool_results),
                      has_documents=bool(bot.knowledge.collections))


_CITATION_CHECKS = frozenset({"label_retrieved", "quote_verbatim", "table_row_cited"})


def _drop_bad_citations(question: Screened, answer: AgentAnswer, issues: list[CheckIssue], ctx: RequestContext,
                        scope: ScopeDecision, bot: BotConfig, removed: list[CheckIssue]) -> tuple[AgentAnswer, list[CheckIssue]]:
    """Drop citations that fail on their own, when the answer still passes every check without them.

    A citation whose label was never shown, whose quote is not in the source, or that names a
    whole table is never shown to the user. If the remaining citations still support every
    number and claim check, the answer stands on them and each removal is reported as a
    warning. If they do not, nothing is changed and the answer goes to repair as before.
    """
    errors = [i for i in issues if i.severity == "error"]
    if not errors or any(i.check not in _CITATION_CHECKS or not i.label for i in errors):
        return answer, issues
    bad = {i.label for i in errors}
    kept = [c for c in answer.citations if clean_label(c.label) not in bad]
    if not kept:
        return answer, issues
    trimmed = answer.model_copy(update={"citations": kept})
    rechecked = _check(question, trimmed, ctx, scope, bot)
    if any(i.severity == "error" for i in rechecked):
        return answer, issues
    for issue in errors:
        removed.append(issue.model_copy(update={
            "severity": "warning",
            "detail": f"Citation removed: {issue.detail}",
        }))
    count = len(answer.citations) - len(kept)
    ctx.record_step("checks", "Removed unsupported citations",
                    f"{count} citation{'s' if count != 1 else ''} removed; the answer passes on the rest")
    return trimmed, rechecked


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
        loaded = await load_mcp_tools(
            specs, runtime.screen, bot.privacy, reserved=[t.name for t in tools] + [ANSWER_TOOL, READ_FILE],
            on_call=lambda label, detail: ctx.record_step("mcp_tool", label, detail),
            register=ctx.register_tool_result,
        )
        tools += loaded.tools
        outcome.warnings += loaded.warnings
    agent = build_agent(bot, chat_model, tools, ctx.budget)
    turn = await run_turn(agent, [HumanMessage(question.text)], ctx.budget)
    if turn.answer is None:
        raise ProviderUnavailableError(
            "The model stopped without giving a final answer. Try again, or choose another model with --model.",
            detail="no structured response after one nudge",
        )
    removed: list[CheckIssue] = []
    outcome.answer = _prune(question, turn.answer, ctx, scope, removed)
    outcome.issues = _check(question, outcome.answer, ctx, scope, bot)
    outcome.answer, outcome.issues = _drop_bad_citations(question, outcome.answer, outcome.issues, ctx, scope, bot, removed)
    _record_checks(ctx, outcome.issues)
    earlier: list[CheckIssue] = []
    while not outcome.passed and ctx.budget.repairs < ctx.budget.max_repairs:
        failing = sorted({i.check for i in outcome.issues if i.severity == "error"})
        ctx.record_step("repair", "Asked the model to repair the answer", "failed: " + ", ".join(failing))
        repaired = await _repair(agent, turn, outcome.issues, ctx.budget)
        if repaired is None or repaired.answer is None:
            ctx.record_step("repair", "Repair not completed", "the budget ran out or no new answer came back")
            break
        earlier += [i for i in outcome.issues if i.severity == "error"]
        turn = repaired
        outcome.answer = _prune(question, repaired.answer, ctx, scope, removed)
        outcome.issues = _check(question, outcome.answer, ctx, scope, bot)
        outcome.answer, outcome.issues = _drop_bad_citations(question, outcome.answer, outcome.issues, ctx, scope, bot, removed)
        outcome.repaired = True
        _record_checks(ctx, outcome.issues)
    # Issues the repair turn fixed are kept as warnings, so the audit shows why a repair happened.
    outcome.issues += [
        i.model_copy(update={"severity": "warning", "detail": f"{REPAIRED_PREFIX}{i.detail}"}) for i in earlier
    ]
    outcome.issues += removed
    _recheck_sources(runtime, outcome)
    return outcome


def _recheck_sources(runtime: Runtime, outcome: Answered) -> None:
    """Re-read every cited document's status just before returning: a block or supersession during the run counts."""
    if outcome.answer is None:
        return
    ctx = outcome.context
    cited = {
        evidence.hit.document_id
        for evidence in (ctx.evidence.get(clean_label(c.label)) for c in outcome.answer.citations)
        if evidence is not None
    }
    if not cited:
        return
    documents = runtime.store.documents
    current = {doc_id: record.status if (record := documents.get_document(doc_id)) else None for doc_id in sorted(cited)}
    issues = check_sources_still_current(outcome.answer, dict(ctx.evidence), current, documents.eligible_document_ids(ctx.scope))
    if issues:
        outcome.issues += issues
        outcome.withheld_reason = WITHDRAWN_REASON
        ctx.record_step("checks", "A cited source was withdrawn", ", ".join(sorted({i.label or "" for i in issues})))


def _record_checks(ctx: RequestContext, issues: list[CheckIssue]) -> None:
    failing = sorted({i.check for i in issues if i.severity == "error"})
    if failing:
        ctx.record_step("checks", "Evidence checks failed", ", ".join(failing))
    else:
        ctx.record_step("checks", "Evidence checks passed", "no check failed")


async def _repair(agent: object, turn: TurnResult, issues: list[CheckIssue], budget: Budget) -> TurnResult | None:
    """One repair turn in the same conversation. An exhausted budget ends repair; the last answer stands."""
    if budget.model_calls_left <= 0 or budget.remaining_s <= 0:
        return None
    budget.repairs += 1
    try:
        return await run_turn(agent, [*turn.messages, HumanMessage(repair_message(issues))], budget)
    except BudgetExceededError:
        return None
