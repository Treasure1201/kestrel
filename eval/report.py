"""Markdown comparison across run folders, computed only from the files in them.

    uv run python -m eval.report <run folder> [<run folder> ...] [--out report.md]

One row per run folder (normally one model): automatic grade counts, withheld, errors,
retrieval misses, cost per question and median latency, plus the human review counts
when the folder has a completed review. Then a per-question grid of automatic grades,
and the search-only hit@1/3/6 and MRR of each folder that holds a `retrieval.json`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from kestrel.contracts import EvalRecord

from eval.artefacts import GRADE_ORDER, RESULTS_JSONL, REVIEW_YAML, read_jsonl, summarise
from eval.retrieval_eval import CUTOFFS, SEARCH_LIMIT, read_retrieval
from eval.review import ALLOWED, load_review

SHORT = {
    "correct": "correct",
    "partially_correct": "partial",
    "incorrect": "incorrect",
    "correctly_declined": "declined ok",
    "incorrectly_declined": "declined wrongly",
    "withheld": "withheld",
    "error": "error",
}


def _human_counts(folder: Path) -> dict[str, int] | None:
    path = folder / REVIEW_YAML
    if not path.exists():
        return None
    items = load_review(path).get("questions", [])
    values = [str(i.get("assessment") or "").strip() for i in items if isinstance(i, dict)]  # type: ignore[union-attr]
    if not values or any(v not in ALLOWED for v in values):
        return None
    return {a: values.count(a) for a in ALLOWED}


def _qkey(qid: str) -> int:
    return int(qid[1:])


def _money(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.4f}"  # type: ignore[arg-type]


def build_report(folders: Sequence[Path]) -> str:
    """The Markdown report for `folders`."""
    loaded: list[tuple[Path, list[EvalRecord]]] = [(Path(f), read_jsonl(Path(f) / RESULTS_JSONL)) for f in folders]
    head = ["Run folder", "Model", "Runs", "Answers"] + [SHORT[g] for g in GRADE_ORDER] + [
        "Retrieval misses", "Cost per question (USD)", "Median latency (ms)", "Human review",
    ]
    lines = [
        "# Evaluation comparison",
        "",
        "Grades are automatic string-matching checks against the gold file, not human assessments.",
        "The human review column counts the assessments a person entered, where a review is complete.",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
    ]
    for folder, records in loaded:
        s = summarise(records)
        grades, cost, latency = s["grades"], s["cost_usd"], s["latency_ms"]
        assert isinstance(grades, dict) and isinstance(cost, dict) and isinstance(latency, dict)
        human = _human_counts(folder)
        human_text = ", ".join(f"{k}: {v}" for k, v in human.items()) if human else "not reviewed"
        median = latency.get("median")
        cells = [folder.name, ", ".join(s["models"]), str(len(s["runs"])), str(s["records"])]  # type: ignore[arg-type]
        cells += [str(grades[g]) for g in GRADE_ORDER]
        cells += [str(s["retrieval_misses"]), _money(cost.get("per_question")), "n/a" if median is None else f"{median:.0f}", human_text]
        unpriced = cost.get("unpriced_records")
        if unpriced:
            cells[-3] += f" ({unpriced} unpriced)"
        lines.append("| " + " | ".join(cells) + " |")

    ids = sorted({r.question_id for _, records in loaded for r in records}, key=_qkey)
    lines += ["", "## Per question (automatic grade per run)", "", "| Question | " + " | ".join(f.name for f, _ in loaded) + " |"]
    lines.append("|" + "---|" * (len(loaded) + 1))
    for qid in ids:
        row = [qid]
        for _, records in loaded:
            grades = [
                (SHORT[r.auto_grade.category] if r.auto_grade else "ungraded")
                + ("*" if r.auto_grade and r.auto_grade.expected_section_retrieved is False else "")
                for r in sorted(records, key=lambda r: r.run)
                if r.question_id == qid
            ]
            row.append(" / ".join(grades) or "-")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "`*` the expected section was never retrieved (retrieval miss)."]
    lines += _retrieval_lines([folder for folder, _ in loaded])
    return "\n".join(lines) + "\n"


def _share(hits: int, n: int) -> str:
    return f"{hits}/{n} ({hits / n:.0%})" if n else "n/a"


def _retrieval_lines(folders: Sequence[Path]) -> list[str]:
    """The search-only table, from each folder's `retrieval.json` (folders without one are skipped)."""
    found = [(folder, data) for folder in folders if (data := read_retrieval(folder)) is not None]
    if not found:
        return []
    lines = [
        "",
        f"## Retrieval (search only, no model, top {found[0][1].get('search_limit', SEARCH_LIMIT)})",
        "",
        "| Run folder | Questions with a target | " + " | ".join(f"Hit@{k}" for k in CUTOFFS) + " | MRR |",
        "|" + "---|" * (len(CUTOFFS) + 3),
    ]
    for folder, data in found:
        n = int(data.get("questions_with_target") or 0)
        hits = data.get("hits") or {}
        mrr = data.get("mrr")
        cells = [folder.name, str(n), *(_share(int(hits.get(str(k), 0)), n) for k in CUTOFFS), "n/a" if mrr is None else f"{mrr:.3f}"]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "Hit@k: share of questions whose expected section ranks k or better. Unanswerable questions have no target section."]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.report", description="Compare run folders in Markdown.")
    parser.add_argument("folders", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        text = build_report(args.folders)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
