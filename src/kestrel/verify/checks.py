"""The status contract and the seven evidence checks (DESIGN.md section 6).

Pure functions: they read the screened question, the model's `AgentAnswer`, the
evidence handed to the model in this request, the scope decision and the calculations
the `calculate` tool performed, and return `CheckIssue`s. Nothing here calls a model
or a database. Every issue's `detail` names the label, quote or number at fault and
what to do about it, because it is sent back to the model in the repair turn.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from kestrel.agent.calculator import Calculation, CalculationError, evaluate, format_decimal
from kestrel.contracts import AgentAnswer, CheckIssue, DocumentStatus, Evidence, EvidenceReport, ScopeDecision
from kestrel.verify.numbers import contains_number, numbers_in, parse_number, percent_rates
from kestrel.verify.quotes import quote_found

UNIT_CONSTANTS = frozenset({Decimal(1), Decimal(12), Decimal(100)})
"""Operands allowed without a source: one, months in a year, and the percentage base."""

_DECLINES = {"not_found", "refused"}
_QUOTE_PREVIEW = 120


def clean_label(label: str) -> str:
    """`[C3]`, ` c3 ` and `c3` are the same label."""
    return label.strip().strip("[]()").strip().lower()


def _preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _QUOTE_PREVIEW else text[: _QUOTE_PREVIEW - 3] + "..."


def _show(value: Decimal) -> str:
    return format_decimal(value)


def _evidence_numbers(evidence: Evidence) -> set[Decimal]:
    """Numbers an excerpt supports: its text plus where it comes from (title, locator, edition, file name)."""
    hit = evidence.hit
    context = " ".join([hit.title, hit.locator, hit.edition or "", hit.filename.replace("_", " ")])
    return numbers_in(evidence.text) | numbers_in(context, words=False)


def _operand_support(evidence: Evidence) -> set[Decimal]:
    return _evidence_numbers(evidence) | percent_rates(evidence.text)


@dataclass
class _Context:
    question: str
    answer: AgentAnswer
    evidence: Mapping[str, Evidence]
    scope: ScopeDecision | None
    tool_calculations: Sequence[Calculation]
    cited: list[tuple[str, Evidence]] = field(default_factory=list)
    verified: set[Decimal] = field(default_factory=set)

    @property
    def declined(self) -> bool:
        return self.answer.status in _DECLINES


def check_status_contract(ctx: _Context) -> list[CheckIssue]:
    """`answered` and `partial` need an answer and at least one citation."""
    issues: list[CheckIssue] = []
    if ctx.answer.status in {"answered", "partial"}:
        if not ctx.answer.answer.strip():
            issues.append(CheckIssue(check="schema", detail="The answer text is empty. Write the answer in plain sentences."))
        if not ctx.answer.citations:
            issues.append(CheckIssue(
                check="schema",
                detail=f"Status {ctx.answer.status} needs at least one citation: an excerpt label, a quote copied "
                "word for word from it, and the claim it supports. If the excerpts do not support an answer, use not_found.",
            ))
    return issues


def check_labels(ctx: _Context) -> list[CheckIssue]:
    """Check 1: every cited label was retrieved in this request."""
    issues = []
    known = ", ".join(list(ctx.evidence)[:30]) or "none"
    for citation in ctx.answer.citations:
        label = clean_label(citation.label)
        evidence = ctx.evidence.get(label)
        if evidence is None:
            issues.append(CheckIssue(
                check="label_retrieved",
                label=label,
                detail=f"Citation {label!r} was not shown to you in this request. Use only labels from search_documents "
                f"or get_table results (available: {known}).",
            ))
        else:
            ctx.cited.append((label, evidence))
    return issues


def check_quotes(ctx: _Context) -> list[CheckIssue]:
    """Check 2: every quote is verbatim in the excerpt, or the single row, it cites."""
    issues = []
    for citation in ctx.answer.citations:
        label = clean_label(citation.label)
        evidence = ctx.evidence.get(label)
        if evidence is None:
            continue
        if quote_found(citation.quote, evidence.text, row=evidence.row is not None):
            continue
        elsewhere = [
            other for other, ev in ctx.evidence.items()
            if other != label and quote_found(citation.quote, ev.text, row=ev.row is not None)
        ]
        hint = f" It does appear in {elsewhere[0]}; cite that label instead." if elsewhere else (
            " Copy the exact words from that excerpt (an ellipsis may skip words); do not paraphrase."
        )
        where = "table row" if evidence.row is not None else "excerpt"
        issues.append(CheckIssue(
            check="quote_verbatim",
            label=label,
            detail=f"The quote for {label} is not word for word in that {where}: \"{_preview(citation.quote)}\".{hint}",
        ))
    return issues


def check_table_rows(ctx: _Context) -> list[CheckIssue]:
    """Check 3: a citation into a table names a row, not the whole table."""
    issues = []
    for label, evidence in ctx.cited:
        if evidence.hit.kind == "table" and evidence.row is None:
            issues.append(CheckIssue(
                check="table_row_cited",
                label=label,
                detail=f"{label} is a whole table. Call get_table with {label!r} and cite the row you relied on "
                f"(for example {label}.r1), quoting from that row.",
            ))
    return issues


def _calculation_support(ctx: _Context) -> set[Decimal]:
    support = numbers_in(ctx.question) | percent_rates(ctx.question) | set(UNIT_CONSTANTS)
    sources: Iterable[Evidence] = ctx.evidence.values() if ctx.declined else (ev for _, ev in ctx.cited)
    for evidence in sources:
        support |= _operand_support(evidence)
    return support


def _traced(calculation: Calculation, support: set[Decimal]) -> list[Decimal]:
    return [operand for operand in calculation.operands if not contains_number(support, operand.normalize())]


def _accept(ctx: _Context, calculation: Calculation) -> None:
    ctx.verified |= {calculation.value.normalize(), calculation.exact.normalize()}
    ctx.verified |= {step.normalize() for step in calculation.steps}


def check_calculations(ctx: _Context) -> list[CheckIssue]:
    """Check 5: every reported calculation is recomputed, its operands traced and its rule label retrieved."""
    issues: list[CheckIssue] = []
    support = _calculation_support(ctx)
    for calculation in ctx.tool_calculations:
        # The tool's own results count as verified when their operands trace, so later steps can use them.
        if not _traced(calculation, support | ctx.verified):
            _accept(ctx, calculation)
    for reported in ctx.answer.calculations:
        expression = reported.expression
        try:
            computed = evaluate(expression)
        except CalculationError as exc:
            issues.append(CheckIssue(check="calculation_recomputed", detail=f"The calculation {expression!r} cannot be evaluated: {exc}"))
            continue
        # The tool prints `expression = result`; a result copied with its expression is read after the last `=`.
        claimed = parse_number(reported.result.rsplit("=", 1)[-1])
        if claimed is None or not (
            contains_number({computed.value, computed.exact}, claimed)
        ):
            issues.append(CheckIssue(
                check="calculation_recomputed",
                detail=f"{expression} is {computed.text}, not {reported.result!r}. Use the calculate tool and report its result exactly.",
            ))
            continue
        missing = _traced(computed, support | ctx.verified)
        if missing:
            shown = ", ".join(_show(value) for value in missing)
            issues.append(CheckIssue(
                check="calculation_recomputed",
                detail=f"In {expression}, the number(s) {shown} do not appear in a cited excerpt, the question or an "
                "earlier calculation. Cite the excerpt each operand comes from.",
            ))
        rule = clean_label(reported.rule or "")
        if not rule:
            issues.append(CheckIssue(check="calculation_recomputed", detail=f"The calculation {expression} does not name the excerpt that states its rule; set rule to that label."))
        elif rule not in ctx.evidence:
            issues.append(CheckIssue(
                check="calculation_recomputed", label=rule,
                detail=f"The rule label {rule!r} for {expression} was not retrieved in this request; use the label of the excerpt stating the formula.",
            ))
        if not missing:
            _accept(ctx, computed)
    return issues


def check_numbers(ctx: _Context) -> list[CheckIssue]:
    """Check 4: every number in the answer and caveats traces to a cited excerpt, the question or a calculation."""
    issues: list[CheckIssue] = []
    # A rate written as 75% also supports 0.75 in the answer, as in "0.75 x R40 000".
    support = numbers_in(ctx.question) | percent_rates(ctx.question) | ctx.verified
    uncited: dict[str, set[Decimal]] = {}
    cited_labels = {label for label, _ in ctx.cited}
    for label, evidence in ctx.evidence.items():
        if label in cited_labels or ctx.declined:
            support |= _operand_support(evidence)
        else:
            uncited[label] = _operand_support(evidence)
    texts = [("answer", ctx.answer.answer)] + [("caveats", caveat) for caveat in ctx.answer.caveats]
    reported: set[Decimal] = set()
    for where, text in texts:
        for number in sorted(numbers_in(text)):
            if number in reported or contains_number(support, number):
                continue
            reported.add(number)
            sources = [label for label, values in uncited.items() if contains_number(values, number)]
            if sources:
                detail = (f"The number {_show(number)} in the {where} comes from {sources[0]}, which is not cited. "
                          f"Add a citation to {sources[0]} quoting the words that contain it.")
            else:
                detail = (f"The number {_show(number)} in the {where} does not appear in any cited excerpt, the question "
                          "or a calculation. Remove it, cite where it comes from, or compute it with the calculate tool.")
            issues.append(CheckIssue(check="numbers_grounded", detail=detail))
    return issues


def check_decline(ctx: _Context) -> list[CheckIssue]:
    """Check 6: a decline carries a reason and does not follow hidden arithmetic. A decline needs no citation."""
    if not ctx.declined:
        return []
    issues = []
    if not (ctx.answer.reason or "").strip():
        who = "and who to ask" if ctx.answer.status == "refused" else "worded as what the documents do not say"
        issues.append(CheckIssue(check="decline_reason", detail=f"Status {ctx.answer.status} needs a reason, {who}."))
    if ctx.tool_calculations or ctx.answer.calculations:
        done = ", ".join(c.expression for c in ctx.tool_calculations) or ", ".join(c.expression for c in ctx.answer.calculations)
        issues.append(CheckIssue(
            check="decline_reason",
            detail=f"You calculated ({_preview(done)}) but then declined. If the excerpts support an answer, give it with "
            "citations and the calculation; if they do not, explain in the reason why the calculation does not answer the question.",
        ))
    return issues


def check_source_status(ctx: _Context) -> list[CheckIssue]:
    """Check 7: no citation to a superseded or blocked document unless the question named that edition."""
    issues = []
    explicit = bool(ctx.scope and ctx.scope.explicit)
    for label, evidence in ctx.cited:
        status = evidence.hit.status
        if status in {DocumentStatus.BLOCKED, DocumentStatus.PENDING} or (status == DocumentStatus.SUPERSEDED and not explicit):
            issues.append(CheckIssue(
                check="source_status",
                label=label,
                detail=f"{label} comes from a {status.value} document ({evidence.hit.title}). Cite current documents only "
                "unless the question asks for that edition.",
            ))
    return issues


def run_checks(
    question: str,
    answer: AgentAnswer,
    evidence: Mapping[str, Evidence],
    scope: ScopeDecision | None,
    tool_calculations: Sequence[Calculation] = (),
) -> list[CheckIssue]:
    """Run the status contract and all seven checks; return every issue found (empty when all pass)."""
    ctx = _Context(question=question, answer=answer, evidence=evidence, scope=scope, tool_calculations=tool_calculations)
    issues = check_status_contract(ctx)
    issues += check_labels(ctx)
    issues += check_quotes(ctx)
    issues += check_table_rows(ctx)
    issues += check_calculations(ctx)
    issues += check_numbers(ctx)
    issues += check_decline(ctx)
    issues += check_source_status(ctx)
    return issues


def evidence_report(issues: Sequence[CheckIssue], *, repaired: bool) -> EvidenceReport:
    """Summarise issues: the checks pass when no issue has severity `error`."""
    return EvidenceReport(passed=not any(i.severity == "error" for i in issues), issues=tuple(issues), repaired=repaired)


def repair_message(issues: Sequence[CheckIssue]) -> str:
    """The message sent to the model in the repair turn."""
    lines = [f"- [{issue.check}] {issue.detail}" for issue in issues if issue.severity == "error"]
    return (
        "The application checked your answer against the excerpts and found these problems:\n"
        + "\n".join(lines)
        + "\nFix them and give your final answer again with the answer tool. Use only labels and words from the "
        "excerpts you were shown; removing an unsupported detail is often the right fix. If the excerpts do not "
        "support an answer, use status not_found with a reason. Do not mention these checks or the earlier draft."
    )
