"""A tiny in-memory `AssistantService` for testing the MCP server. TEST ONLY.

`kestrel mcp` uses it instead of the real service when `KESTREL_MCP_FAKE=1`, so a test
can start the server as a real child process without documents, a database or a model.
It holds one synthetic section, never calls a model and never writes anything.
"""

from __future__ import annotations

import uuid

from kestrel.contracts import (
    AskResult,
    AuditRecord,
    AuditVerification,
    BotSummary,
    DocumentRecord,
    DocumentStatus,
    DoctorCheck,
    DoctorReport,
    EvidenceReport,
    Finding,
    IngestReport,
    Outcome,
    Principal,
    PruneResult,
    ResolvedCitation,
    SearchHit,
    SearchResult,
    SectionView,
)
from kestrel.errors import AccessDeniedError, ErrorCategory, InvalidInputError

FAKE_FILENAME = "example-notes.txt"
FAKE_SECTION_ID = "fake0000:s000"
FAKE_TEXT = "The help desk is open from 08:00 to 17:00 on working days."
RESTRICTED_BOT = "restricted-bot"
"""A bot no principal may use, for access-denied tests."""


class FakeAssistantService:
    """Deterministic stand-in: one usable bot (`bot_id`) and one restricted bot."""

    def __init__(self, bot_id: str = "example-bot") -> None:
        self.bot_id = bot_id
        self._audiences = {bot_id: None, RESTRICTED_BOT: frozenset({"no-such-role"})}

    def _check(self, bot_id: str, principal: Principal) -> None:
        if bot_id not in self._audiences:
            raise InvalidInputError(f"Unknown bot {bot_id!r}. Use list_bots to see the bots you may use.")
        audience = self._audiences[bot_id]
        if audience is not None and not principal.may_use(audience):
            raise AccessDeniedError(f"You do not have access to bot {bot_id!r}.")

    def _hit(self) -> SearchHit:
        return SearchHit(
            chunk_id=f"{FAKE_SECTION_ID}:p00",
            document_id="fake0000",
            section_id=FAKE_SECTION_ID,
            filename=FAKE_FILENAME,
            title="Example notes",
            locator="Section 1",
            status=DocumentStatus.CURRENT,
            kind="prose",
            text=FAKE_TEXT,
            score=1.0,
            rank=1,
        )

    def ask(self, bot_id: str, question: str, principal: Principal, model: str | None = None) -> AskResult:
        trace_id = uuid.uuid4().hex
        try:
            self._check(bot_id, principal)
        except (InvalidInputError, AccessDeniedError) as exc:
            return AskResult(trace_id=trace_id, bot_id=bot_id, question=question, outcome=Outcome.ERROR, error_category=exc.category)
        citation = ResolvedCitation(
            label="c1",
            quote=FAKE_TEXT,
            supports="opening hours",
            filename=FAKE_FILENAME,
            title="Example notes",
            locator="Section 1",
            document_id="fake0000",
            section_id=FAKE_SECTION_ID,
            status=DocumentStatus.CURRENT,
        )
        return AskResult(
            trace_id=trace_id,
            bot_id=bot_id,
            question=question,
            outcome=Outcome.ANSWERED,
            answer="The help desk is open from 08:00 to 17:00 on working days.",
            citations=(citation,),
            caveats=("Synthetic test answer.",),
            evidence_report=EvidenceReport(passed=True),
            model=model or "fake:none",
            latency_ms=1,
        )

    def search(self, bot_id: str, query: str, principal: Principal, limit: int = 8) -> SearchResult:
        self._check(bot_id, principal)
        return SearchResult(hits=(self._hit(),)[:limit])

    def get_section(self, bot_id: str, section_id: str, principal: Principal) -> SectionView:
        self._check(bot_id, principal)
        if section_id != FAKE_SECTION_ID:
            raise InvalidInputError(f"Unknown section {section_id!r}.")
        return SectionView(
            section_id=FAKE_SECTION_ID,
            document_id="fake0000",
            filename=FAKE_FILENAME,
            title="Example notes",
            locator="Section 1",
            kind="prose",
            text=FAKE_TEXT,
            status=DocumentStatus.CURRENT,
        )

    def list_bots(self, principal: Principal) -> list[BotSummary]:
        return [
            BotSummary(
                id=self.bot_id,
                name="Example bot",
                description="Synthetic bot for tests.",
                classification="internal",
                model="fake:none",
                collections=("example",),
            )
        ]

    def list_documents(self, bot_id: str, principal: Principal) -> list[DocumentRecord]:
        self._check(bot_id, principal)
        return []

    def list_findings(self, bot_id: str, principal: Principal, document_id: str | None = None) -> list[Finding]:
        self._check(bot_id, principal)
        return []

    def _operator(self) -> AccessDeniedError:
        return AccessDeniedError("Operator actions are not available in the test service.", category=ErrorCategory.ACCESS_DENIED)

    def ingest(self, principal: Principal, bot_id: str | None = None) -> IngestReport:
        raise self._operator()

    def block_document(self, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
        raise self._operator()

    def unblock_document(self, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
        raise self._operator()

    def affected_answers(self, document_id: str, principal: Principal) -> list[AuditRecord]:
        return []

    def audit_tail(self, principal: Principal, limit: int = 20, bot_id: str | None = None) -> list[AuditRecord]:
        return []

    def audit_verify(self, principal: Principal) -> AuditVerification:
        return AuditVerification(ok=True)

    def audit_prune(self, principal: Principal, bot_id: str | None = None) -> list[PruneResult]:
        raise self._operator()

    def doctor(self) -> DoctorReport:
        return DoctorReport(checks=(DoctorCheck(name="fake service", status="ok", detail="test-only service"),))
