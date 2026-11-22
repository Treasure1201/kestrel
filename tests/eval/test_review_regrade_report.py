"""Finalising a human review, offline re-grading and the cross-run report."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
import pytest
import yaml
from eval_helpers import FakeService, good_answers, make_result

from kestrel.contracts import Principal

from eval.artefacts import read_jsonl, write_jsonl
from eval.finalise import FinaliseError, finalise
from eval.finalise import main as finalise_main
from eval.gold import DEFAULT_GOLD, load_gold
from eval.regrade import regrade_folder
from eval.report import build_report
from eval.run_eval import ModelChoice, load_questions, run_evaluation
from eval.workbook import verify_workbook

GOLD = load_gold(DEFAULT_GOLD)
PRINCIPAL = Principal(user="tester", roles=frozenset({"broker"}), channel="eval")


def make_run(service: FakeService, out: Path, template: Path, label: str = "fake:model") -> Path:
    (folder,) = run_evaluation(
        service, bot_id="kestrel-products", models=[ModelChoice(label, None)], runs=1,
        questions=load_questions("test", GOLD, template), gold=GOLD, gold_path=DEFAULT_GOLD,
        principal=PRINCIPAL, out_root=out, template=template, echo=lambda _: None,
        clock=lambda: datetime(2026, 11, 23, 12, 0, tzinfo=UTC),
    )
    return folder


def fill_review(folder: Path, values: dict[str, str]) -> None:
    path = folder / "review.yaml"
    review = yaml.safe_load(path.read_text(encoding="utf-8"))
    for item in review["questions"]:
        item["assessment"] = values.get(item["id"], "")
        item["comment"] = f"checked {item['id']}" if item["id"] in values else ""
    path.write_text(yaml.safe_dump(review, sort_keys=False), encoding="utf-8")


def all_values(default: str = "Correct") -> dict[str, str]:
    values = {f"Q{n}": default for n in range(1, 16)}
    values["Q13"] = values["Q14"] = "Correctly declined"
    return values


def test_finalise_refuses_incomplete_review(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    folder = make_run(fake_service, tmp_path, template)
    values = all_values()
    del values["Q4"]
    fill_review(folder, values)
    with pytest.raises(FinaliseError, match="Q4: assessment is empty"):
        finalise(folder, template=template)
    assert not (folder / "KA_Test_Questions_SUBMISSION.xlsx").exists()
    assert finalise_main([str(folder), "--questions", str(template)]) == 1


def test_finalise_refuses_invalid_value(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    folder = make_run(fake_service, tmp_path, template)
    values = all_values()
    values["Q2"] = "Mostly right"
    fill_review(folder, values)
    with pytest.raises(FinaliseError, match="Q2: 'Mostly right' is not one of"):
        finalise(folder, template=template)


def test_finalise_writes_valid_values(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    before = hashlib.sha256(template.read_bytes()).hexdigest()
    folder = make_run(fake_service, tmp_path, template)
    values = all_values()
    values["Q6"] = "partially correct"  # case is normalised to the dropdown spelling
    fill_review(folder, values)
    path = finalise(folder, template=template)
    assert path is not None and path.name == "KA_Test_Questions_SUBMISSION.xlsx"
    expected = {q: ("Partially correct" if q == "Q6" else v) for q, v in values.items()}
    assert verify_workbook(template, path, expected) == []
    ws = openpyxl.load_workbook(path)["Test Questions"]
    assert ws["E12"].value == "Partially correct"
    assert ws["F12"].value == "checked Q6"
    assert ws["E7"].value == "Correct"
    with (folder / "results.csv").open(encoding="utf-8-sig") as handle:
        rows = {r["id"]: r for r in csv.DictReader(handle)}
    assert rows["Q6"]["human_assessment"] == "Partially correct"
    assert rows["Q6"]["auto_grade"] == "correct"  # both kept, separately
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["human_review"]["assessments"]["Correct"] == 12
    assert hashlib.sha256(template.read_bytes()).hexdigest() == before


def test_review_is_never_overwritten(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    from eval.review import create_review

    folder = make_run(fake_service, tmp_path, template)
    fill_review(folder, all_values())
    create_review(folder)
    assert "Correct" in (folder / "review.yaml").read_text(encoding="utf-8")


def test_regrade_reproduces_grades(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    folder = make_run(fake_service, tmp_path, template)
    original = read_jsonl(folder / "results.jsonl")
    path, changes = regrade_folder(folder)
    assert changes == []
    regraded = read_jsonl(path)
    assert [r.auto_grade for r in regraded] == [r.auto_grade for r in original]
    assert [r.result for r in regraded] == [r.result for r in original]


def test_regrade_detects_change_without_calling_a_model(template: Path, tmp_path: Path) -> None:
    answers = good_answers()
    service = FakeService(answers)
    folder = make_run(service, tmp_path, template)
    records = read_jsonl(folder / "results.jsonl")
    # Pretend the saved grade for Q1 came from an older, laxer grader.
    stale = records[0].auto_grade.model_copy(update={"category": "incorrect"})  # type: ignore[union-attr]
    records[0] = records[0].model_copy(update={"auto_grade": stale})
    write_jsonl(folder / "results.jsonl", records)
    calls_before = len(service.calls)
    _, changes = regrade_folder(folder, in_place=True)
    assert changes == ["run 1 Q1: incorrect -> correct"]
    assert len(service.calls) == calls_before
    assert read_jsonl(folder / "results.jsonl")[0].auto_grade.category == "correct"  # type: ignore[union-attr]


def test_report_totals_match_records(template: Path, tmp_path: Path) -> None:
    good = make_run(FakeService(good_answers()), tmp_path / "a", template, "model:good")
    answers = good_answers()
    answers[GOLD.questions["Q1"].question] = make_result("The maximum is R20 000 000.", cost=0.5, latency_ms=4000)
    answers[GOLD.questions["Q5"].question] = make_result("", outcome="withheld", evidence_passed=False)
    worse = make_run(FakeService(answers), tmp_path / "b", template, "model:worse")
    text = build_report([good, worse])

    rows = [line for line in text.splitlines() if line.startswith("| 2026")]
    assert len(rows) == 2
    cells = [c.strip() for c in rows[1].strip("|").split("|")]
    records = read_jsonl(worse / "results.jsonl")
    counts = {g: sum(1 for r in records if r.auto_grade.category == g) for g in ("correct", "incorrect", "withheld")}  # type: ignore[union-attr]
    assert rows[1].startswith("| 20261123T120000Z_model-worse")
    assert cells[1] == "fake:model"  # the model the service reports it actually used
    assert cells[3] == "15"
    assert int(cells[4]) == counts["correct"] == 11
    assert int(cells[6]) == counts["incorrect"] == 1
    assert int(cells[9]) == counts["withheld"] == 1
    total_cost = sum(r.result.usage.cost_usd or 0 for r in records)
    assert float(cells[12]) == pytest.approx(total_cost / 15, abs=1e-4)
    assert cells[13] == "1000"
    assert cells[14] == "not reviewed"
    # Neither canned answer retrieved anything, so both are also marked as retrieval misses.
    assert "| Q1 | correct | incorrect* |" in text
    assert "| Q5 | correct | withheld* |" in text
    assert cells[11] == "2"
