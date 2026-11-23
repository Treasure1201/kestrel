"""The six items the service now supplies, as the pages show them (fake service)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app_fakes import BOT, FakeService, answered, audit, document
from test_metrics import eval_record
from test_pages import start, text

from kestrel.contracts import (
    REPAIRED_PREFIX,
    AnswerStep,
    CheckIssue,
    DocumentStatus,
    EditionComparison,
    EvidenceReport,
    Outcome,
    SectionChange,
    ValueChange,
)

from kestrel_app import metrics

STEPS = (
    AnswerStep(kind="privacy", label="Checked the question for personal information", detail="none found"),
    AnswerStep(kind="scope", label="Chose the documents to search", detail="current documents only"),
    AnswerStep(kind="search", label="Searched the documents", detail='"waiting period": 6 hits'),
    AnswerStep(kind="checks", label="Evidence checks passed", detail="no check failed"),
)


def test_example_questions_are_buttons_that_ask(tmp_path: Path) -> None:
    bot = BOT.model_copy(update={"examples": ("What is the waiting period?", "Who may join?")})
    fake = FakeService(bots=(bot,))
    at = start(fake, tmp_path)
    assert not at.exception, at.exception
    button = next(b for b in at.button if b.label == "What is the waiting period?")
    button.click().run()
    assert not at.exception, at.exception
    assert fake.asked and fake.asked[0][1] == "What is the waiting period?"
    assert not any(b.label == "Who may join?" for b in at.button)  # offered only in an empty conversation


def test_how_this_was_answered_lists_the_service_steps(tmp_path: Path) -> None:
    at = start(FakeService(ask_result=answered().model_copy(update={"steps": STEPS})), tmp_path)
    at.chat_input[0].set_value("What is the waiting period?").run()
    shown = text(at)
    assert not at.exception, at.exception
    assert 'Searched the documents</div><div class="k-detail">"waiting period": 6 hits' in shown
    assert "Chose the documents to search" in shown and "current documents only" in shown
    assert "Audit record written" in shown and "trace trace-answered" in shown


def test_problems_fixed_by_repair_are_not_shown_as_failures(tmp_path: Path) -> None:
    report = EvidenceReport(passed=True, repaired=True, issues=(
        CheckIssue(check="quote_verbatim", severity="warning", detail=f"{REPAIRED_PREFIX}quote not found", label="c1"),
    ))
    at = start(FakeService(ask_result=answered().model_copy(update={"evidence_report": report})), tmp_path)
    at.chat_input[0].set_value("What is the waiting period?").run()
    shown = text(at)
    assert not at.exception, at.exception
    assert "Evidence checks passed after one repair" in shown
    assert "Fixed during repair" in shown and "Quote matches the source word for word (c1): quote not found" in shown
    assert "these evidence checks failed" not in shown and REPAIRED_PREFIX not in shown
    assert "check failed" not in shown  # the citation badge reads "quote matched"


def test_documents_table_has_chunks_and_history_shows_changed_values(tmp_path: Path) -> None:
    new = document("doc1").model_copy(update={"chunk_count": 23})
    old = document("doc0", DocumentStatus.SUPERSEDED, edition="2025", effective=date(2025, 1, 1))
    comparison = EditionComparison(
        bot_id=BOT.id, document_id="doc1", family="guide", editions=(old, new), older=old, newer=new,
        changes=(
            SectionChange(change="changed", heading="Guide > 5. Claims", older_text="within 12 months", newer_text="within 6 months",
                          values=(ValueChange(before="12 months", after="6 months", context="notified within"),)),
            SectionChange(change="added", heading="Guide > 8. New rule", newer_text="New."),
        ),
    )
    at = start(FakeService(documents=[new, old], comparison=comparison), tmp_path, page="views/documents.py")
    assert not at.exception, at.exception
    tables = [df.value for df in at.dataframe]
    assert "Chunks" in tables[0].columns and 23 in list(tables[0]["Chunks"])
    changed = next(t for t in tables if "Context" in t.columns)
    assert list(changed.columns) == ["Section", "2025", "2026", "Context"]
    assert changed.iloc[0].tolist() == ["5. Claims", "12 months", "6 months", "notified within"]
    shown = text(at)
    assert "Values that changed" in shown and "8. New rule (added)" in shown
    assert "in force from 2025-01-01; replaced by 2026 from 2026-01-01" in shown


def test_unanswered_table_shows_why_and_closest_section(tmp_path: Path) -> None:
    records = [
        audit(1, Outcome.NOT_FOUND, question="Is dental cover offered?").model_copy(),
        audit(2, Outcome.WITHHELD, question="How much is paid?"),
        audit(3, Outcome.NOT_FOUND, question="Old question?"),
    ]
    records[0] = records[0].model_copy(update={"entry": records[0].entry.model_copy(update={
        "reason": "The documents do not mention dental cover.", "closest": "faq.docx > section 2"})})
    records[1] = records[1].model_copy(update={"entry": records[1].entry.model_copy(update={
        "reason": "The evidence checks did not pass after one repair attempt.",
        "failed_checks": ("numbers_grounded",), "closest": "guide.docx > section 4"})})
    at = start(FakeService(audit_records=records), tmp_path, page="views/quality.py")
    assert not at.exception, at.exception
    table = next(df.value for df in at.dataframe if "Closest section" in df.value.columns)
    rows = {r["Question, as screened"]: r for r in table.to_dict("records")}
    assert rows["Is dental cover offered?"]["Why"] == "The documents do not mention dental cover."
    assert rows["Is dental cover offered?"]["Closest section"] == "faq.docx > section 2"
    assert rows["How much is paid?"]["Why"] == "failed checks: numbers grounded"
    assert rows["Old question?"]["Why"] == metrics.NOT_RECORDED and rows["Old question?"]["Closest section"] == "not recorded"


def test_unanswered_groups_by_question_with_a_count_and_latest_reason() -> None:
    first = audit(1, Outcome.NOT_FOUND, question="dental?")
    second = audit(2, Outcome.NOT_FOUND, question="dental?")
    second = second.model_copy(update={"entry": second.entry.model_copy(update={"reason": "Not in the guides.", "closest": "a > b"})})
    (row,) = metrics.unanswered_questions([first, second])
    assert (row.count, row.reason, row.closest) == (2, "Not in the guides.", "a > b")
    csv_text = metrics.unanswered_csv([row], with_closest=True)
    assert csv_text.splitlines()[0].endswith(",closest_section") and csv_text.splitlines()[1].endswith(",a > b")


def test_evaluation_tab_shows_three_retrieval_bars(tmp_path: Path) -> None:
    run = tmp_path / "20261120T000000Z_anthropic-claude-sonnet-5-5"
    run.mkdir()
    (run / "results.jsonl").write_text(eval_record("Q1", "correct").model_dump_json() + "\n", encoding="utf-8")
    (run / "retrieval.json").write_text(json.dumps({
        "questions_with_target": 13, "questions_without_target": 2, "search_limit": 10,
        "hits": {"1": 9, "3": 12, "6": 13}, "mrr": 0.82,
    }), encoding="utf-8")
    at = start(FakeService(), tmp_path, page="views/quality.py")
    assert not at.exception, at.exception
    bars = [str(p.proto.text) for p in at.get("progress")]
    assert bars == ["Hit@1: 9 of 13 (69%)", "Hit@3: 12 of 13 (92%)", "Hit@6: 13 of 13 (100%)"]
    assert "unanswerable questions have no target section" in text(at)
