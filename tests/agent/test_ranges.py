"""Range detection in tables and the `get_table` range lookup."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kestrel.agent.context import Budget, RequestContext
from kestrel.agent.ranges import find_ranges, text_band
from kestrel.agent.tools import build_builtin_tools
from kestrel.contracts import DocumentStatus, SearchHit, SearchResult, SearchScope, TableRowView, TableView

SCOPE = SearchScope(collections=("demo",))


def _rows(*cells: tuple[str, ...]) -> tuple[TableRowView, ...]:
    return tuple(TableRowView(row_number=i, cells=row) for i, row in enumerate(cells, start=1))


BANDED = _rows(
    ("1", "1", "49", "R 1 000 000"),
    ("2", "50", "99", "R 2 000 000"),
    ("3", "100", "249", "R 3 500 000"),
    ("4", "250", "499", "R 5 000 000"),
    ("5", "500", "and above", "R 8 000 000"),
)


def test_two_numeric_columns_with_an_open_last_row_are_ranges() -> None:
    ranges = find_ranges(BANDED)
    assert ranges is not None
    assert [band.describe() for band in ranges.bands] == ["1 to 49", "50 to 99", "100 to 249", "250 to 499", "500 or more"]
    assert ranges.lookup(Decimal(320)).row.row_number == 4  # type: ignore[union-attr]
    assert ranges.lookup(Decimal(100000)).row.row_number == 5  # type: ignore[union-attr]


def test_value_exactly_on_a_boundary() -> None:
    ranges = find_ranges(BANDED)
    assert ranges is not None
    assert ranges.lookup(Decimal(249)).row.row_number == 3  # type: ignore[union-attr]
    assert ranges.lookup(Decimal(250)).row.row_number == 4  # type: ignore[union-attr]
    assert ranges.lookup(Decimal(1)).row.row_number == 1  # type: ignore[union-attr]


def test_value_below_the_first_band_or_between_bands_matches_no_row() -> None:
    ranges = find_ranges(BANDED)
    assert ranges is not None
    below = ranges.lookup(Decimal(0))
    assert below.row is None and below.before is None and below.after.row_number == 1  # type: ignore[union-attr]
    between = ranges.lookup(Decimal("49.5"))
    assert between.row is None and between.before.row_number == 1 and between.after.row_number == 2  # type: ignore[union-attr]


@pytest.mark.parametrize("cells", [
    (("under 6",), ("6 to 21",), ("22-64 years",), ("65+",)),
    (("up to 1 000",), ("1 001 - 5 000",), ("5 001 or more",)),
])
def test_a_single_column_of_text_ranges_is_recognised(cells: tuple[tuple[str], ...]) -> None:
    ranges = find_ranges(_rows(*(c + ("x",) for c in cells)))
    assert ranges is not None and len(ranges.bands) == len(cells)


def test_text_range_forms() -> None:
    assert text_band(1, "under 6").covers(Decimal("5.99"))  # type: ignore[union-attr]
    assert not text_band(1, "under 6").covers(Decimal(6))  # type: ignore[union-attr]
    assert text_band(1, "R1,000+").low == Decimal(1000)  # type: ignore[union-attr]
    assert text_band(1, "250 – 499").describe() == "250 to 499"  # type: ignore[union-attr]
    assert not text_band(1, "over 1000").covers(Decimal(1000))  # type: ignore[union-attr]
    assert text_band(1, "about fifty") is None


def test_text_column_lookup() -> None:
    ranges = find_ranges(_rows(("under 6", "a"), ("6 to 21", "b"), ("22 and above", "c")))
    assert ranges is not None
    assert ranges.lookup(Decimal(6)).row.row_number == 2  # type: ignore[union-attr]
    assert ranges.lookup(Decimal(5)).row.row_number == 1  # type: ignore[union-attr]


@pytest.mark.parametrize("rows", [
    _rows(("1", "R 1 000 000"), ("2", "R 2 000 000"), ("3", "R 3 000 000")),  # a band number and an amount
    _rows(("Monthly", "30 days"), ("Yearly", "365 days")),  # no numbers in pairs
    _rows(("2024", "x"), ("2025", "y"), ("2026", "z")),  # plain numbers only
    _rows(("500", "999"), ("250", "499"), ("1", "249")),  # descending
    _rows(("1", "100"), ("50", "200"), ("201", "300")),  # overlapping
    _rows(("10 to 20", "a"), ("15 to 30", "b")),  # overlapping text ranges
    _rows(("1", "and above"), ("50", "99")),  # open bound before the last row
    _rows(("1", "49"),),  # one row is not a table of ranges
])
def test_tables_that_are_not_ranges(rows: tuple[TableRowView, ...]) -> None:
    assert find_ranges(rows) is None


class _Retriever:
    def __init__(self, rows: tuple[TableRowView, ...]) -> None:
        self.rows = rows

    def search(self, query: str, scope: SearchScope, limit: int) -> SearchResult:
        hit = SearchHit(chunk_id="d1:s001:r03", document_id="d1", section_id="d1:s001", filename="sheet.xlsx", title="Sheet",
                        locator="table 1, row 3", status=DocumentStatus.CURRENT, kind="table_row", row_index=3,
                        text="row three", score=1.0, rank=1)
        return SearchResult(hits=(hit,))

    def get_table(self, section_id: str, scope: SearchScope) -> TableView | None:
        return TableView(section_id=section_id, document_id="d1", filename="sheet.xlsx", title="Sheet", locator="table 1",
                         status=DocumentStatus.CURRENT, header=("Band", "From", "To", "Limit"), rows=self.rows)


def _tools(rows: tuple[TableRowView, ...] = BANDED):  # type: ignore[no-untyped-def]
    ctx = RequestContext(scope=SCOPE, budget=Budget(max_model_calls=10, max_searches=3, max_repairs=1, timeout_s=60))
    tools = {t.name: t for t in build_builtin_tools(("search_documents", "get_table"), ctx, _Retriever(rows))}
    return ctx, tools


def test_search_hint_names_the_rows_range_and_asks_for_value() -> None:
    _, tools = _tools()
    found = tools["search_documents"].invoke({"query": "limit"})
    assert "this row covers 100 to 249 only" in found
    assert 'call get_table("c1", value=<the number from the question>)' in found


def test_get_table_with_value_names_the_row_first() -> None:
    ctx, tools = _tools()
    tools["search_documents"].invoke({"query": "limit"})
    out = tools["get_table"].invoke({"label": "c1", "value": 320})
    assert out.startswith("Row for 320: c1.r4 (250 to 499). Cite this row.")
    assert "[c1.r4] (r4 covers 250 to 499) Band: 4; From: 250; To: 499; Limit: R 5 000 000" in out
    assert ctx.evidence["c1.r4"].text == "Band: 4; From: 250; To: 499; Limit: R 5 000 000"  # the citable text is unchanged
    step = ctx.steps[-1]
    assert step.kind == "table" and "value 320" in step.detail and "c1.r4" in step.detail


def test_get_table_without_value_still_annotates_rows() -> None:
    _, tools = _tools()
    tools["search_documents"].invoke({"query": "limit"})
    out = tools["get_table"].invoke({"label": "c1"})
    assert not out.startswith("Row for")
    assert "[c1.r5] (r5 covers 500 or more)" in out


def test_get_table_value_outside_every_row_says_so() -> None:
    _, tools = _tools()
    tools["search_documents"].invoke({"query": "limit"})
    out = tools["get_table"].invoke({"label": "c1", "value": "0"})
    assert out.startswith("No row covers 0: it is below the first row, c1.r1 (1 to 49).")


def test_get_table_value_on_a_table_without_ranges() -> None:
    plain = _rows(("a", "b", "c", "d"), ("e", "f", "g", "h"))
    _, tools = _tools(plain)
    tools["search_documents"].invoke({"query": "limit"})
    out = tools["get_table"].invoke({"label": "c1", "value": 5})
    assert out.startswith("This table has no recognised number ranges")
    assert "covers" not in out.split("\n\n", 1)[1]
