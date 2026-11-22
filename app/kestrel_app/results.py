"""Evaluation run folders under `results/`, read from the files a run writes.

A run folder holds `results.jsonl` (one `EvalRecord` per line), `results.csv`,
`summary.json` and, once a person has reviewed it, `review.yaml`. Only the JSONL file
and the review are read here: every number on the page is computed from the records.
No Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kestrel.contracts import EvalRecord

RESULTS_JSONL = "results.jsonl"
REVIEW_YAML = "review.yaml"
MAX_FILE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class HumanReview:
    """A person's assessment of one answer."""

    assessment: str
    comment: str = ""


@dataclass(frozen=True)
class RunFolder:
    """One evaluation run folder and what could be read from it."""

    name: str
    path: Path
    records: list[EvalRecord] = field(default_factory=list)
    reviews: dict[tuple[int, str], HumanReview] = field(default_factory=dict)
    problem: str | None = None
    """Why the folder could not be read in full, if it could not."""

    @property
    def model(self) -> str:
        """The model of the folder's records (normally one per folder)."""
        return ", ".join(sorted({r.model for r in self.records}))

    @property
    def runs(self) -> list[int]:
        """Run numbers present in the folder."""
        return sorted({r.run for r in self.records})


def read_records(path: Path) -> tuple[list[EvalRecord], str | None]:
    """Read a JSONL file; returns the valid records and a note about any invalid lines."""
    if path.stat().st_size > MAX_FILE_BYTES:
        return [], f"{path.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB and was not read"
    records: list[EvalRecord] = []
    bad: list[int] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(EvalRecord.model_validate_json(line))
        except ValueError:
            bad.append(number)
    note = f"{len(bad)} line(s) of {path.name} could not be read (first: line {bad[0]})" if bad else None
    return records, note


def read_review(path: Path) -> dict[tuple[int, str], HumanReview]:
    """Assessments entered in `review.yaml`, keyed by (run, question id); blanks are skipped."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return {}
    run = data.get("run", 1)
    run = run if isinstance(run, int) else 1
    out: dict[tuple[int, str], HumanReview] = {}
    for item in data["questions"]:
        if not isinstance(item, dict):
            continue
        assessment = str(item.get("assessment") or "").strip()
        if assessment and item.get("id"):
            out[(run, str(item["id"]))] = HumanReview(assessment, str(item.get("comment") or "").strip())
    return out


def discover_runs(root: Path) -> list[RunFolder]:
    """Every folder under `root` holding a `results.jsonl`, newest name first.

    Folder names start with a UTC timestamp, so name order is time order.
    """
    if not root.is_dir():
        return []
    folders: list[RunFolder] = []
    for path in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        jsonl = path / RESULTS_JSONL
        if not jsonl.is_file():
            continue
        try:
            records, note = read_records(jsonl)
        except OSError as exc:
            records, note = [], f"{jsonl.name} could not be opened ({type(exc).__name__})"
        folders.append(RunFolder(path.name, path, records, read_review(path / REVIEW_YAML), note))
    return folders


def latest_per_model(folders: list[RunFolder]) -> list[RunFolder]:
    """The newest folder for each model (folders must be newest first, as discovered)."""
    seen: set[str] = set()
    latest: list[RunFolder] = []
    for folder in folders:
        if folder.records and folder.model not in seen:
            seen.add(folder.model)
            latest.append(folder)
    return latest
