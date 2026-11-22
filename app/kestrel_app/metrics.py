"""The numbers the pages show, computed from audit records, documents and evaluation records.

Pure Python: no Streamlit and no service calls, so every function is unit-tested with
constructed records. Every function accepts empty input and returns zeros or `None`
rather than raising, so an empty installation renders empty states, never made-up numbers.
"""

from __future__ import annotations

import csv
import io
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath

from kestrel.contracts import AuditRecord, DocumentRecord, DocumentStatus, EvalRecord, Finding, Outcome

OUTCOME_ORDER: tuple[Outcome, ...] = (
    Outcome.ANSWERED,
    Outcome.PARTIAL,
    Outcome.NOT_FOUND,
    Outcome.WITHHELD,
    Outcome.REFUSED,
    Outcome.ERROR,
)
UNANSWERED: frozenset[Outcome] = frozenset({Outcome.NOT_FOUND, Outcome.REFUSED, Outcome.WITHHELD, Outcome.ERROR})
NOT_STORED = "[not stored]"


# --------------------------------------------------------------------------- live usage


def ask_records(records: Iterable[AuditRecord]) -> list[AuditRecord]:
    """Only the `ask` rows (block, unblock and checkpoint rows are not questions)."""
    return [r for r in records if r.entry.kind == "ask"]


@dataclass(frozen=True)
class LiveUsage:
    """Counts and medians over the `ask` rows of an audit tail."""

    questions: int = 0
    by_outcome: dict[str, int] = field(default_factory=dict)
    by_channel: dict[str, int] = field(default_factory=dict)
    median_latency_ms: float | None = None
    median_cost_usd: float | None = None
    unpriced: int = 0
    first: datetime | None = None
    last: datetime | None = None

    def count(self, outcome: Outcome) -> int:
        """Questions with this outcome."""
        return self.by_outcome.get(outcome.value, 0)

    def share(self, outcome: Outcome) -> float | None:
        """Fraction of questions with this outcome, or None when there are none."""
        return self.count(outcome) / self.questions if self.questions else None


def live_usage(records: Iterable[AuditRecord]) -> LiveUsage:
    """Summarise the questions in `records`."""
    asks = ask_records(records)
    if not asks:
        return LiveUsage()
    outcomes = Counter(r.entry.outcome.value if r.entry.outcome else "unknown" for r in asks)
    channels = Counter(r.entry.channel for r in asks)
    latencies = [float(r.entry.latency_ms) for r in asks]
    costs = [r.entry.cost_usd for r in asks if r.entry.cost_usd is not None]
    times = [r.created_at for r in asks]
    return LiveUsage(
        questions=len(asks),
        by_outcome=dict(outcomes),
        by_channel=dict(sorted(channels.items())),
        median_latency_ms=statistics.median(latencies),
        median_cost_usd=statistics.median(costs) if costs else None,
        unpriced=len(asks) - len(costs),
        first=min(times),
        last=max(times),
    )


@dataclass(frozen=True)
class CitedSection:
    """A section and how many answers cited it."""

    document_id: str
    section_id: str
    locator: str
    count: int


def most_cited_sections(records: Iterable[AuditRecord], limit: int = 8) -> list[CitedSection]:
    """Sections cited most often; each answer counts once per section."""
    counts: Counter[tuple[str, str]] = Counter()
    locators: dict[tuple[str, str], str] = {}
    for record in ask_records(records):
        seen: set[tuple[str, str]] = set()
        for citation in record.entry.citations:
            key = (citation.document_id, citation.section_id)
            locators.setdefault(key, citation.locator)
            if key not in seen:
                seen.add(key)
                counts[key] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], locators[item[0]]))
    return [CitedSection(doc, sec, locators[(doc, sec)], n) for (doc, sec), n in ranked[:limit]]


NOT_RECORDED = "not recorded (an older audit record)"
"""Shown for audit rows written before the reason and closest section were kept."""


@dataclass(frozen=True)
class UnansweredRow:
    """One question (as screened) that was not answered, with how often and why."""

    question: str
    outcome: str
    reason: str
    count: int
    last_asked: datetime
    closest: str = ""
    """`filename > locator` of the top-ranked passage retrieved, `nothing retrieved`, or `not recorded` for older rows."""


def _has_new_fields(record: AuditRecord) -> bool:
    entry = record.entry
    return bool(entry.reason or entry.failed_checks or entry.closest)


def _reason(record: AuditRecord) -> str:
    """Why: the stored reason, or the failed checks of a withheld answer, else the older fallbacks."""
    entry = record.entry
    if entry.outcome == Outcome.WITHHELD and entry.failed_checks:
        return "failed checks: " + ", ".join(c.replace("_", " ") for c in entry.failed_checks)
    if entry.reason:
        return entry.reason
    if entry.detail:
        return entry.detail
    if entry.error_category is not None:
        return entry.error_category.value.replace("_", " ")
    if not _has_new_fields(record) and entry.question_text is not None:
        return NOT_RECORDED
    return NOT_STORED if entry.question_text is None else ""


def unanswered_questions(records: Iterable[AuditRecord]) -> list[UnansweredRow]:
    """Questions that ended not found, refused, withheld or in error, most frequent first.

    Grouped by screened question and outcome; the reason and closest section come from
    the most recent record of the group. Questions whose text was not stored are grouped
    by digest and shown as `[not stored]`.
    """
    groups: dict[tuple[str, str], list[AuditRecord]] = {}
    for record in ask_records(records):
        entry = record.entry
        if entry.outcome not in UNANSWERED:
            continue
        identity = entry.question_text or entry.question_digest or entry.trace_id
        groups.setdefault((identity, entry.outcome.value), []).append(record)
    rows = []
    for (_, outcome), items in groups.items():
        latest = max(items, key=lambda r: (r.created_at, r.id))
        rows.append(UnansweredRow(
            question=latest.entry.question_text or NOT_STORED,
            outcome=outcome,
            reason=_reason(latest),
            count=len(items),
            last_asked=latest.created_at,
            closest=latest.entry.closest or ("nothing retrieved" if _has_new_fields(latest) else "not recorded"),
        ))
    return sorted(rows, key=lambda r: (-r.count, -r.last_asked.timestamp()))


def unanswered_csv(rows: Sequence[UnansweredRow], *, with_closest: bool = False) -> str:
    """CSV text of the unanswered-questions table, for content owners; optionally with the closest section."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["question_as_screened", "outcome", "reason", "count", "last_asked"] + (["closest_section"] if with_closest else []))
    for row in rows:
        values: list[object] = [row.question, row.outcome, row.reason, row.count, row.last_asked.isoformat()]
        writer.writerow(values + ([row.closest] if with_closest else []))
    return buffer.getvalue()


# --------------------------------------------------------------------------- documents


@dataclass(frozen=True)
class DocumentCounts:
    """The metric row of the documents page."""

    indexed: int = 0
    current: int = 0
    superseded: int = 0
    pending_or_blocked: int = 0
    findings: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    findings_by_kind: dict[str, int] = field(default_factory=dict)


def file_type(document: DocumentRecord) -> str:
    """Upper-case file extension, for example `PDF`, or the media type when there is none."""
    suffix = PurePath(document.filename).suffix.lstrip(".")
    return suffix.upper() if suffix else document.media_type


def document_counts(documents: Sequence[DocumentRecord], findings: Sequence[Finding]) -> DocumentCounts:
    """Counts by status, by file type and by finding kind."""
    statuses = Counter(d.status for d in documents)
    return DocumentCounts(
        indexed=len(documents),
        current=statuses.get(DocumentStatus.CURRENT, 0),
        superseded=statuses.get(DocumentStatus.SUPERSEDED, 0),
        pending_or_blocked=statuses.get(DocumentStatus.PENDING, 0) + statuses.get(DocumentStatus.BLOCKED, 0),
        findings=len(findings),
        by_type=dict(sorted(Counter(file_type(d) for d in documents).items())),
        findings_by_kind=dict(sorted(Counter(f.kind for f in findings).items())),
    )


def edition_history(documents: Sequence[DocumentRecord], document: DocumentRecord) -> list[DocumentRecord]:
    """Every edition in the same collection and family as `document`, oldest first.

    Empty when the document has no family or is the only edition.
    """
    if not document.family:
        return []
    family = [d for d in documents if d.family == document.family and d.collection == document.collection]
    if len(family) < 2:
        return []
    return sorted(family, key=lambda d: (d.effective_from is None, d.effective_from or datetime.min.date(), d.edition or ""))


# --------------------------------------------------------------------------- evaluation

GRADE_STYLE: dict[str, tuple[str, str]] = {
    "correct": ("C", "correct"),
    "partially_correct": ("P", "partially correct"),
    "incorrect": ("X", "incorrect"),
    "correctly_declined": ("D", "correctly declined"),
    "incorrectly_declined": ("N", "declined, but the documents answer it"),
    "withheld": ("W", "withheld by the evidence checks"),
    "retrieval_miss": ("M", "retrieval miss"),
    "error": ("E", "error"),
}
UNGRADED = ("?", "not graded")
GRADE_ORDER: tuple[str, ...] = tuple(GRADE_STYLE)


def grade_of(record: EvalRecord) -> str:
    """The automatic grade category, or `ungraded`."""
    return record.auto_grade.category if record.auto_grade else "ungraded"


def grade_letter(record: EvalRecord | None) -> str:
    """One letter for the per-question grid; empty when the question was not asked."""
    if record is None:
        return ""
    return GRADE_STYLE.get(grade_of(record), UNGRADED)[0]


def question_key(question_id: str) -> int:
    """Sort key for `Q<n>` ids."""
    digits = question_id.lstrip("Qq")
    return int(digits) if digits.isdigit() else 0


@dataclass(frozen=True)
class RunSummary:
    """One run folder summarised for the comparison table."""

    folder: str
    model: str
    runs: int = 0
    answers: int = 0
    grades: dict[str, int] = field(default_factory=dict)
    withheld: int = 0
    errors: int = 0
    retrieval_hits: int = 0
    retrieval_targets: int = 0
    cost_per_question: float | None = None
    unpriced: int = 0
    median_latency_ms: float | None = None

    @property
    def hit_rate(self) -> float | None:
        """Share of graded questions with a target section whose section was retrieved."""
        return self.retrieval_hits / self.retrieval_targets if self.retrieval_targets else None


def summarise_run(folder: str, records: Sequence[EvalRecord]) -> RunSummary:
    """Grade counts, withheld, retrieval hit rate, cost per question and median latency."""
    models = sorted({r.model for r in records})
    if not records:
        return RunSummary(folder=folder, model="")
    grades = Counter(grade_of(r) for r in records)
    targets = [r.auto_grade.expected_section_retrieved for r in records if r.auto_grade]
    known = [t for t in targets if t is not None]
    costs = [r.result.usage.cost_usd for r in records if r.result.usage.cost_usd is not None]
    return RunSummary(
        folder=folder,
        model=", ".join(models),
        runs=len({r.run for r in records}),
        answers=len(records),
        grades={name: grades.get(name, 0) for name in (*GRADE_ORDER, "ungraded") if grades.get(name, 0)},
        withheld=sum(1 for r in records if r.result.outcome == Outcome.WITHHELD),
        errors=sum(1 for r in records if r.result.outcome == Outcome.ERROR),
        retrieval_hits=sum(1 for t in known if t),
        retrieval_targets=len(known),
        cost_per_question=sum(costs) / len(costs) if costs else None,
        unpriced=len(records) - len(costs),
        median_latency_ms=statistics.median(float(r.result.latency_ms) for r in records),
    )


def grid(records: Sequence[EvalRecord], run: int) -> dict[str, EvalRecord]:
    """Question id to record for one run of one folder."""
    return {r.question_id: r for r in records if r.run == run}


def all_question_ids(record_sets: Iterable[Sequence[EvalRecord]]) -> list[str]:
    """Every question id across the given folders, in numeric order."""
    ids = {r.question_id for records in record_sets for r in records}
    return sorted(ids, key=question_key)
