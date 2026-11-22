"""Re-grade saved answers offline after the gold file or the grader changes.

    uv run python -m eval.regrade <run folder or results.jsonl> [--gold PATH] [--in-place]

No model is called: the saved `AskResult`s are graded again. By default the output is
written beside the originals as `results.regraded.jsonl`, `results.regraded.csv` and
`summary.regraded.json`, and every question whose automatic grade changed is listed.
`--in-place` replaces `results.jsonl`, `results.csv` and `summary.json` instead
(keeping the summary's run metadata and any human review counts).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from kestrel.contracts import EvalRecord

from eval.artefacts import RESULTS_CSV, RESULTS_JSONL, REVIEW_YAML, SUMMARY_JSON, read_jsonl, summarise, write_csv, write_jsonl, write_summary
from eval.gold import GoldFile, gold_path_for, load_gold
from eval.grading import GRADER_VERSION, grade


def regrade_records(records: Sequence[EvalRecord], gold: GoldFile) -> tuple[list[EvalRecord], list[str]]:
    """Return re-graded copies and a line per (run, question) whose grade changed."""
    out: list[EvalRecord] = []
    changes: list[str] = []
    for record in records:
        entry = gold.questions.get(record.question_id)
        new = grade(record, entry) if entry is not None else None
        old_cat = record.auto_grade.category if record.auto_grade else "ungraded"
        new_cat = new.category if new else "ungraded"
        if record.auto_grade != new:
            detail = "" if old_cat != new_cat else " (reasons changed)"
            changes.append(f"run {record.run} {record.question_id}: {old_cat} -> {new_cat}{detail}")
        out.append(record.model_copy(update={"auto_grade": new}))
    return out, changes


def _human(folder: Path) -> dict[tuple[int, str], dict[str, str]]:
    """Human columns from a completed review, so a re-graded CSV keeps them."""
    path = folder / REVIEW_YAML
    if not path.exists():
        return {}
    from eval.review import load_review

    review = load_review(path)
    run = int(review.get("run") or 1)
    out: dict[tuple[int, str], dict[str, str]] = {}
    for item in review.get("questions", []):  # type: ignore[union-attr]
        if isinstance(item, dict) and item.get("assessment"):
            out[(run, str(item["id"]))] = {"assessment": str(item["assessment"]), "comment": str(item.get("comment") or "")}
    return out


def _question_set(folder: Path) -> str:
    path = folder / SUMMARY_JSON
    if path.exists():
        return str(json.loads(path.read_text(encoding="utf-8")).get("meta", {}).get("question_set", "test"))
    return "test"


def regrade_folder(source: Path, gold_path: Path | None = None, in_place: bool = False) -> tuple[Path, list[str]]:
    """Re-grade one run folder (or one results.jsonl); return the written JSONL path and the changes."""
    source = Path(source)
    jsonl = source if source.is_file() else source / RESULTS_JSONL
    folder = jsonl.parent
    gold_path = gold_path or gold_path_for(_question_set(folder))
    records, changes = regrade_records(read_jsonl(jsonl), load_gold(gold_path))
    summary = summarise(records)
    old_summary_path = folder / SUMMARY_JSON
    if old_summary_path.exists():
        old = json.loads(old_summary_path.read_text(encoding="utf-8"))
        for key in ("meta", "human_review"):
            if key in old:
                summary[key] = old[key]
    summary["regrade"] = {"gold_file": str(gold_path), "grader_version": GRADER_VERSION, "changes": changes}
    if in_place:
        names = (RESULTS_JSONL, RESULTS_CSV, SUMMARY_JSON)
    else:
        names = ("results.regraded.jsonl", "results.regraded.csv", "summary.regraded.json")
    write_jsonl(folder / names[0], records)
    write_csv(folder / names[1], records, _human(folder))
    write_summary(folder / names[2], summary)
    return folder / names[0], changes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.regrade", description="Re-grade saved results offline.")
    parser.add_argument("source", type=Path, help="a run folder or its results.jsonl")
    parser.add_argument("--gold", type=Path, default=None)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args(argv)
    try:
        path, changes = regrade_folder(args.source, args.gold, args.in_place)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}")
    print("\n".join(changes) if changes else "no grade changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
