"""Write a completed human review into the submission workbook and the CSV.

    uv run python -m eval.finalise <run folder> [--review PATH] [--questions TEMPLATE]

Refuses to finalise while any assessment is empty or not one of the five workbook
values. Writes `KA_Test_Questions_SUBMISSION.xlsx` (columns C and D from the run,
E the human assessment, F the human comment, or the labelled automatic comment when
the person left it blank), verifies it against the template, and rewrites
`results.csv` with the human assessment beside the automatic grade.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from kestrel.contracts import Assessment, WorkbookLayout

from eval.artefacts import RESULTS_CSV, RESULTS_JSONL, REVIEW_YAML, SUBMISSION_XLSX, SUMMARY_JSON, read_jsonl, write_csv
from eval.review import ALLOWED, load_review
from eval.workbook import DEFAULT_TEMPLATE, fill_workbook

_BY_LOWER = {a.value.casefold(): a for a in Assessment}


class FinaliseError(ValueError):
    """The review is incomplete or invalid; nothing was written."""


def validate_review(review: dict[str, object], expected_ids: Sequence[str]) -> dict[str, tuple[Assessment, str]]:
    """Return id -> (assessment, comment), or raise FinaliseError listing every problem."""
    problems: list[str] = []
    out: dict[str, tuple[Assessment, str]] = {}
    items = review.get("questions")
    assert isinstance(items, list)
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            problems.append(f"entry without an id: {item!r}")
            continue
        qid = str(item["id"])
        raw = str(item.get("assessment") or "").strip()
        if not raw:
            problems.append(f"{qid}: assessment is empty")
            continue
        value = _BY_LOWER.get(" ".join(raw.split()).casefold())
        if value is None:
            problems.append(f"{qid}: {raw!r} is not one of {', '.join(ALLOWED)}")
            continue
        out[qid] = (value, str(item.get("comment") or ""))
    for qid in expected_ids:
        if qid not in out and not any(p.startswith(f"{qid}:") for p in problems):
            problems.append(f"{qid}: missing from the review")
    if problems:
        raise FinaliseError("review is not complete: " + "; ".join(problems))
    return out


def _is_workbook_set(folder: Path, ids: Sequence[str]) -> bool:
    summary = folder / SUMMARY_JSON
    if summary.exists():
        meta = json.loads(summary.read_text(encoding="utf-8")).get("meta", {})
        if "question_set" in meta:
            return meta["question_set"] == "test"
    return all(1 <= int(q[1:]) <= WorkbookLayout.QUESTION_COUNT for q in ids)


def finalise(folder: Path, review_path: Path | None = None, template: Path = DEFAULT_TEMPLATE) -> Path | None:
    """Validate the review and write the outputs; returns the submission workbook path (None for non-workbook sets)."""
    folder = Path(folder)
    review = load_review(review_path or folder / REVIEW_YAML)
    run = int(review.get("run") or 1)
    records = read_jsonl(folder / RESULTS_JSONL)
    in_run = [r for r in records if r.run == run]
    ids = [r.question_id for r in in_run]
    assessments = validate_review(review, ids)

    submission = None
    if _is_workbook_set(folder, ids):
        submission = fill_workbook(template, folder / SUBMISSION_XLSX, {r.question_id: r for r in in_run}, assessments)
    human = {(run, qid): {"assessment": a.value, "comment": c} for qid, (a, c) in assessments.items()}
    write_csv(folder / RESULTS_CSV, records, human)

    summary_path = folder / SUMMARY_JSON
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        counts = Counter(a.value for a, _ in assessments.values())
        summary["human_review"] = {"run": run, "assessments": {v: counts.get(v, 0) for v in ALLOWED}}
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return submission


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.finalise", description="Finalise a reviewed run folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--review", type=Path, default=None)
    parser.add_argument("--questions", type=Path, default=DEFAULT_TEMPLATE, help="the template workbook")
    args = parser.parse_args(argv)
    try:
        path = finalise(args.folder, args.review, args.questions)
    except FinaliseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}" if path else "updated results.csv (no workbook for this question set)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
