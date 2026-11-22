"""Builders for canned `AskResult`s and a fake `AssistantService` for evaluation tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from kestrel.contracts import (
    AskResult,
    CheckIssue,
    DocumentStatus,
    Evidence,
    EvidenceReport,
    IngestedFile,
    IngestReport,
    Outcome,
    Principal,
    ResolvedCitation,
    SearchHit,
    Usage,
)
from kestrel.errors import ErrorCategory

from eval.gold import DEFAULT_GOLD, load_gold

GL26 = "KA_Group_Life_Product_Guide_2026.docx"
GL25 = "KA_Group_Life_Product_Guide_2025.docx"
DI = "KA_Group_Disability_Income_Guide_2026.pdf"
FUN = "KA_Funeral_Benefit_Rules_2026.docx"
FAQ = "KA_Claims_FAQ.docx"
FCL = "KA_Free_Cover_Limits_2026.xlsx"
FCL_TABLE = 'sheet "Free Cover Limits 2026", cells A4:E10'

Source = tuple[str, str]


def citation(filename: str, locator: str, status: str = "current", quote: str = "quoted text") -> ResolvedCitation:
    return ResolvedCitation(
        label="c1", quote=quote, supports="the answer", filename=filename, title=filename,
        locator=locator, document_id="d" * 16, section_id="d" * 16 + ":s001", status=DocumentStatus(status),
    )


def evidence(filename: str, locator: str, status: str = "current", n: int = 1) -> Evidence:
    hit = SearchHit(
        chunk_id=f"{'d' * 16}:s{n:03d}:p00", document_id="d" * 16, section_id=f"{'d' * 16}:s{n:03d}",
        filename=filename, title=filename, locator=locator, status=DocumentStatus(status),
        kind="prose", text="excerpt", score=1.0, rank=n,
    )
    return Evidence(label=f"c{n}", hit=hit)


def make_result(
    answer: str = "",
    *,
    outcome: str = "answered",
    sources: Sequence[Source] = (),
    retrieved: Sequence[Source] | None = None,
    reason: str | None = None,
    caveats: Sequence[str] = (),
    status: str = "current",
    cost: float | None = 0.001,
    tokens: tuple[int, int] = (1000, 100),
    latency_ms: int = 1000,
    error_category: ErrorCategory | None = None,
    evidence_passed: bool = True,
    clarification: str | None = None,
    question: str = "q",
) -> AskResult:
    retrieved = list(sources) if retrieved is None else list(retrieved)
    report = None
    if outcome != "error":
        issues = () if evidence_passed else (CheckIssue(check="quote_verbatim", detail="quote not found"),)
        report = EvidenceReport(passed=evidence_passed, issues=issues)
    return AskResult(
        trace_id="t-1", bot_id="kestrel-products", question=question, outcome=Outcome(outcome),
        answer=answer, reason=reason, caveats=tuple(caveats), clarification=clarification,
        citations=tuple(citation(f, loc, status) for f, loc in sources),
        retrieved=tuple(evidence(f, loc, status, i + 1) for i, (f, loc) in enumerate(retrieved)),
        evidence_report=report, model="fake:model",
        usage=Usage(model_calls=3, searches=2, input_tokens=tokens[0], output_tokens=tokens[1], cost_usd=cost),
        latency_ms=latency_ms, error_category=error_category,
    )


GOOD: dict[str, tuple[str, Source | None, str]] = {
    "Q1": ("The maximum lump sum group life cover per member is R25 000 000, regardless of salary or multiple.",
           (GL26, 'section 2.2 "Maximum cover"'), "answered"),
    "Q2": ("A member who leaves before normal retirement age must apply within 60 days of leaving service. "
           "Cover is limited to the lower of the member's group cover and R5 000 000.",
           (GL26, 'section 4 "Continuation option"'), "answered"),
    "Q3": ("A scheme with 320 members is in band 4 (250 to 499 members); the group life free cover limit is R5 000 000.",
           (FCL, FCL_TABLE + ", row A8:E8"), "answered"),
    "Q4": ("A scheme with 1 200 members is in band 6 (1000 and above); the disability income free cover limit is R120 000 per month.",
           (FCL, FCL_TABLE + ", row A10:E10"), "answered"),
    "Q5": ("Option B has a waiting period of 6 months.", (DI, 'page 1, section 2 "Benefit options", table 1'), "answered"),
    "Q6": ("Option C pays 100% of the first R20 000 plus 75% of the balance: R20 000 + 75% of R30 000 = R42 500 per month, "
           "below the R300 000 maximum.", (DI, 'page 1, section 2 "Benefit options", table 1'), "answered"),
    "Q7": ("The benefit is paid until the earliest of the member's recovery, the member's death, or the member reaching age 65.",
           (DI, 'page 1, section 3 "Payment term"'), "answered"),
    "Q8": ("A child aged under 6 is covered for R7 500.", (FUN, 'section 2 "Cover amounts", table 1'), "answered"),
    "Q9": ("Yes. For extended family members a 6-month waiting period applies to death from natural causes. "
           "Death from accidental causes is covered immediately.", (FUN, 'section 4 "Waiting periods"'), "answered"),
    "Q10": ("Up to 8 extended family members can be added, and the maximum entry age is 85.",
            (FUN, 'section 3 "Extended family cover"'), "answered"),
    "Q11": ("A completed disability claim form (employer section and member section), a medical report from the treating "
            "doctor, the member's last 3 payslips and a certified copy of the member's ID document.",
            (FAQ, 'section "What documents are needed for a disability claim?"'), "answered"),
    "Q12": ("The death must be notified within 6 months of the date of death; claims notified later may be declined.",
            (GL26, 'section 5 "Claims"'), "answered"),
    "Q13": ("", None, "not_found"),
    "Q14": ("", (GL26, 'section 6 "Premiums"'), "not_found"),
    "Q15": ("Kestrel aims to make a decision on group life claims within 10 working days of receiving all required documents.",
            (FAQ, 'section "How long does it take to assess a claim?"'), "answered"),
}
REASONS = {
    "Q13": "The approved documents do not say whether Kestrel offers group dental cover. Please ask the product team.",
    "Q14": "The documents do not give a premium rate per R1 000 of cover; they say premiums are a percentage of the salary bill.",
}


def good_result(qid: str, question: str = "q") -> AskResult:
    answer, source, outcome = GOOD[qid]
    return make_result(answer, outcome=outcome, sources=[source] if source else [], reason=REASONS.get(qid), question=question)


def good_answers() -> dict[str, AskResult]:
    """Question text -> a correct answer, for the 15 workbook questions."""
    gold = load_gold(DEFAULT_GOLD)
    return {entry.question: good_result(qid, entry.question) for qid, entry in gold.questions.items()}


class FakeService:
    """Returns canned results by question text; raises for questions listed in `raise_for`."""

    def __init__(self, answers: Mapping[str, AskResult], raise_for: Sequence[str] = ()) -> None:
        self.answers = dict(answers)
        self.raise_for = set(raise_for)
        self.calls: list[tuple[str, str, Principal, str | None]] = []
        self.ingested = 0

    def ask(self, bot_id: str, question: str, principal: Principal, model: str | None = None) -> AskResult:
        self.calls.append((bot_id, question, principal, model))
        if question in self.raise_for:
            raise RuntimeError("simulated failure")
        result = self.answers.get(question)
        if result is None:
            return make_result(outcome="error", error_category=ErrorCategory.INTERNAL, reason="no canned answer")
        return result

    def ingest(self, principal: Principal, bot_id: str | None = None) -> IngestReport:
        self.ingested += 1
        return IngestReport(files=(IngestedFile(collection="kestrel-products", filename="a.docx", action="unchanged"),))
