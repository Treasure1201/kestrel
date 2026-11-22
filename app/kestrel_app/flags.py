"""Answers a user flagged for review.

A local stand-in for a review queue (production would send flags to a ticketing or
review system): each flag is one JSON line in `results/flags/flags.jsonl`. It stores only what the audit log already holds about the
answer (trace id, screened question, outcome), never the raw question. No Streamlit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from kestrel.contracts import AskResult

FLAGS_FILE = Path("flags") / "flags.jsonl"


def flag_record(result: AskResult, user: str, note: str = "") -> dict[str, object]:
    """The JSON object written for one flag."""
    return {
        "flagged_at": datetime.now(UTC).isoformat(),
        "user": user,
        "trace_id": result.trace_id,
        "bot_id": result.bot_id,
        "outcome": result.outcome.value,
        "model": result.model,
        "question": result.question,
        "note": note.strip()[:500],
    }


def append_flag(results_root: Path, record: dict[str, object]) -> Path:
    """Append one flag; returns the file written."""
    path = results_root / FLAGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
