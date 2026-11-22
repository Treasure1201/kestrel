"""The runner end to end with a fake service: artefacts, failures, workbook and template safety."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import openpyxl
import pytest
import yaml
from eval_helpers import FakeService, good_answers, make_result

from kestrel.config import Settings
from kestrel.contracts import Principal

from eval import run_eval
from eval.artefacts import CSV_COLUMNS, read_jsonl
from eval.gold import DEFAULT_GOLD, REWORDED, load_gold
from eval.run_eval import ModelChoice, load_questions, run_evaluation
from eval.workbook import verify_workbook

GOLD = load_gold(DEFAULT_GOLD)
PRINCIPAL = Principal(user="tester", roles=frozenset({"call-centre-agent"}), channel="eval")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 23, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=500)
        return self.now


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(service: FakeService, out: Path, template: Path, **kw: object) -> list[Path]:
    questions = load_questions("test", GOLD, template)
    params: dict[str, object] = dict(
        bot_id="kestrel-products", models=[ModelChoice("fake:model", None)], runs=1, questions=questions,
        gold=GOLD, gold_path=DEFAULT_GOLD, principal=PRINCIPAL, out_root=out, template=template,
        clock=Clock(), echo=lambda _: None,
    )
    params.update(kw)
    return run_evaluation(service, **params)  # type: ignore[arg-type]


def test_run_writes_every_artefact(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    before = sha(template)
    (folder,) = run(fake_service, tmp_path, template)
    assert folder.name == "20261123T100000Z_fake-model"
    names = {p.name for p in folder.iterdir()}
    assert {"results.jsonl", "results.csv", "summary.json", "review.yaml", "KA_Test_Questions_fake-model.xlsx"} <= names

    records = read_jsonl(folder / "results.jsonl")
    assert [r.question_id for r in records] == [f"Q{n}" for n in range(1, 16)]
    assert all(r.auto_grade is not None for r in records)
    assert {r.auto_grade.category for r in records} == {"correct", "correctly_declined"}  # type: ignore[union-attr]
    assert all(call[2].channel == "eval" for call in fake_service.calls)

    with (folder / "results.csv").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert rows[0]["auto_grade"] == "correct" and rows[0]["human_assessment"] == ""
    assert rows[0]["sources"] == 'KA_Group_Life_Product_Guide_2026.docx, section 2.2 "Maximum cover"'
    assert rows[0]["tokens_in"] == "1000" and rows[0]["model_calls"] == "3"

    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["grades"]["correct"] == 13 and summary["grades"]["correctly_declined"] == 2
    assert summary["cost_usd"]["total"] == pytest.approx(0.015)
    assert summary["latency_ms"]["median"] == 1000
    assert summary["meta"]["question_set"] == "test"
    assert sha(template) == before


def test_service_failure_is_recorded_and_run_continues(template: Path, tmp_path: Path) -> None:
    answers = good_answers()
    failing = GOLD.questions["Q3"].question
    service = FakeService(answers, raise_for=[failing])
    (folder,) = run(service, tmp_path, template)
    records = read_jsonl(folder / "results.jsonl")
    assert len(records) == 15
    q3 = next(r for r in records if r.question_id == "Q3")
    assert q3.result.outcome.value == "error"
    assert q3.result.error_category.value == "internal"  # type: ignore[union-attr]
    assert "RuntimeError" in (q3.result.reason or "")
    assert q3.auto_grade.category == "error"  # type: ignore[union-attr]
    assert json.loads((folder / "summary.json").read_text())["errors"] == 1
    wb = openpyxl.load_workbook(folder / "KA_Test_Questions_fake-model.xlsx")
    assert str(wb["Test Questions"]["C9"].value).startswith("[Error: internal]")


def test_workbook_copy_keeps_structure_and_leaves_assessment_empty(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    (folder,) = run(fake_service, tmp_path, template)
    copy = folder / "KA_Test_Questions_fake-model.xlsx"
    assert verify_workbook(template, copy) == []
    wb = openpyxl.load_workbook(copy)
    ws = wb["Test Questions"]
    validations = [(str(dv.sqref), dv.formula1) for dv in ws.data_validations.dataValidation]
    assert validations == [("E7:E21", '"Correct,Partially correct,Incorrect,Correctly declined,Incorrectly declined"')]
    assert wb["Summary"]["B5"].value == "=COUNTIF('Test Questions'!$E$7:$E$21,A5)"
    assert wb["Summary"]["B10"].value == "=15-SUM(B5:B9)"
    assert ws.column_dimensions["B"].width == 50
    assert all(ws[f"E{row}"].value in (None, "") for row in range(7, 22))
    assert ws["E6"].value == "Correct"  # the grey example row is untouched
    assert "R25 000 000" in ws["C7"].value
    assert ws["D7"].value == 'KA_Group_Life_Product_Guide_2026.docx, section 2.2 "Maximum cover"'
    assert ws["F7"].value.startswith("Auto-grade: correct")
    assert "Declined: The approved documents do not say" in ws["C19"].value


def test_answer_text_starting_with_equals_is_not_a_formula(template: Path, tmp_path: Path) -> None:
    answers = good_answers()
    answers[GOLD.questions["Q1"].question] = make_result("=SUM(A1:A2) is R25 000 000")
    (folder,) = run(FakeService(answers), tmp_path, template)
    ws = openpyxl.load_workbook(folder / "KA_Test_Questions_fake-model.xlsx")["Test Questions"]
    assert ws["C7"].data_type == "s"


def test_multiple_runs_and_models(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    folders = run(fake_service, tmp_path, template, runs=2, models=[ModelChoice("a:one", "a:one"), ModelChoice("b:two", "b:two")])
    assert len(folders) == 2
    assert {c[3] for c in fake_service.calls} == {"a:one", "b:two"}
    assert (folders[0] / "KA_Test_Questions_a-one_run2.xlsx").exists()
    assert len(read_jsonl(folders[0] / "results.jsonl")) == 30


def test_ingest_option_calls_service(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    run(fake_service, tmp_path, template, ingest=True)
    assert fake_service.ingested == 1


def test_reworded_set_has_no_workbook(fake_service: FakeService, tmp_path: Path, template: Path) -> None:
    gold = load_gold(REWORDED)
    questions = load_questions("reworded", gold, template)
    (folder,) = run(fake_service, tmp_path, template, questions=questions, gold=gold, gold_path=REWORDED, set_name="reworded")
    assert not list(folder.glob("*.xlsx"))
    assert len(read_jsonl(folder / "results.jsonl")) == len(gold.questions)


def test_review_file_has_empty_assessments(fake_service: FakeService, template: Path, tmp_path: Path) -> None:
    (folder,) = run(fake_service, tmp_path, template)
    review = yaml.safe_load((folder / "review.yaml").read_text(encoding="utf-8"))
    assert len(review["questions"]) == 15
    assert all(q["assessment"] == "" and q["comment"] == "" for q in review["questions"])
    assert review["questions"][0]["auto_grade"] == "correct"


def test_main_with_injected_factory(fake_service: FakeService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setenv("KESTREL_MODEL", "fake:model")
    code = run_eval.main(["--out", str(tmp_path), "--no-ingest"], service_factory=lambda s: fake_service)
    assert code == 0
    assert fake_service.calls and fake_service.calls[0][3] is None
    assert fake_service.ingested == 0
    (folder,) = list(tmp_path.iterdir())
    assert folder.name.endswith("_fake-model")


def test_main_rejects_bad_model_and_runs(tmp_path: Path) -> None:
    assert run_eval.main(["--models", "nocolon", "--out", str(tmp_path)], service_factory=lambda s: None) == 2
    assert run_eval.main(["--runs", "0", "--out", str(tmp_path)], service_factory=lambda s: None) == 2
