"""Audit records, environment checks and bot summaries."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from kestrel.contracts._base import Channel, Frozen
from kestrel.contracts.answers import Outcome
from kestrel.errors import ErrorCategory

AuditKind = Literal["ask", "block", "unblock", "checkpoint"]
"""`checkpoint` rows are written by pruning and carry the hash chain forward."""


class AuditCitation(Frozen):
    """The part of a citation worth keeping in the audit log."""

    label: str
    document_id: str
    section_id: str
    locator: str
    quote: str


class AuditEntry(Frozen):
    """One event to append. The log adds the id, timestamp and hashes.

    For `ask` events, `question_text` holds the screened question only when the bot's
    policy sets `audit_question_text: redacted`; `question_digest` (SHA-256 of the
    screened text) is always stored.
    """

    kind: AuditKind = "ask"
    trace_id: str
    bot_id: str | None = None
    channel: Channel
    user: str
    roles: tuple[str, ...] = ()
    question_text: str | None = None
    question_digest: str | None = None
    redactions: tuple[str, ...] = ()
    retrieved_chunk_ids: tuple[str, ...] = ()
    citations: tuple[AuditCitation, ...] = ()
    calculations: tuple[str, ...] = ()
    """Each as `expression = result`."""
    outcome: Outcome | None = None
    error_category: ErrorCategory | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: int = 0
    document_id: str | None = None
    """Target of `block` and `unblock` events."""
    detail: str | None = None
    """Free text such as a block reason. Must already be screened."""

    @property
    def cited_document_ids(self) -> tuple[str, ...]:
        """Distinct documents the answer cited, in citation order."""
        return tuple(dict.fromkeys(c.document_id for c in self.citations))


class AuditRecord(Frozen):
    """A stored audit row."""

    id: int
    created_at: datetime
    entry: AuditEntry
    prev_hash: str
    hash: str


class AuditReceipt(Frozen):
    """Returned by `AuditLog.append`."""

    record_id: int
    hash: str


class AuditVerification(Frozen):
    """Result of walking the hash chain."""

    ok: bool
    checked: int = 0
    first_broken_id: int | None = None
    """The first row whose stored hash, or link to the previous row, does not match."""
    detail: str | None = None


class PruneResult(Frozen):
    """Result of pruning one bot's rows past retention."""

    removed: int
    checkpoint_id: int | None = None


class DoctorCheck(Frozen):
    """One environment check. `fix` is the exact command or setting that resolves a failure."""

    name: str
    status: Literal["ok", "warn", "fail"]
    detail: str
    fix: str | None = None


class DoctorReport(Frozen):
    """All environment checks. Never contains secret values, only whether keys are present."""

    checks: tuple[DoctorCheck, ...] = ()

    @property
    def ok(self) -> bool:
        """True when no check failed (warnings allowed)."""
        return all(check.status != "fail" for check in self.checks)


class BotSummary(Frozen):
    """A bot as listed to a principal who may use it."""

    id: str
    name: str
    description: str = ""
    classification: str
    model: str
    collections: tuple[str, ...] = Field(min_length=1)
