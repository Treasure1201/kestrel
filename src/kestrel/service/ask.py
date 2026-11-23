"""`AssistantService.ask`: validate, access, privacy, egress, scope, agent, checks, usage, audit.

`ask` never raises. Every outcome, including refusals and errors, is returned as an
`AskResult` and appended to the audit log. Only screened text is ever stored or
returned: before screening succeeds, the question is recorded as empty.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field

from kestrel.agent.context import RequestContext
from kestrel.agent.middleware import is_transient
from kestrel.contracts import (
    AnswerStep,
    AuditCitation,
    AuditEntry,
    AskResult,
    BotConfig,
    DocumentStatus,
    EvidenceReport,
    Outcome,
    Principal,
    PrivacyPolicy,
    ResolvedCitation,
    ScopeDecision,
    TableRowView,
    Screened,
    SearchScope,
    Usage,
)
from kestrel.contracts.service import MAX_QUESTION_CHARS
from kestrel.errors import (
    ErrorCategory,
    InvalidInputError,
    KestrelError,
    NotIngestedError,
    PrivacyBlockedError,
)
from kestrel.llm import check_egress, cost_usd, explain_provider_error, load_prices, parse_model_ref
from kestrel.redaction import describe_exception, redact_secrets
from kestrel.service.answering import Answered, answer_question, new_budget
from kestrel.service.runtime import Runtime
from kestrel.verify import clean_label, evidence_report

log = logging.getLogger(__name__)

WITHHELD_MESSAGE = (
    "This answer could not be confirmed against the sources, so it is not shown. "
    "Please read the retrieved sections below, or ask the question in another way."
)
_REFUSED_CATEGORIES = {ErrorCategory.ACCESS_DENIED, ErrorCategory.PRIVACY_BLOCKED, ErrorCategory.EGRESS_DENIED}


@dataclass
class _Request:
    trace_id: str
    bot_id: str
    principal: Principal
    started: float = field(default_factory=time.perf_counter)
    bot: BotConfig | None = None
    screened: Screened | None = None
    model: str = ""
    scope: ScopeDecision | None = None
    answered: Answered | None = None
    question_derived_reason: bool = False
    """True when the result's reason was written by the model or quotes the question."""

    @property
    def latency_ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


def screen_question(runtime: Runtime, text: str, policy: PrivacyPolicy) -> Screened:
    """Screen text; any failure fails closed as a privacy block."""
    try:
        return runtime.screen.screen(text, policy)
    except KestrelError:
        raise
    except Exception as exc:
        raise PrivacyBlockedError("The question could not be screened for personal information, so it was not sent.", detail=type(exc).__name__) from None


def _row_locator(locator: str, row: TableRowView | None) -> str:
    """Name the row in a table citation, so a reader can find the exact line relied on."""
    if row is None or f"row {row.row_number}" in locator:
        return locator
    cells = f" (cells {row.address})" if row.address else ""
    return f"{locator}, row {row.row_number}{cells}"


def _resolve(answered: Answered) -> tuple[ResolvedCitation, ...]:
    resolved = []
    for citation in answered.answer.citations if answered.answer else ():
        label = clean_label(citation.label)
        evidence = answered.context.evidence.get(label)
        tool = answered.context.tool_results.get(label)
        if evidence is None and tool is not None:
            resolved.append(ResolvedCitation(
                label=label, quote=citation.quote, supports=citation.supports, filename="Tool result",
                title=f"{tool.server} / {tool.tool}", locator=tool.arguments or "(no arguments)",
                document_id="", section_id="",
                status=DocumentStatus.CURRENT, source="tool", tool_server=tool.server, tool_name=tool.tool,
                tool_arguments=tool.arguments,
            ))
            continue
        if evidence is None:
            continue
        hit = evidence.hit
        resolved.append(ResolvedCitation(
            label=label, quote=citation.quote, supports=citation.supports, filename=hit.filename, title=hit.title,
            locator=_row_locator(hit.locator, evidence.row), document_id=hit.document_id, section_id=hit.section_id, edition=hit.edition,
            status=hit.status, row_number=evidence.row.row_number if evidence.row else None,
        ))
    return tuple(resolved)


def _usage(runtime: Runtime, req: _Request) -> Usage:
    if req.answered is None:
        return Usage()
    budget = req.answered.context.budget
    usage = Usage(model_calls=budget.model_calls, searches=budget.searches, repairs=budget.repairs,
                  input_tokens=budget.input_tokens, output_tokens=budget.output_tokens)
    if not req.model:
        return usage
    try:
        ref = parse_model_ref(req.model)
        cost = cost_usd(ref.provider, ref.model, usage, prices=load_prices(runtime.settings.config_dir))
    except KestrelError:
        cost = None
    return usage.model_copy(update={"cost_usd": cost})


def _success(runtime: Runtime, req: _Request) -> AskResult:
    answered = req.answered
    assert answered is not None and answered.answer is not None and req.bot is not None and req.screened is not None
    answer = answered.answer
    report: EvidenceReport = evidence_report(answered.issues, repaired=answered.repaired)
    common = dict(
        trace_id=req.trace_id, bot_id=req.bot.id, question=req.screened.text, evidence_report=report,
        redactions=req.screened.matched, scope=req.scope.scope if req.scope else None, model=req.model,
        usage=_usage(runtime, req), latency_ms=req.latency_ms, retrieved=answered.context.retrieved(),
        tool_results=answered.context.tools_shown(), steps=_steps(req),
    )
    if not report.passed and req.bot.verification.on_failure == "withhold":
        return AskResult(outcome=Outcome.WITHHELD, answer=WITHHELD_MESSAGE,
                         reason="The evidence checks did not pass after one repair attempt.", **common)
    req.question_derived_reason = True
    text = answer.answer.strip() or (answer.reason or "").strip()
    return AskResult(
        outcome=Outcome(answer.status), answer=text, reason=answer.reason, citations=_resolve(answered),
        calculations=tuple(answer.calculations), caveats=tuple(answer.caveats) + tuple(answered.warnings), **common,
    )


def _failure(runtime: Runtime, req: _Request, error: KestrelError) -> AskResult:
    outcome = Outcome.REFUSED if error.category in _REFUSED_CATEGORIES else Outcome.ERROR
    return AskResult(
        trace_id=req.trace_id, bot_id=req.bot_id, question=req.screened.text if req.screened else "",
        outcome=outcome, reason=redact_secrets(error.user_message, runtime.settings), redactions=req.screened.matched if req.screened else (),
        scope=req.scope.scope if req.scope else None, model=req.model, usage=_usage(runtime, req),
        latency_ms=req.latency_ms, error_category=error.category,
        retrieved=req.answered.context.retrieved() if req.answered else (),
        tool_results=req.answered.context.tools_shown() if req.answered else (), steps=_steps(req),
    )


def _clarification(req: _Request) -> AskResult:
    assert req.bot is not None and req.screened is not None and req.scope is not None
    req.question_derived_reason = True  # a clarification quotes the dates or editions the question named
    return AskResult(
        trace_id=req.trace_id, bot_id=req.bot.id, question=req.screened.text, outcome=Outcome.NOT_FOUND,
        answer=req.scope.clarification or "", reason=req.scope.clarification, clarification=req.scope.clarification,
        redactions=req.screened.matched, model=req.model, latency_ms=req.latency_ms, steps=_steps(req),
    )


def _scope_detail(scope: SearchScope) -> str:
    if scope.edition:
        return f"edition {scope.edition}, named in the question"
    if scope.effective_on:
        return f"the editions in force on {scope.effective_on.isoformat()}"
    return ", ".join(sorted(s.value for s in scope.statuses)) + " documents only"


def _steps(req: _Request) -> tuple[AnswerStep, ...]:
    """Privacy gate and scope, then every tool call, check and repair the request made, in order."""
    steps: list[AnswerStep] = []
    if req.screened is not None:
        found = req.screened.matched
        if found:
            kinds = ", ".join(sorted(set(found)))
            steps.append(AnswerStep(kind="privacy", label="Removed personal information", detail=f"{len(found)} item(s): {kinds}"))
        else:
            steps.append(AnswerStep(kind="privacy", label="Checked the question for personal information", detail="none found"))
    if req.scope is not None:
        if req.scope.scope is not None:
            steps.append(AnswerStep(kind="scope", label="Chose the documents to search", detail=_scope_detail(req.scope.scope)))
        else:
            steps.append(AnswerStep(kind="scope", label="Asked to clarify the edition", detail="nothing was searched"))
    if req.answered is not None:
        steps += list(req.answered.context.steps)
    return tuple(steps)


def _audit_reason(runtime: Runtime, req: _Request, result: AskResult, store_text: bool) -> str | None:
    """Why the question was not answered, as it may be stored.

    A reason written by the model, or a clarification (which quotes the question), is
    derived from the question: it is stored screened, and only when the question text
    itself would be stored. A reason written by the application is always stored.
    """
    if result.outcome in {Outcome.ANSWERED, Outcome.PARTIAL} or not result.reason:
        return None
    if not req.question_derived_reason:
        return result.reason
    if not store_text or req.bot is None:
        return None
    try:
        return screen_question(runtime, result.reason, req.bot.privacy).text
    except KestrelError:
        return None


def _closest(result: AskResult) -> str | None:
    """`filename > locator` of the top-ranked passage the model was shown."""
    if not result.retrieved:
        return None
    hit = result.retrieved[0].hit
    return hit.locator if hit.locator.startswith(hit.filename) else f"{hit.filename} > {hit.locator}"


def _audit(runtime: Runtime, req: _Request, result: AskResult) -> None:
    policy = req.bot.privacy if req.bot else None
    text = result.question
    store_text = bool(text) and policy is not None and policy.audit_question_text == "redacted"
    report = result.evidence_report
    failed = tuple(sorted({i.check for i in report.issues if i.severity == "error"})) if report else ()
    entry = AuditEntry(
        kind="ask", trace_id=req.trace_id, bot_id=result.bot_id, channel=req.principal.channel, user=req.principal.user,
        roles=tuple(sorted(req.principal.roles)), question_text=text if store_text else None,
        question_digest=hashlib.sha256(text.encode()).hexdigest() if text else None, redactions=result.redactions,
        retrieved_chunk_ids=tuple(dict.fromkeys(e.hit.chunk_id for e in result.retrieved)),
        citations=tuple(AuditCitation(label=c.label, document_id=c.document_id, section_id=c.section_id,
                                      locator=c.locator, quote=c.quote) for c in result.citations),
        calculations=tuple(f"{c.expression} = {c.result}" for c in result.calculations),
        outcome=result.outcome, error_category=result.error_category, model=result.model or None,
        input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
        cost_usd=result.usage.cost_usd, latency_ms=result.latency_ms,
        reason=_audit_reason(runtime, req, result, store_text), failed_checks=failed, closest=_closest(result),
    )
    try:
        runtime.store.audit.append(entry)
    except Exception as exc:  # the answer is still returned; the failure is logged without content
        log.error("audit append failed for trace %s: %s", req.trace_id, describe_exception(exc))


def _run(runtime: Runtime, req: _Request, question: str, model: str | None) -> AskResult:
    if not isinstance(question, str) or not question.strip():
        raise InvalidInputError("The question is empty.")
    if len(question) > MAX_QUESTION_CHARS:
        raise InvalidInputError(f"The question is longer than {MAX_QUESTION_CHARS} characters. Please shorten it.")
    bot = req.bot = runtime.registry.get(req.bot_id)
    # Screen first, so even a refusal is recorded with screened text only.
    req.screened = screen_question(runtime, question, bot.privacy)
    runtime.bot_for(req.bot_id, req.principal)
    if req.screened.found_personal_information and bot.privacy.on_personal_information == "block":
        raise PrivacyBlockedError(
            "The question contains personal information (" + ", ".join(req.screened.matched) + "), which this "
            "assistant does not accept. Ask again without names, ID numbers or contact details."
        )
    req.model = str(parse_model_ref(model or bot.model.default))
    specs = runtime.registry.mcp_specs(bot)
    check_egress(bot, req.model, specs, runtime.settings)
    req.scope = runtime.retriever.resolve_scope(req.screened.text, bot.knowledge)
    if req.scope.clarification is not None:
        return _clarification(req)
    if not runtime.store.documents.eligible_document_ids(SearchScope(collections=bot.knowledge.collections)):
        raise NotIngestedError(f"No documents are available for {bot.id!r} yet. Run: kestrel ingest --bot {bot.id}")
    assert req.scope.scope is not None
    budget = new_budget(bot, runtime)
    req.answered = Answered(context=RequestContext(scope=req.scope.scope, budget=budget))
    chat_model = runtime.chat_model(req.model)
    coroutine = answer_question(runtime, bot, req.screened, req.scope, chat_model, specs, req.answered)
    try:
        runtime.loop.run(coroutine, budget.timeout_s)
    except KestrelError:
        raise
    except Exception as exc:
        if _from_provider(exc):
            raise explain_provider_error(exc, req.model) from exc
        raise
    return _success(runtime, req)


_PROVIDER_MODULES = {"anthropic", "openai", "ollama", "httpx", "httpcore", "langchain_anthropic", "langchain_openai", "langchain_ollama"}


def _from_provider(exc: BaseException) -> bool:
    """True for errors raised by a model provider client or the network, as opposed to a bug."""
    if type(exc).__module__.split(".")[0] in _PROVIDER_MODULES or is_transient(exc):
        return True
    return isinstance(getattr(exc, "status_code", None), int)


def ask(runtime: Runtime, bot_id: str, question: str, principal: Principal, model: str | None = None) -> AskResult:
    """Answer one question; never raises. See the module docstring."""
    req = _Request(trace_id=uuid.uuid4().hex, bot_id=str(bot_id), principal=principal)
    try:
        result = _run(runtime, req, question, model)
    except KestrelError as exc:
        log.info("ask %s failed: %s (%s)", req.trace_id, exc.category.value, redact_secrets(exc.detail, runtime.settings))
        result = _failure(runtime, req, exc)
    except Exception as exc:  # fail closed: an audited error; never the exception text or a traceback
        log.error("ask %s failed unexpectedly: %s", req.trace_id, describe_exception(exc, "internal"))
        result = _failure(runtime, req, KestrelError("Something went wrong while answering. Please try again.", detail=type(exc).__name__))
    _audit(runtime, req, result)
    return result
