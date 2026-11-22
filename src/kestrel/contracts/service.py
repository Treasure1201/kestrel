"""`AssistantService`: the only entry point the CLI, Streamlit app and MCP server use.

All methods are synchronous; the service runs the agent on an event loop it owns.

Access rules, applied to every method that takes a `Principal`:

- a bot is visible only to principals with a role in its `audience`;
- document, section, finding and audit reads are limited to bots the principal may use;
- operator methods (`ingest`, `block_document`, `unblock_document`, `audit_prune`,
  `audit_verify`) also require `OPERATOR_ROLE`.

Errors: `ask` never raises for a request-level failure. Every outcome, including an
access refusal, a privacy block and an error, is audited and returned as an `AskResult`
(`outcome` `refused` or `error`, with `error_category`). Every other method raises
`KestrelError` subclasses, which front ends show as a message and a non-zero exit.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from kestrel.contracts._base import Frozen, Principal
from kestrel.contracts.answers import AskResult
from kestrel.contracts.audit import AuditRecord, AuditVerification, BotSummary, DoctorReport, PruneResult
from kestrel.contracts.documents import DocumentRecord, DocumentStatus, Finding
from kestrel.contracts.retrieval import SearchResult, SectionView

OPERATOR_ROLE = "staff"
"""Role required, in addition to bot access, for operator methods."""

MAX_QUESTION_CHARS = 2000
"""Longer questions are rejected with `InvalidInputError` before screening."""


class IngestedFile(Frozen):
    """What ingest did with one file."""

    collection: str
    filename: str
    document_id: str | None = None
    status: DocumentStatus | None = None
    action: Literal["added", "updated", "unchanged", "removed", "failed"]
    findings: int = 0
    detail: str | None = None


class IngestReport(Frozen):
    """Result of `AssistantService.ingest`."""

    files: tuple[IngestedFile, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        """True when any file was added, updated or removed."""
        return any(f.action in {"added", "updated", "removed"} for f in self.files)


@runtime_checkable
class AssistantService(Protocol):
    """Synchronous facade over bots, ingest, retrieval, answering and audit."""

    def ask(self, bot_id: str, question: str, principal: Principal, model: str | None = None) -> AskResult:
        """Answer a question with one bot: access check, privacy gate, agent, evidence checks, audit.

        `model` overrides the bot's default `<provider>:<model>`, subject to the bot's
        allowed providers and egress policy.
        """
        ...

    def search(self, bot_id: str, query: str, principal: Principal, limit: int = 8) -> SearchResult:
        """Screened hybrid search over the bot's current documents, without a model."""
        ...

    def get_section(self, bot_id: str, section_id: str, principal: Principal) -> SectionView:
        """One whole section, if it belongs to the bot's collections and is not blocked."""
        ...

    def list_bots(self, principal: Principal) -> list[BotSummary]:
        """Bots the principal may use."""
        ...

    def list_documents(self, bot_id: str, principal: Principal) -> list[DocumentRecord]:
        """Every document in the bot's collections, with status."""
        ...

    def list_findings(self, bot_id: str, principal: Principal, document_id: str | None = None) -> list[Finding]:
        """Scanner findings in the bot's collections, optionally for one document."""
        ...

    def ingest(self, principal: Principal, bot_id: str | None = None) -> IngestReport:
        """Ingest the source folders of one bot, or of every bot. Idempotent."""
        ...

    def block_document(self, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
        """Withdraw a document from search immediately, and audit it."""
        ...

    def unblock_document(self, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
        """Restore a blocked document to the status its manifest entry gives it, and audit it."""
        ...

    def affected_answers(self, document_id: str, principal: Principal) -> list[AuditRecord]:
        """Past answers that cited a document, most recent first."""
        ...

    def audit_tail(self, principal: Principal, limit: int = 20, bot_id: str | None = None) -> list[AuditRecord]:
        """Most recent audit records the principal may see."""
        ...

    def audit_verify(self, principal: Principal) -> AuditVerification:
        """Check the whole hash chain."""
        ...

    def audit_prune(self, principal: Principal, bot_id: str | None = None) -> list[PruneResult]:
        """Apply each bot's `retention_days`, writing a checkpoint per bot."""
        ...

    def doctor(self) -> DoctorReport:
        """Check the environment end to end; never prints secret values."""
        ...
