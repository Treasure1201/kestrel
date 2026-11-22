"""Command-line interface tests over a fake `AssistantService` with canned data."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from kestrel.config import Settings
from kestrel.contracts import (
    AskResult,
    AssistantService,
    AuditEntry,
    AuditRecord,
    AuditVerification,
    BotSummary,
    CalculationOut,
    CheckIssue,
    DoctorCheck,
    DoctorReport,
    DocumentRecord,
    DocumentStatus,
    EditionComparison,
    Evidence,
    EvidenceReport,
    Finding,
    IngestedFile,
    IngestReport,
    Outcome,
    Principal,
    PruneResult,
    ResolvedCitation,
    SearchHit,
    SearchResult,
    SearchScope,
    SectionView,
    Usage,
)
from kestrel.errors import AccessDeniedError, ErrorCategory, InvalidInputError, ProviderUnavailableError
from kestrel.interfaces import cli_runtime
from kestrel.interfaces.cli import app

BOT = "demo-bot"
DOC = "0123456789abcdef"
SECTION = f"{DOC}:s004"
NOW = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
TRICKY = "Cover applies “from day one” – see terms…"

HIT = SearchHit(
    chunk_id=f"{SECTION}:p00", document_id=DOC, section_id=SECTION, filename="guide.pdf", title="Guide",
    locator="guide.pdf > section 4.2 > Waiting periods", status=DocumentStatus.CURRENT, kind="prose",
    text=TRICKY, score=0.8123, rank=1,
)
CITATION = ResolvedCitation(
    label="c1", quote=TRICKY, supports="cover starts at once", filename="guide.pdf", title="Guide",
    locator="guide.pdf > section 4.2 > Waiting periods", document_id=DOC, section_id=SECTION,
    status=DocumentStatus.CURRENT,
)
DOCUMENT = DocumentRecord(
    id=DOC, collection=BOT, sha256="a" * 64, filename="guide.pdf", title="Guide", media_type="application/pdf",
    edition="2026", status=DocumentStatus.CURRENT, ingested_at=NOW, section_count=12, finding_count=1,
)
AUDIT = AuditRecord(
    id=7, created_at=NOW, prev_hash="0" * 64, hash="1" * 64,
    entry=AuditEntry(trace_id="t-123", bot_id=BOT, channel="cli", user="local-user", outcome=Outcome.ANSWERED,
                     question_digest="f" * 64, model="ollama:tiny"),
)


def make_result(outcome: Outcome = Outcome.ANSWERED, **overrides: Any) -> AskResult:
    """An AskResult with every display field filled."""
    fields: dict[str, Any] = {
        "trace_id": "t-123", "bot_id": BOT, "question": "When does cover start?", "outcome": outcome,
        "answer": "Cover starts on the first day.", "citations": (CITATION,),
        "calculations": (CalculationOut(expression="0.5 * 100", result="50", rule="c1"),),
        "caveats": ("Check the schedule.",),
        "evidence_report": EvidenceReport(passed=True),
        "scope": SearchScope(collections=(BOT,)), "model": "ollama:tiny",
        "usage": Usage(model_calls=2, searches=1, input_tokens=1200, output_tokens=150, cost_usd=0.0042),
        "latency_ms": 2345, "retrieved": (Evidence(label="c1", hit=HIT),),
    }
    fields.update(overrides)
    return AskResult(**fields)


class FakeService:
    """Implements the AssistantService Protocol with canned data, recording calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.next_result = make_result()
        self.doctor_report = DoctorReport(checks=(DoctorCheck(name="database", status="ok", detail="writable"),))
        self.verification = AuditVerification(ok=True, checked=3)
        self.raise_on: dict[str, Exception] = {}
        self.comparison = EditionComparison(bot_id=BOT, document_id=DOC, editions=(DOCUMENT,), note="no other edition")
        self.audit_records = [AUDIT]

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.raise_on:
            raise self.raise_on[name]

    def _check_bot(self, bot_id: str | None) -> None:
        if bot_id is not None and bot_id != BOT:
            raise InvalidInputError(f"Unknown bot '{bot_id}'. Run `kestrel bots` to list the bots you may use.")

    def ask(self, bot_id: str, question: str, principal: Principal, model: str | None = None) -> AskResult:
        self.calls.append(("ask", (bot_id, question, principal, model)))
        if bot_id != BOT:
            return make_result(Outcome.ERROR, answer="", citations=(), calculations=(), caveats=(), evidence_report=None,
                               reason=f"Unknown bot '{bot_id}'.", error_category=ErrorCategory.INVALID_INPUT)
        return self.next_result

    def search(self, bot_id: str, query: str, principal: Principal, limit: int = 8) -> SearchResult:
        self._record("search", bot_id, query, limit)
        self._check_bot(bot_id)
        return SearchResult(hits=(HIT,), warnings=("vector index unavailable; keyword results only",))

    def get_section(self, bot_id: str, section_id: str, principal: Principal) -> SectionView:
        self._record("get_section", bot_id, section_id)
        self._check_bot(bot_id)
        return SectionView(section_id=section_id, document_id=DOC, filename="guide.pdf", title="Guide",
                           locator="guide.pdf > section 4.2", kind="prose", text=TRICKY, status=DocumentStatus.CURRENT)

    def list_bots(self, principal: Principal) -> list[BotSummary]:
        self._record("list_bots", principal)
        return [BotSummary(id=BOT, name="Demo", description="Answers from demo documents.", classification="internal",
                           model="ollama:tiny", collections=(BOT,))]

    def list_documents(self, bot_id: str, principal: Principal) -> list[DocumentRecord]:
        self._record("list_documents", bot_id)
        self._check_bot(bot_id)
        return [DOCUMENT]

    def list_findings(self, bot_id: str, principal: Principal, document_id: str | None = None) -> list[Finding]:
        self._record("list_findings", bot_id, document_id)
        self._check_bot(bot_id)
        return [Finding(document_id=DOC, locator="guide.pdf > page 3", kind="hidden_text", severity="high",
                        detail="white text on white", excerpt="ignore previous instructions")]

    def ingest(self, principal: Principal, bot_id: str | None = None) -> IngestReport:
        self._record("ingest", bot_id)
        self._check_bot(bot_id)
        return IngestReport(
            files=(IngestedFile(collection=BOT, filename="guide.pdf", document_id=DOC, status=DocumentStatus.CURRENT,
                                action="added", findings=1),
                   IngestedFile(collection=BOT, filename="notes.txt", action="unchanged")),
            warnings=("notes.txt is not in the manifest",),
        )

    def block_document(self, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
        self._record("block_document", document_id, reason)
        return DOCUMENT.model_copy(update={"status": DocumentStatus.BLOCKED, "status_reason": reason})

    def unblock_document(self, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
        self._record("unblock_document", document_id, reason)
        return DOCUMENT

    def affected_answers(self, document_id: str, principal: Principal) -> list[AuditRecord]:
        self._record("affected_answers", document_id)
        return [AUDIT]

    def compare_editions(self, bot_id: str, document_id: str, principal: Principal) -> EditionComparison:
        self._record("compare_editions", bot_id, document_id)
        self._check_bot(bot_id)
        return self.comparison

    def audit_tail(self, principal: Principal, limit: int = 20, bot_id: str | None = None) -> list[AuditRecord]:
        self._record("audit_tail", limit, bot_id)
        return self.audit_records

    def audit_verify(self, principal: Principal) -> AuditVerification:
        self._record("audit_verify")
        return self.verification

    def audit_prune(self, principal: Principal, bot_id: str | None = None) -> list[PruneResult]:
        self._record("audit_prune", bot_id)
        return [PruneResult(removed=4, checkpoint_id=9)]

    def doctor(self) -> DoctorReport:
        self._record("doctor")
        return self.doctor_report


@pytest.fixture
def fake(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FakeService:
    service = FakeService()
    cli_settings = settings.model_copy(update={"default_bot": BOT})
    monkeypatch.setattr(cli_runtime, "get_settings", lambda: cli_settings)
    monkeypatch.setattr(cli_runtime, "get_service", lambda s=None: service)
    monkeypatch.setattr(cli_runtime.STATE, "debug", False)
    return service


def run(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(app, list(args), input=input)


def assert_console_safe(result: Any) -> None:
    (result.stdout + result.stderr).encode("cp1252")
    assert (result.stdout + result.stderr).isascii()
    assert "Traceback" not in result.stdout + result.stderr


def test_version_needs_no_service(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> None:
        raise AssertionError("version must not build the service")

    monkeypatch.setattr(cli_runtime, "get_service", boom)
    monkeypatch.setattr(cli_runtime, "get_settings", boom)
    result = run("version")
    assert result.exit_code == 0
    assert result.stdout.startswith("kestrel ")


def test_ask_answered_prints_everything(fake: FakeService) -> None:
    result = run("ask", "When does cover start?")
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert out.splitlines()[0] == "Answered - evidence checks passed"
    assert "Cover starts on the first day." in out
    assert "Caveats:" in out and "Check the schedule." in out
    assert "0.5 * 100 = 50" in out
    assert "[1] guide.pdf > section 4.2 > Waiting periods" in out
    assert '"Cover applies "from day one" - see terms..."' in out
    assert "Model ollama:tiny | 2 model calls | 1200 in / 150 out tokens | $0.0042 | 2.3 s | trace t-123" in out
    assert "Retrieved evidence" not in out
    name, (bot, question, principal, model) = fake.calls[-1]
    assert (name, bot, question, model) == ("ask", BOT, "When does cover start?", None)
    assert principal.channel == "cli" and principal.user == "local-user"
    assert_console_safe(result)


@pytest.mark.parametrize(
    ("outcome", "label", "code"),
    [
        (Outcome.ANSWERED, "Answered", 0),
        (Outcome.PARTIAL, "Partial", 0),
        (Outcome.NOT_FOUND, "Not in the documents", 0),
        (Outcome.REFUSED, "Refused", 0),
        (Outcome.WITHHELD, "Withheld", 3),
        (Outcome.ERROR, "Error (provider_unavailable)", 1),
    ],
)
def test_ask_outcomes_and_exit_codes(fake: FakeService, outcome: Outcome, label: str, code: int) -> None:
    extra: dict[str, Any] = {"reason": "The documents do not say; ask the product team."}
    if outcome == Outcome.ERROR:
        extra.update(error_category=ErrorCategory.PROVIDER_UNAVAILABLE, evidence_report=None)
    if outcome == Outcome.WITHHELD:
        extra["evidence_report"] = EvidenceReport(
            passed=False, repaired=True,
            issues=(CheckIssue(check="quote_verbatim", detail="quote not found in excerpt", label="c1"),),
        )
    fake.next_result = make_result(outcome, **extra)
    result = run("ask", "q", "--bot", BOT)
    assert result.exit_code == code
    assert result.stdout.splitlines()[0].startswith(label)
    assert "Reason: The documents do not say" in result.stdout
    if outcome == Outcome.WITHHELD:
        assert "evidence checks found 1 issue(s)" in result.stdout
        assert "[error] quote_verbatim c1: quote not found in excerpt" in result.stdout
    assert_console_safe(result)


def test_ask_cost_unknown_and_model_override(fake: FakeService) -> None:
    fake.next_result = make_result(usage=Usage(model_calls=1))
    result = run("ask", "q", "--model", "openai:some-model")
    assert "1 model call |" in result.stdout and "cost unknown" in result.stdout
    assert fake.calls[-1][1][3] == "openai:some-model"


def test_ask_trace_shows_evidence_and_checks(fake: FakeService) -> None:
    result = run("ask", "q", "--trace")
    assert result.exit_code == 0
    assert "Trace:" in result.stdout
    assert "Scope: collections demo-bot; status current" in result.stdout
    assert "Evidence checks: passed; 0 issue(s)" in result.stdout
    assert "[c1] guide.pdf > section 4.2 > Waiting periods (score 0.812)" in result.stdout
    assert_console_safe(result)


def test_ask_json_round_trips(fake: FakeService) -> None:
    fake.next_result = make_result(redactions=("email",))
    result = run("ask", "q", "--json")
    assert result.exit_code == 0
    parsed = AskResult.model_validate(json.loads(result.stdout))
    assert parsed == fake.next_result
    assert result.stdout.isascii()


def test_ask_json_keeps_exit_code(fake: FakeService) -> None:
    fake.next_result = make_result(Outcome.WITHHELD)
    result = run("ask", "q", "--json")
    assert result.exit_code == 3  # not 2, which Typer uses for a usage error
    assert AskResult.model_validate_json(result.stdout).outcome == Outcome.WITHHELD


def test_ask_redaction_notice(fake: FakeService) -> None:
    fake.next_result = make_result(redactions=("email", "sa_id", "email"))
    result = run("ask", "q")
    assert "Personal information removed from the question: 3 items (email, sa_id)." in result.stdout


def test_ask_unknown_bot(fake: FakeService) -> None:
    result = run("ask", "q", "--bot", "nope")
    assert result.exit_code == 1
    assert result.stdout.startswith("Error (invalid_input)")
    assert "Unknown bot 'nope'" in result.stdout


def test_ask_clarification(fake: FakeService) -> None:
    fake.next_result = make_result(Outcome.NOT_FOUND, answer="", citations=(), evidence_report=None,
                                   clarification="Name one edition: 2025 or 2026.")
    result = run("ask", "q")
    assert result.exit_code == 0
    assert "Please clarify: Name one edition: 2025 or 2026." in result.stdout


def test_empty_question_is_a_user_error(fake: FakeService) -> None:
    result = run("ask", "   ")
    assert result.exit_code == 1
    assert result.stderr.strip() == "Error: the question is empty."


def test_kestrel_error_is_one_stderr_line(fake: FakeService) -> None:
    fake.raise_on["list_bots"] = AccessDeniedError("You may not use any bot.")
    result = run("bots")
    assert result.exit_code == 1
    assert result.stderr.strip().splitlines() == ["Error: You may not use any bot."]
    assert result.stdout == ""
    assert "Traceback" not in result.output


def test_debug_flag_shows_traceback(fake: FakeService) -> None:
    fake.raise_on["list_bots"] = ProviderUnavailableError("Model server unreachable.")
    result = run("--debug", "bots")
    assert result.exit_code == 1
    assert "Traceback" in result.stderr
    assert result.stderr.strip().splitlines()[-1] == "Error: Model server unreachable."


def test_unexpected_error_has_no_traceback(fake: FakeService) -> None:
    fake.raise_on["doctor"] = RuntimeError("secret detail")
    result = run("doctor")
    assert result.exit_code == 1
    assert result.stderr.strip() == "Error: unexpected RuntimeError. Run again with --debug for details."
    assert "secret detail" not in result.output


def test_unknown_bot_on_other_commands(fake: FakeService) -> None:
    for args in (("search", "x", "--bot", "nope"), ("docs", "list", "--bot", "nope"), ("ingest", "--bot", "nope")):
        result = run(*args)
        assert result.exit_code == 1, args
        assert result.stderr.startswith("Error: Unknown bot 'nope'")


def test_missing_service_package_is_a_clear_error(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_runtime, "get_settings", lambda: settings)
    monkeypatch.setitem(sys.modules, "kestrel.service", None)
    result = run("bots")
    assert result.exit_code == 1
    assert "assistant service is not available" in result.stderr


def test_doctor(fake: FakeService) -> None:
    assert run("doctor").exit_code == 0
    fake.doctor_report = DoctorReport(checks=(
        DoctorCheck(name="database", status="ok", detail="writable"),
        DoctorCheck(name="anthropic key", status="fail", detail="not set", fix="set ANTHROPIC_API_KEY in .env"),
        DoctorCheck(name="model", status="warn", detail="not on the tested list", fix="use a tested model"),
    ))
    result = run("doctor")
    assert result.exit_code == 1
    assert "FAIL" in result.stdout and "WARN" in result.stdout
    assert "anthropic key: set ANTHROPIC_API_KEY in .env" in result.stdout
    assert "3 check(s): 1 failed, 1 warning(s)." in result.stdout


def test_ingest(fake: FakeService) -> None:
    result = run("ingest")
    assert result.exit_code == 0
    assert "guide.pdf" in result.stdout and "added" in result.stdout
    assert "Warning: notes.txt is not in the manifest" in result.stdout
    assert "2 file(s): 1 added, 1 unchanged." in result.stdout
    assert fake.calls[-1] == ("ingest", (BOT,))
    run("ingest", "--all")
    assert fake.calls[-1] == ("ingest", (None,))


def test_bots(fake: FakeService) -> None:
    result = run("bots")
    assert result.exit_code == 0
    assert "demo-bot - Demo  (default)" in result.stdout
    assert "classification: internal; model: ollama:tiny; collections: demo-bot" in result.stdout


def test_chat_loop(fake: FakeService) -> None:
    result = run("chat", input="first question\n\nsecond question\nquit\nnever asked\n")
    assert result.exit_code == 0
    asked = [c[1][1] for c in fake.calls if c[0] == "ask"]
    assert asked == ["first question", "second question"]
    assert result.stdout.count("Answered - evidence checks passed") == 2
    assert "Goodbye." in result.stdout


def test_chat_ends_on_eof(fake: FakeService) -> None:
    result = run("chat", input="one\n")
    assert result.exit_code == 0
    assert [c[0] for c in fake.calls] == ["ask"]


def test_search(fake: FakeService) -> None:
    result = run("search", "waiting period", "--limit", "3")
    assert result.exit_code == 0
    assert fake.calls[-1] == ("search", (BOT, "waiting period", 3))
    assert "Warning: vector index unavailable" in result.stdout
    assert "[1] guide.pdf > section 4.2 > Waiting periods  score 0.812" in result.stdout
    assert f"section {SECTION}" in result.stdout
    assert_console_safe(result)


def test_docs_commands(fake: FakeService) -> None:
    listed = run("docs", "list")
    assert listed.exit_code == 0 and DOC in listed.stdout and "current" in listed.stdout
    findings = run("docs", "findings", DOC)
    assert findings.exit_code == 0 and "HIGH hidden_text" in findings.stdout
    assert fake.calls[-1] == ("list_findings", (BOT, DOC))
    shown = run("docs", "show", SECTION)
    assert shown.exit_code == 0 and "guide.pdf > section 4.2" in shown.stdout
    assert_console_safe(shown)
    blocked = run("docs", "block", DOC, "--reason", "wrong edition")
    assert blocked.exit_code == 0 and "status is now blocked" in blocked.stdout
    assert fake.calls[-1] == ("block_document", (DOC, "wrong edition"))
    unblocked = run("docs", "unblock", DOC, "--reason", "fixed")
    assert unblocked.exit_code == 0 and "status is now current" in unblocked.stdout
    affected = run("docs", "affected", DOC)
    assert affected.exit_code == 0 and "t-123" in affected.stdout and "1 answer(s) cited" in affected.stdout


def test_docs_block_requires_reason(fake: FakeService) -> None:
    assert run("docs", "block", DOC).exit_code != 0
    result = run("docs", "block", DOC, "--reason", "  ")
    assert result.exit_code == 1 and "--reason must not be empty" in result.stderr


def test_audit_commands(fake: FakeService) -> None:
    tail = run("audit", "tail", "--limit", "5")
    assert tail.exit_code == 0 and "t-123" in tail.stdout and "answered" in tail.stdout
    assert fake.calls[-1] == ("audit_tail", (5, BOT))
    run("audit", "tail", "--all")
    assert fake.calls[-1] == ("audit_tail", (20, None))
    verified = run("audit", "verify")
    assert verified.exit_code == 0 and "Audit chain intact: 3 record(s) checked." in verified.stdout
    fake.verification = AuditVerification(ok=False, checked=5, first_broken_id=4, detail="hash mismatch")
    broken = run("audit", "verify")
    assert broken.exit_code == 1 and "BROKEN at record 4: hash mismatch" in broken.stdout
    pruned = run("audit", "prune")
    assert pruned.exit_code == 0 and "Removed 4 record(s); checkpoint record 9." in pruned.stdout
    assert fake.calls[-1] == ("audit_prune", (BOT,))


def test_mcp_starts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[bool] = []
    module = ModuleType("kestrel.interfaces.mcp_server")
    module.main = lambda: started.append(True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kestrel.interfaces.mcp_server", module)
    assert run("mcp").exit_code == 0
    assert started == [True]


def test_mcp_missing_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "kestrel.interfaces.mcp_server", None)
    result = run("mcp")
    assert result.exit_code == 1
    assert result.stderr.strip() == "Error: the MCP server is not available in this installation."


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeService(), AssistantService)


def test_safe_text_handles_typography() -> None:
    assert cli_runtime.safe(TRICKY) == 'Cover applies "from day one" - see terms...'
    assert cli_runtime.safe("café 中") == "cafe ?"
