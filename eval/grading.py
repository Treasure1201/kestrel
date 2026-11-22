"""Automatic grading of one answer against its gold entry.

The grade is a screening signal produced by plain string matching. It is never copied
into the workbook's human assessment column. Rules, in order:

1. outcome `error` -> `error`; outcome `withheld` -> `withheld` (no workbook value);
2. gold `not_found`:
   - a decline (`not_found` or `refused`) -> `correctly_declined`, unless the decline
     states a forbidden conclusion, which makes it `incorrect`;
   - any answer (`answered` or `partial`) -> `incorrect`;
3. gold `answered`:
   - a decline -> `incorrectly_declined`;
   - a forbidden statement, or a citation of a document whose status the question did
     not allow (for example the superseded edition) -> `incorrect`;
   - every required fact missing -> `incorrect`;
   - some facts missing, a condition dropped, the expected section not cited, or the
     model itself reporting `partial` -> `partially_correct`;
   - otherwise -> `correct`.

Facts and conditions are looked for in the answer and its caveats (what the user
reads). Forbidden statements are looked for in the answer, caveats and, for declines,
the reason. `expected_section_retrieved` is False when no retrieved excerpt came from
an expected source section: a retrieval miss, reported beside the grade and counted
separately in summaries. It is None when it cannot be judged (an error, a request for
clarification with no search, or a question with no expected source).
"""

from __future__ import annotations

from kestrel.contracts import AskResult, Assessment, AutoGrade, EvalRecord, Outcome

from eval.gold import ExpectedSource, Forbidden, GoldEntry, Requirement
from eval.normalise import clauses, contains_any, locator_key, normalise

GRADER_VERSION = "1"

_ASSESSMENT = {
    "correct": Assessment.CORRECT,
    "partially_correct": Assessment.PARTIALLY_CORRECT,
    "incorrect": Assessment.INCORRECT,
    "correctly_declined": Assessment.CORRECTLY_DECLINED,
    "incorrectly_declined": Assessment.INCORRECTLY_DECLINED,
}
_DECLINES = {Outcome.NOT_FOUND, Outcome.REFUSED}
_ANSWERS = {Outcome.ANSWERED, Outcome.PARTIAL}


def _source_matches(sources: tuple[ExpectedSource, ...], filename: str, locator: str) -> bool:
    name, place = locator_key(filename), locator_key(locator)
    return any(locator_key(s.document) in name and locator_key(s.locator) in place for s in sources)


def _missing(text: str, requirements: tuple[Requirement, ...]) -> tuple[str, ...]:
    return tuple(r.label for r in requirements if not contains_any(text, r.any))


def forbidden_found(raw_text: str, forbidden: tuple[Forbidden, ...]) -> tuple[str, ...]:
    """Labels of forbidden statements present in some clause without an excusing marker."""
    found: list[str] = []
    parts = clauses(raw_text)
    for item in forbidden:
        for clause in parts:
            if contains_any(clause, item.any) and not (item.unless_clause_has and contains_any(clause, item.unless_clause_has)):
                found.append(item.label)
                break
    return tuple(found)


def _retrieved(result: AskResult, gold: GoldEntry) -> bool | None:
    if not gold.sources or result.outcome == Outcome.ERROR or result.clarification is not None:
        return None
    return any(_source_matches(gold.sources, e.hit.filename, e.hit.locator) for e in result.retrieved)


def _cited(result: AskResult, gold: GoldEntry) -> bool:
    return any(_source_matches(gold.sources, c.filename, c.locator) for c in result.citations)


def _join(*parts: str | None) -> str:
    return "\n".join(p for p in parts if p)


def _make(category: str, notes: list[str], **fields: object) -> AutoGrade:
    return AutoGrade(
        category=category,  # type: ignore[arg-type]
        assessment=_ASSESSMENT.get(category),
        notes=" ".join(notes),
        **fields,  # type: ignore[arg-type]
    )


def grade(record: EvalRecord | AskResult, gold: GoldEntry) -> AutoGrade:
    """Grade one answer. Accepts an `EvalRecord` or the `AskResult` inside one."""
    result = record.result if isinstance(record, EvalRecord) else record
    retrieved = _retrieved(result, gold)
    notes: list[str] = []
    if retrieved is False:
        notes.append("retrieval_miss: no retrieved excerpt came from the expected section.")

    if result.outcome == Outcome.ERROR:
        category = result.error_category.value if result.error_category else "unknown"
        return _make("error", [f"Request failed ({category})."], expected_section_retrieved=retrieved)
    if result.outcome == Outcome.WITHHELD:
        issues = result.evidence_report.issues if result.evidence_report else ()
        detail = "; ".join(f"{i.check}: {i.detail}" for i in issues[:5])
        notes.insert(0, "Withheld by the evidence checks." + (f" {detail}" if detail else ""))
        return _make("withheld", notes, expected_section_retrieved=retrieved)

    answer_text = _join(result.answer, *result.caveats)
    if gold.status == "not_found":
        return _grade_unanswerable(result, gold, notes, retrieved)

    if result.outcome in _DECLINES:
        why = "clarification requested" if result.clarification else f"declined as {result.outcome.value}"
        notes.insert(0, f"Answerable question {why}.")
        return _make("incorrectly_declined", notes, expected_section_retrieved=retrieved)

    normalised = normalise(answer_text)
    missing = _missing(normalised, gold.must_include)
    dropped = _missing(normalised, gold.conditions)
    forbidden = list(forbidden_found(answer_text, gold.must_not_include))
    allowed = set(gold.allowed_statuses)
    for citation in result.citations:
        if citation.status.value not in allowed:
            forbidden.append(f"cited a {citation.status.value} document ({citation.filename})")
    cited = _cited(result, gold)

    if missing:
        notes.append("Missing facts: " + "; ".join(missing) + ".")
    if dropped:
        notes.append("Dropped conditions: " + "; ".join(dropped) + ".")
    if forbidden:
        notes.append("Forbidden: " + "; ".join(forbidden) + ".")
    notes.append("Expected section cited." if cited else "Expected section not cited.")
    if result.outcome == Outcome.PARTIAL:
        notes.append("Model reported a partial answer.")

    if forbidden or len(missing) == len(gold.must_include):
        category = "incorrect"
    elif missing or dropped or not cited or result.outcome == Outcome.PARTIAL:
        category = "partially_correct"
    else:
        category = "correct"
    return _make(
        category,
        notes,
        missing_facts=missing,
        dropped_conditions=dropped,
        forbidden_found=tuple(forbidden),
        expected_section_retrieved=retrieved,
    )


def _grade_unanswerable(result: AskResult, gold: GoldEntry, notes: list[str], retrieved: bool | None) -> AutoGrade:
    text = _join(result.answer, result.reason, *result.caveats)
    forbidden = forbidden_found(text, gold.must_not_include)
    if forbidden:
        notes.append("Forbidden: " + "; ".join(forbidden) + ".")
    if result.outcome in _ANSWERS:
        notes.insert(0, "The documents do not answer this question, but an answer was given.")
        return _make("incorrect", notes, forbidden_found=forbidden, expected_section_retrieved=retrieved)
    if forbidden:
        notes.insert(0, "Declined, but stated an unsupported conclusion.")
        return _make("incorrect", notes, forbidden_found=forbidden, expected_section_retrieved=retrieved)
    if result.outcome == Outcome.REFUSED:
        notes.insert(0, "Declined as refused (outside purpose) rather than not_found.")
    if not (result.reason or "").strip():
        notes.append("No decline reason given.")
    return _make("correctly_declined", notes, expected_section_retrieved=retrieved)


def describe(grade_: AutoGrade) -> str:
    """One-line text for CSV and workbook comments, always marked as automatic."""
    label = grade_.category.replace("_", " ")
    return f"Auto-grade: {label} (automatic check against the gold file, not a human assessment). {grade_.notes}".strip()
