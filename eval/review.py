"""Human review files: one `review.yaml` per run folder, filled in by a person.

    uv run python -m eval.review <run folder> [--run 1] [--force]

The file lists each question with the answer, its sources and the automatic grade, and
empty `assessment` and `comment` fields. The person reads the answer and its quotes
(results.jsonl or results.csv) and writes one of the five workbook values. The
automatic grade is never copied into `assessment`. An existing review is never
overwritten unless `--force` is given.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

from kestrel.contracts import Assessment

from eval.artefacts import RESULTS_JSONL, REVIEW_YAML, read_jsonl, source_lines
from eval.grading import describe
from eval.workbook import answer_cell

ALLOWED = tuple(a.value for a in Assessment)

HEADER = """\
# Human review. For every question, set `assessment` to exactly one of:
#   {allowed}
# and add a short `comment` (why it failed / what was changed).
# `auto_grade` is an automatic string-matching check against the gold file. It is a
# screening signal, not an assessment: read the answer and its quotes and decide.
# Then run:  uv run python -m eval.finalise {folder}
"""


def build_review(folder: Path, run: int = 1) -> dict[str, object]:
    """The review document for one run of a run folder."""
    records = [r for r in read_jsonl(Path(folder) / RESULTS_JSONL) if r.run == run]
    if not records:
        raise ValueError(f"{folder}: no records for run {run}")
    return {
        "run_folder": Path(folder).name,
        "model": records[0].model,
        "run": run,
        "questions": [
            {
                "id": r.question_id,
                "question": r.question,
                "outcome": r.result.outcome.value,
                "answer": answer_cell(r.result),
                "sources": source_lines(r.result),
                "auto_grade": r.auto_grade.category if r.auto_grade else None,
                "auto_reasons": describe(r.auto_grade) if r.auto_grade else None,
                "assessment": "",
                "comment": "",
            }
            for r in records
        ],
    }


def create_review(folder: Path, run: int = 1, force: bool = False) -> Path:
    """Write `review.yaml` unless one exists (or `force`); return its path."""
    path = Path(folder) / REVIEW_YAML
    if path.exists() and not force:
        return path
    document = build_review(folder, run)
    text = HEADER.format(allowed=" | ".join(ALLOWED), folder=Path(folder).as_posix())
    text += yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(text, encoding="utf-8")
    return path


def load_review(path: Path) -> dict[str, object]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError(f"{path}: expected a mapping with a `questions` list")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.review", description="Create review.yaml in a run folder.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--run", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="overwrite an existing review.yaml")
    args = parser.parse_args(argv)
    existed = (args.folder / REVIEW_YAML).exists()
    try:
        path = create_review(args.folder, run=args.run, force=args.force)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"kept existing {path} (use --force to overwrite)" if existed and not args.force else f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
