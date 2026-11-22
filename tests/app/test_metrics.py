"""Unit tests for the app's pure modules: metrics, result-folder reading and flags."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app_fakes import NOW, BOT, answered, audit, citation, document, finding

from kestrel.contracts import AskResult, AutoGrade, DocumentStatus, EvalRecord, Outcome, Usage
from kestrel.errors import ErrorCategory

from kestrel_app import metrics, results
from kestrel_app.flags import append_flag, flag_record


def eval_record(qid: str, category: str | None, *, run: int = 1, model: str = "anthropic:claude-sonnet-5-5",
                retrieved: bool | None = True, cost: float | None = 0.002, latency: int = 2000,
                outcome: Outcome = Outcome.ANSWERED) -> EvalRecord:
    result = AskResult(trace_id=f"{qid}-{run}", bot_id=BOT.id, question="q", outcome=outcome, answer="a",
                       usage=Usage(model_calls=1, cost_usd=cost), latency_ms=latency, model=model)
    grade = AutoGrade(category=category, expected_section_retrieved=retrieved) if category else None
    return EvalRecord(question_id=qid, question=f"question {qid}", bot_id=BOT.id, model=model, run=run,
                      result=result, auto_grade=grade, started_at=NOW, finished_at=NOW)


# --------------------------------------------------------------------------- live usage


def test_live_usage_empty() -> None:
    usage = metrics.live_usage([])
    assert usage.questions == 0
    assert usage.median_latency_ms is None and usage.median_cost_usd is None
    assert usage.share(Outcome.ANSWERED) is None
    assert metrics.most_cited_sections([]) == []
    assert metrics.unanswered_questions([]) == []
    assert metrics.unanswered_csv([]).splitlines() == ["question_as_screened,outcome,reason,count,last_asked"]


def test_live_usage_counts_only_ask_rows() -> None:
    records = [
        audit(1, Outcome.ANSWERED, channel="ui", cost=0.01, latency=1000),
        audit(2, Outcome.ANSWERED, channel="cli", cost=0.03, latency=3000),
        audit(3, Outcome.NOT_FOUND, channel="ui", cost=None, latency=2000),
        audit(4, None, kind="block"),
    ]
    usage = metrics.live_usage(records)
    assert usage.questions == 3
    assert usage.count(Outcome.ANSWERED) == 2
    assert usage.by_channel == {"cli": 1, "ui": 2}
    assert usage.median_latency_ms == 2000
    assert usage.median_cost_usd == 0.02
    assert usage.unpriced == 1
    assert usage.share(Outcome.NOT_FOUND) == 1 / 3


def test_most_cited_counts_each_answer_once_per_section() -> None:
    a = citation("doc1:s001", "guide > section 1")
    b = citation("doc1:s002", "guide > section 2")
    records = [audit(1, citations=(a, a)), audit(2, citations=(a, b)), audit(3, citations=(b,)), audit(4, citations=(a,))]
    ranked = metrics.most_cited_sections(records, limit=5)
    assert [(c.section_id, c.count) for c in ranked] == [("doc1:s001", 3), ("doc1:s002", 2)]
    assert metrics.most_cited_sections(records, limit=1)[0].locator == "guide > section 1"


def test_unanswered_groups_by_question_outcome_and_reason() -> None:
    records = [
        audit(1, Outcome.NOT_FOUND, question="dental?", detail="not covered"),
        audit(2, Outcome.NOT_FOUND, question="dental?", detail="not covered"),
        audit(3, Outcome.WITHHELD, digest="d1"),
        audit(4, Outcome.WITHHELD, digest="d2"),
        audit(5, Outcome.ANSWERED, question="fine"),
    ]
    rows = metrics.unanswered_questions(records)
    assert rows[0].question == "dental?" and rows[0].count == 2 and rows[0].reason == "not covered"
    assert [r.question for r in rows[1:]] == [metrics.NOT_STORED, metrics.NOT_STORED]  # different digests stay apart
    csv_text = metrics.unanswered_csv(rows)
    assert "dental?,not_found,not covered,2," in csv_text


def test_unanswered_reason_falls_back_to_error_category() -> None:
    record = audit(1, Outcome.ERROR, question="q")
    record = record.model_copy(update={"entry": record.entry.model_copy(update={"error_category": ErrorCategory.BUDGET_EXCEEDED})})
    assert metrics.unanswered_questions([record])[0].reason == "budget exceeded"


# --------------------------------------------------------------------------- documents


def test_document_counts() -> None:
    docs = [
        document("a"),
        document("b", DocumentStatus.SUPERSEDED),
        document("c", DocumentStatus.BLOCKED),
        document("d", DocumentStatus.PENDING).model_copy(update={"filename": "d.pdf"}),
    ]
    counts = metrics.document_counts(docs, [finding()])
    assert (counts.indexed, counts.current, counts.superseded, counts.pending_or_blocked, counts.findings) == (4, 1, 1, 2, 1)
    assert counts.by_type == {"DOCX": 3, "PDF": 1}
    assert counts.findings_by_kind == {"hidden_text": 1}
    assert metrics.document_counts([], []) == metrics.DocumentCounts()


def test_edition_history() -> None:
    new = document("new", edition="2026", effective=date(2026, 1, 1))
    old = document("old", DocumentStatus.SUPERSEDED, edition="2025", effective=date(2025, 1, 1))
    other = document("x", family="other")
    assert [d.id for d in metrics.edition_history([new, other, old], new)] == ["old", "new"]
    assert metrics.edition_history([other], other) == []
    assert metrics.edition_history([new], new.model_copy(update={"family": None})) == []


# --------------------------------------------------------------------------- evaluation


def test_summarise_run_empty() -> None:
    summary = metrics.summarise_run("f", [])
    assert summary.answers == 0 and summary.hit_rate is None and summary.cost_per_question is None


def test_summarise_run() -> None:
    records = [
        eval_record("Q1", "correct", cost=0.002, latency=1000),
        eval_record("Q2", "partially_correct", cost=0.004, latency=3000),
        eval_record("Q3", "withheld", retrieved=False, cost=None, latency=2000, outcome=Outcome.WITHHELD),
        eval_record("Q4", "correctly_declined", retrieved=None),
        eval_record("Q5", None),
    ]
    s = metrics.summarise_run("folder", records)
    assert s.model == "anthropic:claude-sonnet-5-5"
    assert s.grades == {"correct": 1, "partially_correct": 1, "withheld": 1, "correctly_declined": 1, "ungraded": 1}
    assert s.withheld == 1
    assert (s.retrieval_hits, s.retrieval_targets) == (2, 3)
    assert s.unpriced == 1
    assert abs((s.cost_per_question or 0) - 0.0025) < 1e-9
    assert s.median_latency_ms == 2000


def test_grid_letters_and_order() -> None:
    records = [eval_record("Q10", "incorrect"), eval_record("Q2", "correct"), eval_record("Q2", "error", run=2)]
    assert metrics.all_question_ids([records]) == ["Q2", "Q10"]
    first = metrics.grid(records, 1)
    assert metrics.grade_letter(first["Q2"]) == "C"
    assert metrics.grade_letter(first["Q10"]) == "X"
    assert metrics.grade_letter(metrics.grid(records, 2)["Q2"]) == "E"
    assert metrics.grade_letter(None) == ""
    assert metrics.grade_letter(eval_record("Q1", None)) == "?"


# --------------------------------------------------------------------------- result folders and flags


def test_discover_runs(tmp_path: Path) -> None:
    assert results.discover_runs(tmp_path / "missing") == []
    older = tmp_path / "20261101T000000Z_m"
    newer = tmp_path / "20261102T000000Z_m"
    other = tmp_path / "20261101T000000Z_o"
    for folder, model in ((older, "p:m"), (newer, "p:m"), (other, "p:o")):
        folder.mkdir()
        (folder / "results.jsonl").write_text(eval_record("Q1", "correct", model=model).model_dump_json() + "\n", encoding="utf-8")
    (newer / "results.jsonl").write_text(
        (newer / "results.jsonl").read_text(encoding="utf-8") + "not json\n", encoding="utf-8"
    )
    (newer / "review.yaml").write_text(
        "run: 1\nquestions:\n  - id: Q1\n    assessment: Correct\n    comment: fine\n  - id: Q2\n    assessment: ''\n",
        encoding="utf-8",
    )
    (tmp_path / "flags").mkdir()
    folders = results.discover_runs(tmp_path)
    assert [f.name for f in folders] == [newer.name, other.name, older.name]
    assert folders[0].problem and "line 2" in folders[0].problem
    assert folders[0].reviews == {(1, "Q1"): results.HumanReview("Correct", "fine")}
    assert [f.name for f in results.latest_per_model(folders)] == [newer.name, other.name]


def test_flag_record_holds_only_screened_text(tmp_path: Path) -> None:
    record = flag_record(answered("What is the waiting period? [SA_ID]"), "local-user", note="  wrong  ")
    path = append_flag(tmp_path, record)
    append_flag(tmp_path, record)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    data = json.loads(lines[0])
    assert data["question"] == "What is the waiting period? [SA_ID]"
    assert data["note"] == "wrong" and data["outcome"] == "answered"
