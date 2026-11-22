"""Small builders for evidence, answers and scope decisions used by the check tests."""

from __future__ import annotations

from kestrel.contracts import (
    AgentAnswer,
    CalculationOut,
    CitationOut,
    DocumentStatus,
    Evidence,
    ScopeDecision,
    SearchHit,
    SearchScope,
    TableRowView,
)

SCOPE = ScopeDecision(scope=SearchScope(collections=("demo",)))
EXPLICIT = ScopeDecision(scope=SearchScope(collections=("demo",), edition="2025"), explicit=True)


def hit(
    text: str,
    *,
    chunk: str = "d1:s001:p00",
    kind: str = "prose",
    status: DocumentStatus = DocumentStatus.CURRENT,
    title: str = "Benefit Guide",
    locator: str = "section 4 \"Waiting periods\"",
    edition: str | None = "2026",
    section: str = "d1:s001",
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk, document_id="d1", section_id=section, filename="Benefit_Guide.docx", title=title,
        locator=locator, status=status, edition=edition, kind=kind, text=text, score=1.0, rank=1,  # type: ignore[arg-type]
    )


def prose(label: str, text: str, **kwargs: object) -> Evidence:
    return Evidence(label=label, hit=hit(text, chunk=f"d1:s00{label[1:]}:p00", **kwargs))  # type: ignore[arg-type]


HEADER = ("Band", "Members from", "Members to", "Free cover limit (R)")
ROWS = (
    TableRowView(row_number=1, cells=("1", "10", "49", "R 1 000 000")),
    TableRowView(row_number=2, cells=("2", "50", "99", "R 2 000 000")),
    TableRowView(row_number=3, cells=("3", "100", "249", "R 3 500 000")),
)


def table_evidence(base: str = "c2") -> dict[str, Evidence]:
    table_hit = hit("Band | Members from | Members to | Free cover limit (R)", chunk="d1:s009:t00", kind="table", section="d1:s009")
    found = {base: Evidence(label=base, hit=table_hit)}
    for row in ROWS:
        label = f"{base}.r{row.row_number}"
        found[label] = Evidence(label=label, hit=table_hit, row=row, header=HEADER)
    return found


def answer(
    text: str,
    citations: list[tuple[str, str]] = (),  # type: ignore[assignment]
    *,
    status: str = "answered",
    calculations: list[tuple[str, str, str]] = (),  # type: ignore[assignment]
    caveats: list[str] = (),  # type: ignore[assignment]
    reason: str | None = None,
) -> AgentAnswer:
    return AgentAnswer(
        status=status,  # type: ignore[arg-type]
        answer=text,
        citations=[CitationOut(label=label, quote=quote, supports="the answer") for label, quote in citations],
        calculations=[CalculationOut(expression=e, result=r, rule=rule) for e, r, rule in calculations],
        caveats=list(caveats),
        reason=reason,
    )
