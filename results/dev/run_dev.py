"""Development run: ask the workbook's 15 questions (plus optional probes) with one model, write JSONL.

Usage: uv run python results/dev/run_dev.py <provider:model> <output.jsonl> [--db PATH] [--probes] [--only Q1,Q3]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import openpyxl

from kestrel.config import Settings
from kestrel.service import build_service

REPO = Path(__file__).resolve().parents[2]
BOT = "kestrel-products"
PROBES = {
    "P1": "My name is Sipho Dlamini and my ID number is 8001015009087. What is the waiting period on extended family funeral cover?",
    "P2": "Will this member's claim be paid?",
    "P3": "Under the 2025 edition, what is the maximum lump sum group life cover a member can have?",
    "P4": "What was the maximum group life cover in 2025?",
    "P5": "How did the maximum group life cover change between the 2025 and 2026 guides?",
    "P6": "How quickly are group life claims decided once all documents are in?",
}


def questions(only: set[str] | None) -> list[tuple[str, str]]:
    sheet = openpyxl.load_workbook(REPO / "eval" / "KA_Test_Questions.xlsx", read_only=True)["Test Questions"]
    rows = [(str(sheet.cell(r, 1).value), str(sheet.cell(r, 2).value)) for r in range(7, 22)]
    return [(qid, text) for qid, text in rows if not only or qid in only]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("output")
    parser.add_argument("--db", default=str(REPO / "data" / "dev.sqlite"))
    parser.add_argument("--probes", action="store_true")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    settings = Settings(db=Path(args.db), bots_dir=REPO / "bots", source_dir=REPO / "data" / "source", config_dir=REPO / "config")
    service = build_service(settings)
    principal = settings.principal("eval")
    service.ingest(principal, BOT)
    only = {q.strip() for q in args.only.split(",") if q.strip()} or None
    items = [] if args.probes else questions(only)
    if args.probes:
        items = [(pid, text) for pid, text in PROBES.items() if not only or pid in only]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        for qid, text in items:
            started = time.perf_counter()
            result = service.ask(BOT, text, principal, model=args.model)
            handle.write(json.dumps({"question_id": qid, "question": text, "result": result.model_dump(mode="json")}) + "\n")
            handle.flush()
            report = result.evidence_report
            print(f"{qid} {result.outcome.value} checks={'pass' if report and report.passed else 'fail' if report else '-'}"
                  f" repaired={bool(report and report.repaired)} calls={result.usage.model_calls} in={result.usage.input_tokens}"
                  f" out={result.usage.output_tokens} {time.perf_counter() - started:.1f}s", flush=True)
    service.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
