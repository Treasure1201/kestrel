"""Retrieval-only evaluation: where does the expected section rank, with no model involved?

    uv run python -m eval.retrieval_eval [--bot kestrel-products] [--set test|reworded]
        [--gold PATH] [--out results/<folder>]

For each gold question with an expected section, `service.search(bot, question,
principal, limit=10)` is called and the rank of the first hit whose filename and
locator match the gold expectation is recorded, using the grader's matching. From the
ranks: hit@1, hit@3 and hit@6 (the share of questions whose section ranks at or above
the cut-off; the agent's own searches show 6 passages) and the mean reciprocal rank.
Questions the documents cannot answer have no target section and are not counted.

`retrieval.json` is written to the folder given by `--out` (by default a new
`results/<UTC timestamp>_retrieval/`). `run_eval` runs this pass too, unless
`--no-retrieval`, and writes the file into each run folder.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kestrel.contracts import Principal

from eval.gold import GoldFile, gold_path_for, load_gold
from eval.grading import source_matches

RETRIEVAL_JSON = "retrieval.json"
SEARCH_LIMIT = 10
CUTOFFS = (1, 3, 6)

Echo = Callable[[str], None]


def first_match_rank(hits: Sequence[Any], sources: Sequence[Any]) -> int | None:
    """1-based position of the first hit from an expected source, or None when none of the hits is."""
    for position, hit in enumerate(hits, start=1):
        if source_matches(tuple(sources), hit.filename, hit.locator):
            return position
    return None


def _question_row(service: Any, bot_id: str, qid: str, entry: Any, principal: Principal, limit: int) -> dict[str, Any]:
    row: dict[str, Any] = {"id": qid, "question": entry.question, "has_target": bool(entry.sources), "rank": None}
    if not entry.sources:
        return row
    row["expected"] = [{"document": s.document, "locator": s.locator} for s in entry.sources]
    try:
        result = service.search(bot_id, entry.question, principal, limit=limit)
    except Exception as exc:  # noqa: BLE001 - one failing search must not end the pass
        row["error"] = getattr(exc, "user_message", None) or f"{type(exc).__name__}: {str(exc)[:200]}"
        return row
    if result.clarification:
        row["clarification"] = result.clarification
    row["rank"] = first_match_rank(result.hits, entry.sources)
    row["top"] = [f"{h.filename} > {h.locator}" for h in result.hits[:3]]
    return row


def summarise_ranks(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """hit@k for each cut-off and MRR over the questions that have a target and were searched."""
    targets = [r for r in rows if r["has_target"] and "error" not in r]
    ranks = [r["rank"] for r in targets]
    n = len(ranks)
    hits = {str(k): sum(1 for rank in ranks if rank is not None and rank <= k) for k in CUTOFFS}
    return {
        "questions_with_target": n,
        "questions_without_target": sum(1 for r in rows if not r["has_target"]),
        "errors": sum(1 for r in rows if "error" in r),
        "hits": hits,
        "hit_at": {k: (v / n if n else None) for k, v in hits.items()},
        "mrr": (sum(1 / rank for rank in ranks if rank) / n) if n else None,
    }


def evaluate_retrieval(
    service: Any, *, bot_id: str, gold: GoldFile, principal: Principal, limit: int = SEARCH_LIMIT,
    set_name: str = "test",
) -> dict[str, Any]:
    """Search every gold question once and summarise where the expected section ranked."""
    rows = [_question_row(service, bot_id, qid, entry, principal, limit) for qid, entry in gold.questions.items()]
    return {
        "bot": bot_id,
        "question_set": set_name,
        "search_limit": limit,
        "created_at": datetime.now(UTC).isoformat(),
        **summarise_ranks(rows),
        "per_question": rows,
        "note": "Questions the documents cannot answer have no target section and are not counted.",
    }


def write_retrieval(folder: Path, data: dict[str, Any]) -> Path:
    """Write `retrieval.json` into `folder`."""
    path = Path(folder) / RETRIEVAL_JSON
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_retrieval(folder: Path) -> dict[str, Any] | None:
    """`retrieval.json` of a run folder, or None when absent or unreadable."""
    path = Path(folder) / RETRIEVAL_JSON
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def format_retrieval(data: dict[str, Any]) -> str:
    """One line: hit@1/3/6 and MRR with the number of questions they are over."""
    n = data.get("questions_with_target", 0)
    if not n:
        return "retrieval: no question with a target section was searched"
    hits = data.get("hits", {})
    parts = [f"hit@{k} {hits.get(str(k), 0)}/{n} ({hits.get(str(k), 0) / n:.0%})" for k in CUTOFFS]
    mrr = data.get("mrr")
    errors = f"; {data['errors']} search error(s)" if data.get("errors") else ""
    return "retrieval: " + ", ".join(parts) + (f", MRR {mrr:.3f}" if mrr is not None else "") + errors


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m eval.retrieval_eval", description=__doc__.split("\n\n")[0])
    parser.add_argument("--bot", default="kestrel-products")
    parser.add_argument("--set", dest="set_name", choices=("test", "reworded"), default="test")
    parser.add_argument("--gold", type=Path, default=None, help="default: the set's gold file")
    parser.add_argument("--out", type=Path, default=None, help="folder for retrieval.json (default: a new results/ folder)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, service_factory: Callable[[Any], Any] | None = None) -> int:
    """Command-line entry point. `service_factory(settings)` is injectable for tests."""
    args = _parse(argv)
    try:
        from kestrel.config import Settings

        settings = Settings()
        gold_path = args.gold or gold_path_for(args.set_name)
        gold = load_gold(gold_path)
        if service_factory is None:
            from kestrel.service import build_service  # imported lazily: tests inject a fake

            service_factory = build_service
        service = service_factory(settings)
    except Exception as exc:  # noqa: BLE001 - configuration problems end the command with a message
        print(f"error: {getattr(exc, 'user_message', exc)}", file=sys.stderr)
        return 2
    try:
        data = evaluate_retrieval(service, bot_id=args.bot, gold=gold, principal=settings.principal("eval"), set_name=args.set_name)
    finally:
        close = getattr(service, "close", None)
        if callable(close):
            close()
    folder = args.out or Path("results") / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_retrieval"
    folder.mkdir(parents=True, exist_ok=True)
    path = write_retrieval(folder, data)
    for row in data["per_question"]:
        if row["has_target"]:
            where = row.get("error") or (f"rank {row['rank']}" if row["rank"] else f"not in the top {data['search_limit']}")
            print(f"{row['id']}: {where}")
    print(format_retrieval(data))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
