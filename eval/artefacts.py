"""Run-folder artefacts: JSONL records, the CSV, the summary and the printed table.

Every number in a summary is computed from `EvalRecord`s, so the same function serves
the runner, the offline re-grade and the cross-run report.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from kestrel.contracts import AskResult, EvalRecord, Outcome

from eval.grading import GRADER_VERSION, describe

RESULTS_JSONL = "results.jsonl"
RESULTS_CSV = "results.csv"
SUMMARY_JSON = "summary.json"
REVIEW_YAML = "review.yaml"
SUBMISSION_XLSX = "KA_Test_Questions_SUBMISSION.xlsx"

GRADE_ORDER = (
    "correct",
    "partially_correct",
    "incorrect",
    "correctly_declined",
    "incorrectly_declined",
    "withheld",
    "error",
)

CSV_COLUMNS = (
    "run", "id", "question", "model", "outcome", "answer", "reason", "sources",
    "auto_grade", "auto_assessment", "auto_reasons", "expected_section_retrieved",
    "evidence_checks", "model_calls", "searches", "repairs", "tokens_in", "tokens_out",
    "cost_usd", "latency_ms", "error_category", "trace_id",
    "human_assessment", "human_comment",
)


def slug(model: str) -> str:
    """Folder-safe form of `<provider>:<model>`."""
    out = "".join(ch if ch.isalnum() else "-" for ch in model.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "model"


def source_lines(result: AskResult) -> list[str]:
    """`filename, locator` per cited section, in citation order, without duplicates."""
    seen: list[str] = []
    for citation in result.citations:
        line = f"tool result: {citation.title} {citation.locator}" if citation.is_tool else f"{citation.filename}, {citation.locator}"
        if line not in seen:
            seen.append(line)
    return seen


def evidence_summary(result: AskResult) -> str:
    """Short text for the evidence-check column."""
    report = result.evidence_report
    if report is None:
        return "not run"
    head = "passed" if report.passed else "failed"
    if report.repaired:
        head += " after repair"
    issues = "; ".join(f"{i.check}: {i.detail}" for i in report.issues)
    return f"{head}: {issues}" if issues else head


def write_jsonl(path: Path, records: Iterable[EvalRecord]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")


def read_jsonl(path: Path) -> list[EvalRecord]:
    """Read records; a malformed line raises ValueError naming the line number."""
    records: list[EvalRecord] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(EvalRecord.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"{path}: line {number} is not a valid EvalRecord: {exc}") from exc
    return records


def csv_row(record: EvalRecord, human: Mapping[str, str] | None = None) -> dict[str, object]:
    result, grade = record.result, record.auto_grade
    usage = result.usage
    human = human or {}
    return {
        "run": record.run,
        "id": record.question_id,
        "question": record.question,
        "model": record.model,
        "outcome": result.outcome.value,
        "answer": result.answer,
        "reason": result.reason or result.clarification or "",
        "sources": "\n".join(source_lines(result)),
        "auto_grade": grade.category if grade else "",
        "auto_assessment": grade.assessment.value if grade and grade.assessment else "",
        "auto_reasons": describe(grade) if grade else "",
        "expected_section_retrieved": "" if grade is None or grade.expected_section_retrieved is None else str(grade.expected_section_retrieved).lower(),
        "evidence_checks": evidence_summary(result),
        "model_calls": usage.model_calls,
        "searches": usage.searches,
        "repairs": usage.repairs,
        "tokens_in": usage.input_tokens,
        "tokens_out": usage.output_tokens,
        "cost_usd": "" if usage.cost_usd is None else f"{usage.cost_usd:.6f}",
        "latency_ms": result.latency_ms,
        "error_category": result.error_category.value if result.error_category else "",
        "trace_id": result.trace_id,
        "human_assessment": human.get("assessment", ""),
        "human_comment": human.get("comment", ""),
    }


def write_csv(path: Path, records: Iterable[EvalRecord], human: Mapping[tuple[int, str], Mapping[str, str]] | None = None) -> None:
    """Write the CSV. `human` maps (run, question id) to a reviewed assessment and comment."""
    human = human or {}
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow(csv_row(record, human.get((record.run, record.question_id))))


def percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile (the smallest value with at least `fraction` of values at or below it)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def summarise(records: list[EvalRecord]) -> dict[str, object]:
    """Counts, retrieval misses, latency, cost and tokens over `records`."""
    grades = Counter(r.auto_grade.category for r in records if r.auto_grade)
    latencies = [float(r.result.latency_ms) for r in records]
    costs = [r.result.usage.cost_usd for r in records]
    known = [c for c in costs if c is not None]
    misses = [r.question_id for r in records if r.auto_grade and r.auto_grade.expected_section_retrieved is False]
    models = sorted({r.model for r in records})
    return {
        "grader_version": GRADER_VERSION,
        "models": models,
        "runs": sorted({r.run for r in records}),
        "questions": len({r.question_id for r in records}),
        "records": len(records),
        "ungraded": sum(1 for r in records if r.auto_grade is None),
        "grades": {name: grades.get(name, 0) for name in GRADE_ORDER},
        "outcomes": dict(Counter(r.result.outcome.value for r in records)),
        "withheld": sum(1 for r in records if r.result.outcome == Outcome.WITHHELD),
        "errors": sum(1 for r in records if r.result.outcome == Outcome.ERROR),
        "retrieval_misses": len(misses),
        "retrieval_miss_ids": misses,
        "latency_ms": {
            "median": statistics.median(latencies) if latencies else None,
            "p95": percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
        "cost_usd": {
            "total": round(sum(known), 6) if known else None,
            "per_question": round(sum(known) / len(known), 6) if known else None,
            "unpriced_records": len(costs) - len(known),
        },
        "tokens": {
            "input": sum(r.result.usage.input_tokens for r in records),
            "output": sum(r.result.usage.output_tokens for r in records),
        },
        "model_calls": sum(r.result.usage.model_calls for r in records),
    }


def write_summary(path: Path, summary: Mapping[str, object]) -> None:
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def format_summary(summary: Mapping[str, object], title: str) -> str:
    """Plain-text table for the console. Labels the grades as automatic."""
    grades = summary["grades"]
    latency = summary["latency_ms"]
    cost = summary["cost_usd"]
    tokens = summary["tokens"]
    assert isinstance(grades, dict) and isinstance(latency, dict) and isinstance(cost, dict) and isinstance(tokens, dict)

    def fmt(value: object, unit: str = "") -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:,.0f}{unit}" if unit == " ms" else f"{value:.6f}{unit}"
        return f"{value}{unit}"

    rows = [(name.replace("_", " "), str(grades.get(name, 0))) for name in GRADE_ORDER]
    rows += [
        ("retrieval misses", str(summary["retrieval_misses"])),
        ("median latency", fmt(latency.get("median"), " ms")),
        ("p95 latency", fmt(latency.get("p95"), " ms")),
        ("total cost (USD)", fmt(cost.get("total"))),
        ("cost per question (USD)", fmt(cost.get("per_question"))),
        ("unpriced records", str(cost.get("unpriced_records"))),
        ("tokens in / out", f"{tokens.get('input')} / {tokens.get('output')}"),
        ("model calls", str(summary["model_calls"])),
    ]
    width = max(len(k) for k, _ in rows)
    lines = [title, f"automatic grades over {summary['records']} answers (not a human assessment)", "-" * (width + 12)]
    lines += [f"{k.ljust(width)}  {v}" for k, v in rows]
    return "\n".join(lines)
