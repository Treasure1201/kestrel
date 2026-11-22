"""The built-in tools bound to one request context."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kestrel.agent.context import Budget, RequestContext
from kestrel.agent.tools import UNTRUSTED_NOTICE, build_builtin_tools
from kestrel.contracts import DocumentStatus, SearchHit, SearchResult, SearchScope, TableRowView, TableView

SCOPE = SearchScope(collections=("demo",))


def _hit(n: int, *, kind: str = "prose", text: str | None = None) -> SearchHit:
    return SearchHit(
        chunk_id=f"d1:s00{n}:p00", document_id="d1", section_id=f"d1:s00{n}", filename="guide.md", title="Guide",
        locator=f"section {n}", status=DocumentStatus.CURRENT, edition="2026", kind=kind,  # type: ignore[arg-type]
        text=text or f"Excerpt number {n}.", score=1.0, rank=1,
    )


class FakeRetriever:
    """Returns fixed hits per query word and one table for section d1:s009."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def resolve_scope(self, question, knowledge):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def search(self, query: str, scope: SearchScope, limit: int) -> SearchResult:
        self.queries.append(query)
        if "table" in query:
            return SearchResult(hits=(_hit(9, kind="table_row", text="Band: 3; Limit: R 3 500 000"),))
        if "nothing" in query:
            return SearchResult()
        return SearchResult(hits=(_hit(1), _hit(2)))

    def get_table(self, section_id: str, scope: SearchScope) -> TableView | None:
        if section_id != "d1:s009":
            return None
        rows = tuple(TableRowView(row_number=i, cells=(str(i), f"R {i} 000 000")) for i in (1, 2, 3))
        return TableView(section_id=section_id, document_id="d1", filename="guide.md", title="Guide", locator="table 1",
                         status=DocumentStatus.CURRENT, edition="2026", header=("Band", "Limit"), rows=rows)


@pytest.fixture
def ctx() -> RequestContext:
    return RequestContext(scope=SCOPE, budget=Budget(max_model_calls=10, max_searches=3, max_repairs=1, timeout_s=60))


def _tools(ctx: RequestContext, names=("search_documents", "get_table", "calculate")):  # type: ignore[no-untyped-def]
    retriever = FakeRetriever()
    return {tool.name: tool for tool in build_builtin_tools(names, ctx, retriever)}, retriever


def test_labels_are_stable_within_a_request(ctx: RequestContext) -> None:
    tools, _ = _tools(ctx)
    first = tools["search_documents"].invoke({"query": "waiting period"})
    assert first.startswith(UNTRUSTED_NOTICE)
    assert "[c1] guide.md > section 1 (status: current, edition 2026)" in first
    assert "[c2]" in first
    second = tools["search_documents"].invoke({"query": "other words"})
    assert "No new excerpts" in second and "c1, c2" in second
    assert list(ctx.evidence) == ["c1", "c2"]


def test_duplicate_query_is_refused_without_searching(ctx: RequestContext) -> None:
    tools, retriever = _tools(ctx)
    tools["search_documents"].invoke({"query": "Waiting period"})
    again = tools["search_documents"].invoke({"query": "  waiting   PERIOD "})
    assert "already ran this search" in again
    assert retriever.queries == ["Waiting period"]
    assert ctx.budget.searches == 1


def test_search_budget_is_enforced(ctx: RequestContext) -> None:
    tools, retriever = _tools(ctx)
    for query in ("a", "b", "c"):
        tools["search_documents"].invoke({"query": query})
    refused = tools["search_documents"].invoke({"query": "d"})
    assert "Search budget reached (3 searches)" in refused
    assert len(retriever.queries) == 3


def test_empty_results_say_so(ctx: RequestContext) -> None:
    tools, _ = _tools(ctx)
    assert "No matching excerpts" in tools["search_documents"].invoke({"query": "nothing here"})


def test_table_hits_point_to_get_table_and_rows_are_labelled(ctx: RequestContext) -> None:
    tools, _ = _tools(ctx)
    found = tools["search_documents"].invoke({"query": "table lookup"})
    assert 'call get_table("c1")' in found
    table = tools["get_table"].invoke({"label": "c1"})
    assert "[c1.r1] Band: 1; Limit: R 1 000 000" in table
    assert "[c1.r3] Band: 3; Limit: R 3 000 000" in table
    assert ctx.evidence["c1.r2"].text == "Band: 2; Limit: R 2 000 000"
    assert ctx.evidence["c1.r2"].row is not None
    assert "[c1.r1]" in tools["get_table"].invoke({"label": "c1.r2"})  # a row label reaches the same table


def test_get_table_errors(ctx: RequestContext) -> None:
    tools, _ = _tools(ctx)
    assert tools["get_table"].invoke({"label": "c7"}).startswith("Error: unknown label")
    tools["search_documents"].invoke({"query": "prose"})
    assert "is not part of a table" in tools["get_table"].invoke({"label": "c1"})


def test_calculate_records_results_and_reports_errors(ctx: RequestContext) -> None:
    tools, _ = _tools(ctx)
    assert tools["calculate"].invoke({"expression": "0.75 * 40000"}) == "0.75 * 40000 = 30000"
    assert ctx.calculations[0].value == Decimal("30000.00")
    assert tools["calculate"].invoke({"expression": "__import__('os')"}).startswith("Error:")
    assert len(ctx.calculations) == 1


def test_only_named_tools_are_built(ctx: RequestContext) -> None:
    tools, _ = _tools(ctx, names=("search_documents",))
    assert list(tools) == ["search_documents"]
    with pytest.raises(ValueError):
        _tools(ctx, names=("search_documents", "delete_everything"))
