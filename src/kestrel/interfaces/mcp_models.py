"""Typed results of the MCP tools, and their conversion from the service contracts.

Every tool returns one of the `*Output` models below as structured content (with the
same JSON as text, for clients that read only text). A failed call returns the same
model with `error` set and the MCP `isError` flag raised; `error` carries a category
and a message that is safe to show to a user, never a traceback or an internal path.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kestrel.contracts import AskResult, BotSummary, SearchResult, SectionView
from kestrel.errors import ErrorCategory, KestrelError

ErrorKind = Literal[
    "configuration",
    "access_denied",
    "privacy_blocked",
    "egress_denied",
    "provider_unavailable",
    "budget_exceeded",
    "not_ingested",
    "invalid_input",
    "internal",
    "busy",
    "rate_limited",
]
"""The service's error categories plus the two this server adds for admission control."""

_GENERIC_MESSAGES: dict[str, str] = {
    "configuration": "The assistant is not configured correctly. Ask the operator to run `kestrel doctor`.",
    "access_denied": "You do not have access to this assistant or operation.",
    "privacy_blocked": "The question was blocked because it contains personal information. Remove it and ask again.",
    "egress_denied": "This assistant may not send the question to the configured destination.",
    "provider_unavailable": "The model provider could not be reached. Try again later.",
    "budget_exceeded": "The question could not be answered within the per-question budget. Try a narrower question.",
    "not_ingested": "No approved documents are available for this assistant yet.",
    "invalid_input": "The request is not valid.",
    "internal": "The request could not be completed.",
    "busy": "The server is busy. Try again shortly.",
    "rate_limited": "Too many requests in the last minute. Try again shortly.",
}

_PASS_THROUGH = {
    ErrorCategory.ACCESS_DENIED,
    ErrorCategory.PRIVACY_BLOCKED,
    ErrorCategory.EGRESS_DENIED,
    ErrorCategory.BUDGET_EXCEEDED,
    ErrorCategory.NOT_INGESTED,
    ErrorCategory.INVALID_INPUT,
}
"""Categories whose `user_message` is shown; the others always get the generic message."""

_PATH_LIKE = re.compile(r"[A-Za-z]:[\\/]|\\\\|(^|\s)/[\w.-]+/|Traceback", re.MULTILINE)
_MAX_MESSAGE_CHARS = 300


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class SafeError(_Out):
    """Why a call failed, worded for the end user."""

    category: ErrorKind
    message: str


def generic_error(category: str) -> SafeError:
    """The fixed message for a category."""
    kind = category if category in _GENERIC_MESSAGES else "internal"
    return SafeError(category=kind, message=_GENERIC_MESSAGES[kind])  # type: ignore[arg-type]


def error_from_exception(exc: BaseException) -> SafeError:
    """Map any exception to a safe error. Only selected `KestrelError` messages pass through."""
    if not isinstance(exc, KestrelError):
        return generic_error("internal")
    category = exc.category.value
    message = " ".join(str(exc.user_message or "").split())
    if exc.category not in _PASS_THROUGH or not message or _PATH_LIKE.search(message):
        return generic_error(category)
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[: _MAX_MESSAGE_CHARS - 3] + "..."
    return SafeError(category=category, message=message)  # type: ignore[arg-type]


class BotInfo(_Out):
    """A bot the caller may use."""

    id: str
    name: str
    description: str = ""


class BotListOutput(_Out):
    """Result of `list_bots`."""

    bots: list[BotInfo] = Field(default_factory=list)
    error: SafeError | None = None


class CitationInfo(_Out):
    """One quoted source the answer relies on. Pass it on to the user unchanged."""

    filename: str
    locator: str
    quote: str
    edition: str | None = None
    status: str
    title: str = ""
    supports: str = ""
    section_id: str = ""


class CalculationInfo(_Out):
    """One arithmetic step, recomputed by the evidence checks."""

    expression: str
    result: str
    rule: str


class EvidenceIssue(_Out):
    """One problem the evidence checks found."""

    check: str
    severity: str
    detail: str


class AskOutput(_Out):
    """Result of `ask`."""

    outcome: str = Field(description="answered, partial, not_found, refused, withheld or error.")
    answer: str = ""
    reason: str | None = None
    clarification: str | None = None
    citations: list[CitationInfo] = Field(default_factory=list)
    calculations: list[CalculationInfo] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    evidence_checks_passed: bool | None = Field(default=None, description="None when no answer was checked.")
    evidence_issues: list[EvidenceIssue] = Field(default_factory=list)
    redactions: list[str] = Field(default_factory=list, description="Kinds of personal information removed.")
    bot_id: str = ""
    model: str = ""
    latency_ms: int = 0
    trace_id: str | None = None
    steps: int = Field(default=0, description="How many steps (privacy gate, scope, searches, tool calls, checks) produced the answer.")
    error: SafeError | None = None


class PassageInfo(_Out):
    """One ranked passage from `search`."""

    rank: int
    section_id: str
    filename: str
    locator: str
    status: str
    edition: str | None = None
    text: str


class SearchOutput(_Out):
    """Result of `search`."""

    passages: list[PassageInfo] = Field(default_factory=list)
    clarification: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: SafeError | None = None


class SectionOutput(_Out):
    """Result of `get_section`."""

    section_id: str = ""
    filename: str = ""
    title: str = ""
    locator: str = ""
    heading_path: list[str] = Field(default_factory=list)
    status: str = ""
    edition: str | None = None
    text: str = ""
    error: SafeError | None = None


def bots_output(bots: list[BotSummary]) -> BotListOutput:
    """Convert the service's bot summaries."""
    return BotListOutput(bots=[BotInfo(id=b.id, name=b.name, description=b.description) for b in bots])


def ask_output(result: AskResult) -> AskOutput:
    """Convert an `AskResult`. An `error` outcome gets a safe error from its category."""
    report = result.evidence_report
    error = None
    if result.outcome.value == "error":
        error = generic_error(result.error_category.value if result.error_category else "internal")
    return AskOutput(
        outcome=result.outcome.value,
        answer=result.answer,
        reason=result.reason,
        clarification=result.clarification,
        citations=[
            CitationInfo(
                filename=c.filename,
                locator=c.locator,
                quote=c.quote,
                edition=c.edition,
                status=c.status.value,
                title=c.title,
                supports=c.supports,
                section_id=c.section_id,
            )
            for c in result.citations
        ],
        calculations=[CalculationInfo(expression=c.expression, result=c.result, rule=c.rule) for c in result.calculations],
        caveats=list(result.caveats),
        evidence_checks_passed=report.passed if report is not None else None,
        evidence_issues=[
            EvidenceIssue(check=i.check, severity=i.severity, detail=i.detail) for i in (report.issues if report else ())
        ],
        redactions=list(result.redactions),
        bot_id=result.bot_id,
        model=result.model,
        latency_ms=result.latency_ms,
        trace_id=result.trace_id,
        steps=len(result.steps),
        error=error,
    )


def search_output(result: SearchResult) -> SearchOutput:
    """Convert a `SearchResult`."""
    return SearchOutput(
        passages=[
            PassageInfo(
                rank=h.rank,
                section_id=h.section_id,
                filename=h.filename,
                locator=h.locator,
                status=h.status.value,
                edition=h.edition,
                text=h.text,
            )
            for h in result.hits
        ],
        clarification=result.clarification,
        warnings=list(result.warnings),
    )


def section_output(view: SectionView) -> SectionOutput:
    """Convert a `SectionView`."""
    return SectionOutput(
        section_id=view.section_id,
        filename=view.filename,
        title=view.title,
        locator=view.locator,
        heading_path=list(view.heading_path),
        status=view.status.value,
        edition=view.edition,
        text=view.text,
    )
