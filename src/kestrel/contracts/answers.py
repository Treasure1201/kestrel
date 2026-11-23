"""The answer contract: what the model fills, what the checks report, what callers get."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kestrel.contracts._base import Frozen
from kestrel.contracts.documents import DocumentStatus
from kestrel.contracts.retrieval import SearchHit, SearchScope, TableRowView
from kestrel.errors import ErrorCategory

LABEL_PATTERN = r"^c[1-9][0-9]*(\.r[1-9][0-9]*)?$"
"""Excerpt labels: `c3` for a search excerpt, `c7.r4` for row 4 of the table excerpt c7 belongs to."""

TOOL_LABEL_PATTERN = r"^t[1-9][0-9]*$"
"""Tool-result labels: `t2` for the second result an approved MCP tool returned in this request."""


class AnswerStatus(StrEnum):
    """The status the model chooses."""

    ANSWERED = "answered"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    REFUSED = "refused"


class Outcome(StrEnum):
    """What the caller and the audit log see. The last two are set by the application only."""

    ANSWERED = "answered"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    REFUSED = "refused"
    WITHHELD = "withheld"
    """Evidence checks still failed after the repair turn and the bot's policy is `withhold`."""
    ERROR = "error"
    """The request failed; `AskResult.error_category` says why."""


class _ModelOutput(BaseModel):
    """Base for models the LLM fills: immutable, and tolerant of extra keys it invents."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class CitationOut(_ModelOutput):
    """One piece of evidence the answer relies on."""

    label: str = Field(description='Label of the excerpt, e.g. "c3", of one table row, e.g. "c7.r4", or of a tool result, e.g. "t1".')
    quote: str = Field(description="Text copied exactly, word for word, from that excerpt, row or tool result.")
    supports: str = Field(description="The claim in the answer that this quote backs.")


class CalculationOut(_ModelOutput):
    """One arithmetic step, done with the calculate tool."""

    expression: str = Field(description="The expression passed to the calculate tool, e.g. '0.75 * 50000'.")
    result: str = Field(description="The result the calculate tool returned, exactly as returned.")
    rule: str = Field(description='Label of the excerpt that states the formula or rule, e.g. "c2".')

    @field_validator("result", mode="before")
    @classmethod
    def _number_as_text(cls, value: object) -> object:
        return str(value) if isinstance(value, (int, float)) else value


class AgentAnswer(_ModelOutput):
    """Final answer to the question. Every claim must be backed by a citation quote."""

    status: Literal["answered", "partial", "not_found", "refused"] = Field(
        description=(
            "answered: the excerpts fully answer the question. partial: they answer part of it. "
            "not_found: the documents do not say. refused: outside this assistant's purpose."
        )
    )
    answer: str = Field(description="Short, plain answer that can be read to a caller. Keep the conditions attached to each rule.")
    citations: list[CitationOut] = Field(default_factory=list, description="Evidence for every claim in the answer.")
    calculations: list[CalculationOut] = Field(default_factory=list, description="Every arithmetic step, if any.")
    caveats: list[str] = Field(default_factory=list, description="Conflicts between sources, assumptions and limits.")
    reason: str | None = Field(
        default=None,
        description="Required for not_found and refused: why, worded as what the documents do not say, and who to ask.",
    )

    @property
    def answer_status(self) -> AnswerStatus:
        """`status` as the enum used by application code."""
        return AnswerStatus(self.status)


class Evidence(Frozen):
    """What a label handed to the model refers to in this request.

    A label like `c3` refers to a search hit. A label like `c7.r4` refers to row 4 of
    the table that hit c7 belongs to; `row` and `header` are then set and `text` is the
    single row, which is what a table citation's quote must come from.
    """

    label: str = Field(pattern=LABEL_PATTERN)
    hit: SearchHit
    row: TableRowView | None = None
    header: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """The text a quote with this label is checked against."""
        return self.row.as_text(self.header) if self.row is not None else self.hit.text


class ToolResult(Frozen):
    """What a tool label (`t1`, `t2`) handed to the model refers to: one result of an approved MCP tool.

    It is evidence like an excerpt (it can be cited, and its numbers ground the answer),
    but it is not a document: it was produced during this request by `tool` on `server`
    for the (screened) `arguments`.
    """

    label: str = Field(pattern=TOOL_LABEL_PATTERN)
    server: str
    tool: str
    arguments: str = ""
    """The screened arguments as JSON."""
    text: str
    """The result text as shown to the model, which a citation's quote must come from."""

    @property
    def source_line(self) -> str:
        """`tool working_days_between on server workday-calendar, arguments {...}`."""
        args = f", arguments {self.arguments}" if self.arguments else ""
        return f"tool {self.tool} on server {self.server}{args}"


CheckName = Literal[
    "schema",
    "label_retrieved",
    "quote_verbatim",
    "table_row_cited",
    "numbers_grounded",
    "calculation_recomputed",
    "decline_reason",
    "source_status",
]
"""`schema` plus the seven evidence checks, in the order docs/ARCHITECTURE.md section 6 lists them."""


REPAIRED_PREFIX = "Before repair: "
"""Starts the detail of a warning kept for a problem that the repair turn fixed."""


def repaired_detail(issue: CheckIssue) -> str:
    """The detail of an issue without `REPAIRED_PREFIX`."""
    return issue.detail.removeprefix(REPAIRED_PREFIX)


class CheckIssue(Frozen):
    """One problem found by the evidence checks. Shown to the model in the repair turn."""

    check: CheckName
    severity: Literal["error", "warning"] = "error"
    detail: str
    label: str | None = None
    """The citation or calculation label the issue is about, if any."""


class EvidenceReport(Frozen):
    """Result of the evidence checks on the final answer (after any repair)."""

    passed: bool
    issues: tuple[CheckIssue, ...] = ()
    repaired: bool = False
    """True when a repair turn was used."""

    @property
    def failures(self) -> tuple[CheckIssue, ...]:
        """Issues that still fail (severity `error`). `passed` is the verdict; these are why it is False."""
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def fixed_in_repair(self) -> tuple[CheckIssue, ...]:
        """Problems the repair turn fixed: kept as warnings whose detail starts with `REPAIRED_PREFIX`."""
        return tuple(i for i in self.issues if i.severity == "warning" and i.detail.startswith(REPAIRED_PREFIX))

    @property
    def warnings(self) -> tuple[CheckIssue, ...]:
        """Other warnings, which never fail the checks."""
        return tuple(i for i in self.issues if i.severity == "warning" and not i.detail.startswith(REPAIRED_PREFIX))


class ResolvedCitation(Frozen):
    """A citation joined to the source it points at, ready to display."""

    label: str
    quote: str
    supports: str
    filename: str
    title: str
    locator: str
    document_id: str
    section_id: str
    edition: str | None = None
    status: DocumentStatus
    row_number: int | None = None
    source: Literal["document", "tool"] = "document"
    """`tool` for a citation of an MCP tool result. Then `document_id` and `section_id` are empty,
    `filename` is "Tool result", `title` is "<server> / <tool>", `locator` is the arguments, and
    `status` is `current` (the result was produced during this request). Front ends show it as a
    tool result, never as a document passage."""
    tool_server: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    """Screened arguments of the tool call, as JSON."""

    @property
    def is_tool(self) -> bool:
        """True when the cited source is a tool result rather than a document."""
        return self.source == "tool"


class Usage(Frozen):
    """Measured cost of one request, read from provider responses."""

    model_calls: int = 0
    searches: int = 0
    repairs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    """None when the model has no entry in the pricing file."""


class Screened(Frozen):
    """Text after the privacy gate. Only this text reaches models, tools, logs and the UI."""

    text: str
    matched: tuple[str, ...] = ()
    """Kinds of personal information found, for example `sa_id`, `email`; never the values."""

    @property
    def found_personal_information(self) -> bool:
        """True when any pattern matched."""
        return bool(self.matched)


StepKind = Literal["privacy", "scope", "search", "table", "calculate", "mcp_tool", "checks", "repair"]
"""What one step of answering did. `checks` and `repair` are the evidence checks and the repair turn."""


class AnswerStep(Frozen):
    """One step of how an answer was produced, in the order it happened.

    Only screened text appears here: search queries and tool arguments come from the
    model, which only ever saw the screened question.
    """

    kind: StepKind
    label: str
    """Short words for a reader, for example `Searched the documents`."""
    detail: str = ""
    """What exactly: the query and number of hits, the table locator and row count, the expression and result."""


class AskResult(Frozen):
    """Everything a front end needs to show one answer, and what evaluation records."""

    trace_id: str
    """Audit record correlation id."""
    bot_id: str
    question: str
    """The question as screened by the privacy gate, never the raw input."""
    outcome: Outcome
    answer: str = ""
    reason: str | None = None
    citations: tuple[ResolvedCitation, ...] = ()
    calculations: tuple[CalculationOut, ...] = ()
    caveats: tuple[str, ...] = ()
    evidence_report: EvidenceReport | None = None
    redactions: tuple[str, ...] = ()
    """Kinds of personal information removed from the question."""
    scope: SearchScope | None = None
    clarification: str | None = None
    """Set when the question named editions ambiguously; outcome is then `not_found` and no model was called."""
    model: str = ""
    """`<provider>:<model>` actually used."""
    usage: Usage = Usage()
    latency_ms: int = 0
    error_category: ErrorCategory | None = None
    retrieved: tuple[Evidence, ...] = ()
    """Every labelled excerpt and row the model was shown, for display beside the answer."""
    tool_results: tuple[ToolResult, ...] = ()
    """Every labelled MCP tool result (`t1`, `t2`, ...) the model was shown."""
    steps: tuple[AnswerStep, ...] = ()
    """How the answer was produced, in order: privacy gate, scope, each tool call, checks and repair."""
