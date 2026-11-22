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

    label: str = Field(description='Label of the excerpt, e.g. "c3", or of one table row, e.g. "c7.r4".')
    quote: str = Field(description="Text copied exactly, word for word, from that excerpt or row.")
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
"""`schema` plus the seven evidence checks, in the order DESIGN.md section 6 lists them."""


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
