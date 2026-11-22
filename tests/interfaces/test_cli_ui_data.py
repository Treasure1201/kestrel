"""Command line: answer steps, repair shown as fixed, withheld exit code, examples, counts, editions, audit reasons."""

from __future__ import annotations

from datetime import date

import pytest
from interfaces.test_cli import AUDIT, BOT, DOC, DOCUMENT, FakeService, assert_console_safe, make_result, run

from kestrel.config import Settings
from kestrel.contracts import (
    REPAIRED_PREFIX,
    AnswerStep,
    BotSummary,
    CheckIssue,
    DocumentStatus,
    EditionComparison,
    EvidenceReport,
    Outcome,
    SectionChange,
    ValueChange,
)
from kestrel.interfaces import cli_runtime
from kestrel.interfaces.mcp_models import ask_output


@pytest.fixture
def fake(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FakeService:
    service = FakeService()
    cli_settings = settings.model_copy(update={"default_bot": BOT})
    monkeypatch.setattr(cli_runtime, "get_settings", lambda: cli_settings)
    monkeypatch.setattr(cli_runtime, "get_service", lambda s=None: service)
    monkeypatch.setattr(cli_runtime.STATE, "debug", False)
    return service

STEPS = (
    AnswerStep(kind="privacy", label="Checked the question for personal information", detail="none found"),
    AnswerStep(kind="search", label="Searched the documents", detail='"waiting period": 4 hits'),
    AnswerStep(kind="checks", label="Evidence checks passed", detail="no check failed"),
)
REPAIRED = EvidenceReport(passed=True, repaired=True, issues=(
    CheckIssue(check="quote_verbatim", severity="warning", detail=f"{REPAIRED_PREFIX}quote not found in excerpt", label="c1"),
))


def test_trace_lists_the_steps(fake: FakeService) -> None:
    fake.next_result = make_result(steps=STEPS)
    result = run("ask", "q", "--trace")
    assert result.exit_code == 0
    assert "How this was answered (3 steps):" in result.stdout
    assert '2. [search] Searched the documents: "waiting period": 4 hits' in result.stdout
    plain = run("ask", "q")
    assert "How this was answered" not in plain.stdout


def test_repaired_problems_are_listed_as_fixed_not_failed(fake: FakeService) -> None:
    fake.next_result = make_result(evidence_report=REPAIRED)
    result = run("ask", "q", "--trace")
    assert result.exit_code == 0
    assert "Answered - evidence checks passed after one repair (1 fixed during repair)" in result.stdout
    assert "Fixed during repair:" in result.stdout
    assert "quote_verbatim c1: quote not found in excerpt" in result.stdout
    assert "[warning]" not in result.stdout and REPAIRED_PREFIX not in result.stdout
    assert "Evidence checks: passed (after repair); 0 issue(s); 1 fixed during repair" in result.stdout


def test_withheld_exits_3_and_help_says_so(fake: FakeService) -> None:
    fake.next_result = make_result(Outcome.WITHHELD, evidence_report=EvidenceReport(passed=False, repaired=True, issues=(
        CheckIssue(check="numbers_grounded", detail="5 is not in a cited excerpt"),
    )))
    assert run("ask", "q").exit_code == 3
    assert "3 when withheld" in " ".join(run("ask", "--help").stdout.split())


def test_bots_examples(fake: FakeService, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    summary = BotSummary(id=BOT, name="Demo", classification="internal", model="ollama:tiny", collections=(BOT,),
                         examples=("What is the waiting period?",))
    monkeypatch.setattr(fake, "list_bots", lambda principal: [summary])
    assert "example questions" not in run("bots").stdout
    listed = run("bots", "--examples")
    assert listed.exit_code == 0
    assert f'kestrel ask "What is the waiting period?" --bot {BOT}' in listed.stdout


def test_docs_list_shows_chunks(fake: FakeService, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    counted = DOCUMENT.model_copy(update={"chunk_count": 31, "table_count": 2})
    monkeypatch.setattr(fake, "list_documents", lambda bot_id, principal: [counted])
    result = run("docs", "list")
    assert result.exit_code == 0
    header = result.stdout.splitlines()[0]
    assert "Sections" in header and "Chunks" in header and "Tables" in header
    assert " 31 " in result.stdout


def test_docs_compare(fake: FakeService) -> None:
    older = DOCUMENT.model_copy(update={"id": "old", "edition": "2025", "status": DocumentStatus.SUPERSEDED,
                                        "effective_from": date(2025, 1, 1)})
    newer = DOCUMENT.model_copy(update={"effective_from": date(2026, 1, 1)})
    fake.comparison = EditionComparison(
        bot_id=BOT, document_id=DOC, family="guide", editions=(older, newer), older=older, newer=newer,
        changes=(SectionChange(change="changed", heading="Guide > 2.2 Maximum cover", older_text="up to R20 000 000",
                               newer_text="up to R25 000 000",
                               values=(ValueChange(before="R20 000 000", after="R25 000 000", context="up to"),)),
                 SectionChange(change="added", heading="Guide > 8. New rule", newer_text="New.")),
    )
    result = run("docs", "compare", DOC)
    assert result.exit_code == 0
    assert fake.calls[-1] == ("compare_editions", (BOT, DOC))
    assert "Values that changed:" in result.stdout
    assert "2.2 Maximum cover" in result.stdout and "R20 000 000" in result.stdout and "R25 000 000" in result.stdout
    assert "[added] Guide > 8. New rule" in result.stdout
    assert "2 section(s) differ; 1 value(s) changed." in result.stdout
    assert_console_safe(result)
    fake.comparison = EditionComparison(bot_id=BOT, document_id=DOC, editions=(newer,), note="no other edition")
    assert "no other edition" in run("docs", "compare", DOC).stdout


def test_audit_tail_shows_why_and_closest(fake: FakeService) -> None:
    entry = AUDIT.entry.model_copy(update={"outcome": Outcome.WITHHELD, "failed_checks": ("numbers_grounded",),
                                           "closest": "guide.pdf > section 4.2"})
    fake.audit_records = [AUDIT.model_copy(update={"entry": entry})]
    result = run("audit", "tail")
    assert result.exit_code == 0
    assert "Why" in result.stdout and "Closest section" in result.stdout
    assert "failed: numbers_grounded" in result.stdout and "guide.pdf > section 4.2" in result.stdout


def test_mcp_ask_result_has_only_a_step_count() -> None:
    output = ask_output(make_result(steps=STEPS))
    assert output.steps == 3
    assert "Searched the documents" not in output.model_dump_json()
