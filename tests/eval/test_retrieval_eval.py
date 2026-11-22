"""Search-only evaluation: rank of the expected section, hit@1/3/6, MRR, and where the results are written."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from eval_helpers import FakeService, good_answers

from kestrel.config import Settings
from kestrel.contracts import DocumentStatus, Principal, SearchHit, SearchResult

from eval import retrieval_eval
from eval.gold import DEFAULT_GOLD, load_gold
from eval.report import build_report
from eval.retrieval_eval import RETRIEVAL_JSON, evaluate_retrieval, first_match_rank, format_retrieval, summarise_ranks
from eval.run_eval import ModelChoice, load_questions, run_evaluation

GOLD = load_gold(DEFAULT_GOLD)
PRINCIPAL = Principal(user="tester", roles=frozenset({"broker"}), channel="eval")


def hit(filename: str, locator: str, rank: int) -> SearchHit:
    return SearchHit(chunk_id=f"c{rank}", document_id="d", section_id=f"d:s{rank:03d}", filename=filename, title="t",
                     locator=locator, status=DocumentStatus.CURRENT, kind="prose", text="x", score=1.0 / rank, rank=rank)


class SearchService(FakeService):
    """Puts each question's expected section at a chosen rank (None: not returned at all)."""

    def __init__(self, ranks: dict[str, int | None]) -> None:
        super().__init__(good_answers())
        self.ranks = ranks
        self.searched: list[tuple[str, str, int]] = []

    def search(self, bot_id: str, query: str, principal: Principal, limit: int = 8) -> SearchResult:
        self.searched.append((bot_id, query, limit))
        qid = next(q for q, e in GOLD.questions.items() if e.question == query)
        hits = [hit("KA_Other.docx", f'section {n} "Unrelated"', n) for n in range(1, limit + 1)]
        rank = self.ranks.get(qid)
        if rank is not None:
            source = GOLD.questions[qid].sources[0]
            hits[rank - 1] = hit(source.document, f"{source.locator} (extra)", rank)
        return SearchResult(hits=tuple(hits))


def ranks_for_all(default: int | None) -> dict[str, int | None]:
    return {qid: default for qid, entry in GOLD.questions.items() if entry.sources}


def test_first_match_rank_uses_the_grader_matching() -> None:
    source = GOLD.questions["Q1"].sources[0]
    hits = [hit("KA_Other.docx", "section 1", 1), hit(source.document, f'{source.locator.upper()}', 2)]
    assert first_match_rank(hits, GOLD.questions["Q1"].sources) == 2  # case and quotes ignored, as in grading
    assert first_match_rank(hits[:1], GOLD.questions["Q1"].sources) is None


def test_hit_rates_and_mrr() -> None:
    rows = [
        {"has_target": True, "rank": 1}, {"has_target": True, "rank": 2}, {"has_target": True, "rank": 5},
        {"has_target": True, "rank": None}, {"has_target": False, "rank": None},
        {"has_target": True, "rank": None, "error": "boom"},
    ]
    summary = summarise_ranks(rows)
    assert summary["questions_with_target"] == 4 and summary["questions_without_target"] == 1 and summary["errors"] == 1
    assert summary["hits"] == {"1": 1, "3": 2, "6": 3}
    assert summary["hit_at"]["3"] == 0.5
    assert summary["mrr"] == pytest.approx((1 + 0.5 + 0.2) / 4)


def test_evaluate_searches_each_question_with_a_target_once_with_limit_10() -> None:
    ranks = ranks_for_all(1) | {"Q1": 3, "Q2": None}
    service = SearchService(ranks)
    data = evaluate_retrieval(service, bot_id="kestrel-products", gold=GOLD, principal=PRINCIPAL)
    targets = [qid for qid, e in GOLD.questions.items() if e.sources]
    assert len(service.searched) == len(targets) and {s[2] for s in service.searched} == {10}
    assert service.calls == []  # never asks a model
    by_id = {row["id"]: row for row in data["per_question"]}
    assert by_id["Q1"]["rank"] == 3 and by_id["Q2"]["rank"] is None
    assert not by_id["Q13"]["has_target"]  # dental cover: the documents do not say
    n = data["questions_with_target"]
    assert n == len(targets) and data["questions_without_target"] == len(GOLD.questions) - n
    assert data["hits"] == {"1": n - 2, "3": n - 1, "6": n - 1}
    assert "hit@1" in format_retrieval(data) and "MRR" in format_retrieval(data)


def test_a_failing_search_is_recorded_not_raised() -> None:
    service = FakeService(good_answers())  # has no search method at all
    data = evaluate_retrieval(service, bot_id="kestrel-products", gold=GOLD, principal=PRINCIPAL)
    assert data["errors"] == sum(1 for e in GOLD.questions.values() if e.sources)
    assert data["questions_with_target"] == 0 and data["mrr"] is None


def test_main_writes_retrieval_json(tmp_path: Path, settings: Settings) -> None:
    out = tmp_path / "run"
    code = retrieval_eval.main(["--out", str(out)], service_factory=lambda s: SearchService(ranks_for_all(2)))
    assert code == 0
    data = json.loads((out / RETRIEVAL_JSON).read_text(encoding="utf-8"))
    assert data["hits"]["1"] == 0 and data["hits"]["3"] == data["questions_with_target"]
    assert data["mrr"] == pytest.approx(0.5)


def test_run_eval_writes_retrieval_into_each_folder_and_the_report_shows_it(template: Path, tmp_path: Path) -> None:
    service = SearchService(ranks_for_all(1))
    folders = run_evaluation(
        service, bot_id="kestrel-products", models=[ModelChoice("a:one", None), ModelChoice("b:two", None)], runs=1,
        questions=load_questions("test", GOLD, template), gold=GOLD, gold_path=DEFAULT_GOLD, principal=PRINCIPAL,
        out_root=tmp_path, template=template, echo=lambda _: None, clock=lambda: datetime(2026, 11, 23, tzinfo=UTC),
        retrieval=True,
    )
    assert all((f / RETRIEVAL_JSON).is_file() for f in folders)
    report = build_report(folders)
    assert "## Retrieval (search only, no model, top 10)" in report
    n = json.loads((folders[0] / RETRIEVAL_JSON).read_text(encoding="utf-8"))["questions_with_target"]
    assert f"| {folders[0].name} | {n} | {n}/{n} (100%) | {n}/{n} (100%) | {n}/{n} (100%) | 1.000 |" in report


def test_run_eval_main_can_skip_retrieval(tmp_path: Path, settings: Settings) -> None:
    from eval import run_eval

    service = SearchService(ranks_for_all(1))
    assert run_eval.main(["--out", str(tmp_path), "--no-ingest", "--no-retrieval"], service_factory=lambda s: service) == 0
    assert service.searched == []
    assert run_eval.main(["--out", str(tmp_path / "with"), "--no-ingest"], service_factory=lambda s: service) == 0
    assert service.searched and all((f / RETRIEVAL_JSON).is_file() for f in (tmp_path / "with").iterdir())
