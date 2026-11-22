"""Run the evaluation questions through `AssistantService` for one or more models.

    uv run python -m eval.run_eval [--bot kestrel-products] [--models provider:model ...]
        [--runs N] [--questions eval/KA_Test_Questions.xlsx] [--set test|reworded]
        [--out results/] [--ingest/--no-ingest] [--gold PATH] [--retrieval/--no-retrieval]

For each model a folder `results/<UTC timestamp>_<model-slug>/` receives
`results.jsonl` (one `EvalRecord` per question per run, appended as it is produced),
`results.csv`, `summary.json`, `review.yaml` (for the human review) and, for the test
set, a filled copy of the workbook per run, plus `retrieval.json` from the search-only
pass (`eval.retrieval_eval`, skipped with `--no-retrieval`). A failing question is recorded as an error
and the run continues. The template workbook is never written.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kestrel.contracts import AskResult, EvalRecord, Outcome, Principal
from kestrel.errors import ErrorCategory, KestrelError

from eval.artefacts import (
    RESULTS_CSV,
    RESULTS_JSONL,
    SUMMARY_JSON,
    format_summary,
    slug,
    summarise,
    write_csv,
    write_summary,
)
from eval.gold import GoldFile, gold_path_for, load_gold
from eval.grading import grade
from eval.retrieval_eval import evaluate_retrieval, format_retrieval, write_retrieval
from eval.review import create_review
from eval.workbook import DEFAULT_TEMPLATE, fill_workbook, question_texts

Echo = Callable[[str], None]


@dataclass(frozen=True)
class ModelChoice:
    """A model to evaluate: `label` is recorded, `override` is passed to `ask` (None: bot default)."""

    label: str
    override: str | None


def utc_now() -> datetime:
    return datetime.now(UTC)


def load_questions(set_name: str, gold: GoldFile, workbook: Path, echo: Echo = print) -> list[tuple[str, str]]:
    """The (id, question) pairs to ask. The test set reads the workbook; reworded reads its gold file."""
    if set_name == "reworded":
        return [(qid, entry.question) for qid, entry in gold.questions.items()]
    texts = question_texts(workbook)
    for qid, entry in gold.questions.items():
        if qid not in texts:
            echo(f"warning: {qid} is in the gold file but not in the workbook")
        elif " ".join(texts[qid].split()) != " ".join(entry.question.split()):
            echo(f"warning: {qid} question text differs between workbook and gold file")
    missing = [qid for qid in texts if qid not in gold.questions]
    if missing:
        echo(f"warning: no gold entry for {', '.join(missing)}; those answers are not graded")
    return list(texts.items())


def _error_result(bot_id: str, question: str, label: str, exc: BaseException) -> AskResult:
    if isinstance(exc, KestrelError):
        category, message = exc.category, exc.user_message
    else:
        category, message = ErrorCategory.INTERNAL, f"{type(exc).__name__} raised by the service: {str(exc)[:200]}"
    return AskResult(
        trace_id=f"eval-error-{uuid.uuid4().hex[:12]}",
        bot_id=bot_id,
        question=question,
        outcome=Outcome.ERROR,
        reason=message,
        model=label,
        error_category=category,
    )


def ask_one(
    service: Any,
    *,
    bot_id: str,
    question_id: str,
    question: str,
    model: ModelChoice,
    run: int,
    principal: Principal,
    gold: GoldFile,
    clock: Callable[[], datetime] = utc_now,
) -> EvalRecord:
    """Ask one question and grade it. Never raises for a service failure."""
    started = clock()
    try:
        result = service.ask(bot_id, question, principal, model=model.override)
        if not isinstance(result, AskResult):
            raise TypeError(f"ask returned {type(result).__name__}, not AskResult")
    except Exception as exc:  # noqa: BLE001 - a failing question must not abort the run
        result = _error_result(bot_id, question, model.label, exc)
    finished = clock()
    entry = gold.questions.get(question_id)
    auto = None
    if entry is not None:
        try:
            auto = grade(result, entry)
        except Exception as exc:  # noqa: BLE001 - keep the answer even if grading breaks
            print(f"warning: grading {question_id} failed: {exc}", file=sys.stderr)
    return EvalRecord(
        question_id=question_id,
        question=question,
        bot_id=bot_id,
        model=result.model or model.label,
        run=run,
        result=result,
        auto_grade=auto,
        started_at=started,
        finished_at=finished,
    )


def _folder(out_root: Path, stamp: str, label: str) -> Path:
    base = out_root / f"{stamp}_{slug(label)}"
    folder, n = base, 2
    while folder.exists():
        folder = base.with_name(f"{base.name}-{n}")
        n += 1
    folder.mkdir(parents=True)
    return folder


def workbook_name(label: str, run: int) -> str:
    suffix = "" if run == 1 else f"_run{run}"
    return f"KA_Test_Questions_{slug(label)}{suffix}.xlsx"


def run_evaluation(
    service: Any,
    *,
    bot_id: str,
    models: Sequence[ModelChoice],
    runs: int,
    questions: Sequence[tuple[str, str]],
    gold: GoldFile,
    gold_path: Path,
    principal: Principal,
    out_root: Path,
    set_name: str = "test",
    template: Path = DEFAULT_TEMPLATE,
    ingest: bool = False,
    clock: Callable[[], datetime] = utc_now,
    echo: Echo = print,
    retrieval: bool = False,
) -> list[Path]:
    """Ask every question for every model and run; write the artefacts; return the run folders.

    With `retrieval`, the search-only pass (`eval.retrieval_eval`) runs once and its
    `retrieval.json` is written into every run folder.
    """
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if ingest:
        try:
            report = service.ingest(principal, bot_id)
            changed = sum(1 for f in report.files if f.action != "unchanged")
            echo(f"ingest: {len(report.files)} files, {changed} changed")
            for warning in report.warnings:
                echo(f"ingest warning: {warning}")
        except Exception as exc:  # noqa: BLE001 - asking still records why each answer failed
            echo(f"warning: ingest failed: {getattr(exc, 'user_message', exc)}")
    stamp = clock().strftime("%Y%m%dT%H%M%SZ")
    found = None
    if retrieval:
        found = evaluate_retrieval(service, bot_id=bot_id, gold=gold, principal=principal, set_name=set_name)
        echo(format_retrieval(found))
    folders: list[Path] = []
    for model in models:
        folder = _folder(Path(out_root), stamp, model.label)
        started = clock()
        records: list[EvalRecord] = []
        with (folder / RESULTS_JSONL).open("w", encoding="utf-8", newline="\n") as jsonl:
            for run in range(1, runs + 1):
                for qid, question in questions:
                    record = ask_one(
                        service, bot_id=bot_id, question_id=qid, question=question, model=model,
                        run=run, principal=principal, gold=gold, clock=clock,
                    )
                    records.append(record)
                    jsonl.write(record.model_dump_json() + "\n")
                    jsonl.flush()
                    grade_text = record.auto_grade.category if record.auto_grade else "ungraded"
                    echo(f"[{model.label} run {run}] {qid}: {record.result.outcome.value} / auto {grade_text}")
        write_csv(folder / RESULTS_CSV, records)
        summary = summarise(records)
        summary["meta"] = {
            "bot": bot_id,
            "question_set": set_name,
            "model": model.label,
            "model_override": model.override,
            "gold_file": str(gold_path),
            "questions_file": str(template) if set_name == "test" else str(gold_path),
            "started_at": started.isoformat(),
            "finished_at": clock().isoformat(),
        }
        write_summary(folder / SUMMARY_JSON, summary)
        if set_name == "test":
            for run in range(1, runs + 1):
                by_id = {r.question_id: r for r in records if r.run == run}
                fill_workbook(template, folder / workbook_name(model.label, run), by_id)
        create_review(folder, run=1)
        if found is not None:
            write_retrieval(folder, found)
        echo(format_summary(summary, f"{model.label} ({folder.name})"))
        folders.append(folder)
    return folders


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m eval.run_eval", description=__doc__.split("\n\n")[0])
    parser.add_argument("--bot", default="kestrel-products")
    parser.add_argument("--models", nargs="+", metavar="PROVIDER:MODEL", help="default: the configured model")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--questions", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--set", dest="set_name", choices=("test", "reworded"), default="test")
    parser.add_argument("--gold", type=Path, default=None, help="default: the set's gold file")
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--ingest", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--retrieval", action=argparse.BooleanOptionalAction, default=True,
                        help="also run the search-only pass and write retrieval.json (default: on)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, service_factory: Callable[[Any], Any] | None = None) -> int:
    """Command-line entry point. `service_factory(settings)` is injectable for tests."""
    args = _parse(argv)
    if args.runs < 1:
        print("error: --runs must be at least 1", file=sys.stderr)
        return 2
    for model in args.models or ():
        if ":" not in model:
            print(f"error: model {model!r} must be written provider:model", file=sys.stderr)
            return 2
    try:
        from kestrel.config import Settings

        settings = Settings()
        gold_path = args.gold or gold_path_for(args.set_name)
        gold = load_gold(gold_path)
        if service_factory is None:
            try:
                from kestrel.service import build_service  # imported lazily: tests inject a fake
            except ImportError as exc:
                print(f"error: the assistant service is not available ({exc})", file=sys.stderr)
                return 2
            service_factory = build_service
        service = service_factory(settings)
        questions = load_questions(args.set_name, gold, args.questions)
    except KestrelError as exc:
        print(f"error: {exc.user_message}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    models = [ModelChoice(m, m) for m in args.models] if args.models else [ModelChoice(settings.model, None)]
    principal = settings.principal("eval")
    folders = run_evaluation(
        service, bot_id=args.bot, models=models, runs=args.runs, questions=questions, gold=gold,
        gold_path=gold_path, principal=principal, out_root=args.out, set_name=args.set_name,
        template=args.questions, ingest=args.ingest, retrieval=args.retrieval,
    )
    for folder in folders:
        print(f"wrote {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
