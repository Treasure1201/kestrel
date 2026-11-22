"""Builders and a fake `AssistantService` for the Streamlit app tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from kestrel.contracts import (
    AskResult,
    AuditCitation,
    AuditEntry,
    AuditRecord,
    AuditVerification,
    BotSummary,
    CheckIssue,
    DocumentRecord,
    DocumentStatus,
    EditionComparison,
    Evidence,
    EvidenceReport,
    Finding,
    IngestReport,
    Outcome,
    ResolvedCitation,
    SearchHit,
    SectionView,
    Usage,
)

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
BOT = BotSummary(id="demo-bot", name="Demo Assistant", description="A test assistant.", classification="internal",
                 model="anthropic:claude-sonnet-5-5", collections=("demo",))


def hit(label_n: int = 1) -> SearchHit:
    return SearchHit(
        chunk_id=f"doc1:s00{label_n}:p00", document_id="doc1", section_id=f"doc1:s00{label_n}", filename="guide.docx",
        title="Guide", locator=f"guide.docx > section {label_n}", status=DocumentStatus.CURRENT, edition="2026",
        kind="prose", text=f"Section {label_n} says the waiting period is 3 months.", score=1.0, rank=label_n,
    )


def answered(question: str = "What is the waiting period?") -> AskResult:
    return AskResult(
        trace_id="trace-answered", bot_id=BOT.id, question=question, outcome=Outcome.ANSWERED,
        answer="The waiting period is 3 months.",
        citations=(ResolvedCitation(label="c1", quote="the waiting period is 3 months", supports="the waiting period",
                                    filename="guide.docx", title="Guide", locator="guide.docx > section 1",
                                    document_id="doc1", section_id="doc1:s001", edition="2026",
                                    status=DocumentStatus.CURRENT),),
        evidence_report=EvidenceReport(passed=True), model="anthropic:claude-sonnet-5-5",
        usage=Usage(model_calls=2, searches=1, cost_usd=0.0042), latency_ms=3200,
        retrieved=(Evidence(label="c1", hit=hit(1)), Evidence(label="c2", hit=hit(2))),
        redactions=("sa_id",),
    )


def not_found() -> AskResult:
    return AskResult(
        trace_id="trace-nf", bot_id=BOT.id, question="Is dental cover offered?", outcome=Outcome.NOT_FOUND,
        reason="The approved documents do not mention dental cover; ask the product team.",
        evidence_report=EvidenceReport(passed=True), usage=Usage(model_calls=1, searches=2), latency_ms=1500,
    )


def withheld() -> AskResult:
    return AskResult(
        trace_id="trace-wh", bot_id=BOT.id, question="How much is paid?", outcome=Outcome.WITHHELD,
        answer="It pays 999.",
        evidence_report=EvidenceReport(passed=False, repaired=True, issues=(
            CheckIssue(check="numbers_grounded", detail="999 does not appear in any cited excerpt", label="c1"),
        )),
        usage=Usage(model_calls=3, searches=1, repairs=1), latency_ms=5000,
    )


def document(doc_id: str = "doc1", status: DocumentStatus = DocumentStatus.CURRENT, edition: str = "2026",
             family: str | None = "guide", effective: date | None = date(2026, 1, 1)) -> DocumentRecord:
    return DocumentRecord(
        id=doc_id, collection="demo", sha256="a" * 64, filename=f"{doc_id}.docx", title=f"Title {doc_id}",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", family=family,
        edition=edition, effective_from=effective, status=status, approved_by="reviewer", ingested_at=NOW,
        section_count=4,
    )


def audit(record_id: int, outcome: Outcome | None = Outcome.ANSWERED, *, kind: str = "ask", channel: str = "ui",
          question: str | None = None, digest: str | None = None, cost: float | None = 0.01, latency: int = 1000,
          citations: tuple[AuditCitation, ...] = (), detail: str | None = None) -> AuditRecord:
    entry = AuditEntry(
        kind=kind, trace_id=f"t{record_id}", bot_id=BOT.id, channel=channel, user="u", question_text=question,
        question_digest=digest, outcome=outcome, cost_usd=cost, latency_ms=latency, citations=citations,
        detail=detail, model="anthropic:claude-sonnet-5-5",
    )
    return AuditRecord(id=record_id, created_at=NOW.replace(minute=record_id % 60), entry=entry, prev_hash="0", hash=str(record_id))


def citation(section: str = "doc1:s001", locator: str = "guide.docx > section 1") -> AuditCitation:
    return AuditCitation(label="c1", document_id="doc1", section_id=section, locator=locator, quote="q")


class FakeService:
    """In-memory stand-in for `AssistantService`."""

    def __init__(self, *, bots=(BOT,), documents=(), findings=(), audit_records=(), ask_result=None,
                 bots_error: Exception | None = None, documents_error: Exception | None = None,
                 comparison: EditionComparison | None = None) -> None:
        self.comparison = comparison
        self.bots = list(bots)
        self.documents = list(documents)
        self.findings = list(findings)
        self.audit_records = list(audit_records)
        self.ask_result = ask_result or answered()
        self.bots_error = bots_error
        self.documents_error = documents_error
        self.asked: list[tuple[str, str, str | None]] = []
        self.blocked: list[tuple[str, str]] = []

    def ask(self, bot_id, question, principal, model=None):
        self.asked.append((bot_id, question, model))
        return self.ask_result

    def search(self, bot_id, query, principal, limit=8):
        raise NotImplementedError

    def get_section(self, bot_id, section_id, principal):
        return SectionView(section_id=section_id, document_id="doc1", filename="guide.docx", title="Guide",
                           locator="guide.docx > section 1", kind="prose", text="Whole section text.",
                           status=DocumentStatus.CURRENT, edition="2026")

    def list_bots(self, principal):
        if self.bots_error:
            raise self.bots_error
        return self.bots

    def list_documents(self, bot_id, principal):
        if self.documents_error:
            raise self.documents_error
        return self.documents

    def list_findings(self, bot_id, principal, document_id=None):
        return [f for f in self.findings if document_id in (None, f.document_id)]

    def ingest(self, principal, bot_id=None):
        return IngestReport()

    def block_document(self, document_id, principal, reason):
        self.blocked.append((document_id, reason))
        doc = next(d for d in self.documents if d.id == document_id)
        updated = doc.model_copy(update={"status": DocumentStatus.BLOCKED, "status_reason": reason})
        self.documents = [updated if d.id == document_id else d for d in self.documents]
        return updated

    def unblock_document(self, document_id, principal, reason):
        raise NotImplementedError

    def affected_answers(self, document_id, principal):
        return [r for r in self.audit_records if any(c.document_id == document_id for c in r.entry.citations)]

    def compare_editions(self, bot_id, document_id, principal):
        if self.comparison is not None:
            return self.comparison
        doc = next(d for d in self.documents if d.id == document_id)
        return EditionComparison(bot_id=bot_id, document_id=document_id, family=doc.family, editions=(doc,),
                                 note=f"{doc.filename} has no other current or superseded edition to compare with.")

    def audit_tail(self, principal, limit=20, bot_id=None):
        return self.audit_records[:limit]

    def audit_verify(self, principal):
        return AuditVerification(ok=True, checked=len(self.audit_records))

    def audit_prune(self, principal, bot_id=None):
        return []

    def doctor(self):
        raise NotImplementedError


def finding() -> Finding:
    return Finding(document_id="doc1", locator="doc1.docx > section 4", kind="hidden_text", severity="high",
                   detail="White text on a white page.", excerpt="hidden sample excerpt")
