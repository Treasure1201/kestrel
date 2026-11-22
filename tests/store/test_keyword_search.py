"""BM25 keyword search: sanitising, short labels, numbers and the scope filter."""

from __future__ import annotations

from datetime import date

import pytest
from docbuilders import Built, Prose, Table, build, entry, save

from kestrel.contracts import DocumentStatus, SearchScope
from kestrel.store import Store
from kestrel.store.fts import match_expression

ALPHA = SearchScope(collections=("alpha",))


@pytest.fixture
def guide(store: Store) -> Built:
    built = build(
        "alpha",
        "guide.md",
        [
            Prose(("Disability", "Option A"), "Option A pays a monthly benefit after a waiting period of three months."),
            Prose(("Disability", "Option B"), "Option B pays a monthly benefit after a waiting period of six months."),
            Prose(("Funeral",), "The funeral benefit for a spouse is R30 000 and for a child R10 000."),
            Prose(("Limits",), "The free cover limit is R5000000 for schemes with more than 100 members."),
            Prose(("Claims",), "Claims must be notified within 90 days of the event."),
            Table(("Cover bands",), ("Age", "Cover"), [("18-30", "50,000"), ("31-50", "40 000")]),
        ],
    )
    save(store, built)
    return built


def _top(store: Store, query: str, scope: SearchScope = ALPHA) -> str:
    hits = store.documents.keyword_search(query, scope, 5)
    assert hits, f"no hits for {query!r}"
    return hits[0].text


@pytest.mark.parametrize(
    "query",
    [
        '"unbalanced quote',
        'option "B" and "',
        "(option OR",
        "waiting) period (",
        "option AND b",
        "option OR",
        "NEAR(option b, 2)",
        "NOT option",
        "opt*",
        "*",
        "option-b waiting-period",
        "-option",
        "heading:funeral",
        "text: spouse",
        "^option",
        "option + b ~ c",
        "{text heading}: spouse",
        "",
        "   ",
        "?!.,;",
        "\x00 option",
        "ümlaut café",
    ],
)
def test_fts_syntax_in_user_text_never_errors(store: Store, guide: Built, query: str) -> None:
    hits = store.documents.keyword_search(query, ALPHA, 5)
    assert isinstance(hits, list)


def test_operators_are_not_interpreted() -> None:
    expression = match_expression('waiting NEAR period AND "x" OR (y*)')
    assert expression is not None
    for token in ("NEAR", "AND", "(", ")", "*"):
        assert token not in expression.replace(" OR ", " ")
    assert match_expression("") is None
    assert match_expression("!!!") is None


def test_short_label_finds_the_right_option(store: Store, guide: Built) -> None:
    assert "Option B" in _top(store, "What is the waiting period for Option B?")
    assert "Option A" in _top(store, "option a waiting period")
    # The label is never searched alone: "B" does not match text without the phrase.
    assert match_expression("Option B") == '"option" OR "option b"'


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("R30 000 spouse", "R30 000"),
        ("benefit of 30 000", "R30 000"),
        ("cover of 50000", "50,000"),
        ("cover of 50 000", "50,000"),
        ("cover 40000", "40 000"),
        ("cover 40,000", "40 000"),
        ("within 90 days", "90 days"),
        ("R5000000 limit", "R5000000"),
        ("100 members", "100 members"),
    ],
)
def test_numbers_match_grouped_and_ungrouped(store: Store, guide: Built, query: str, expected: str) -> None:
    assert expected in _top(store, query)


def test_stemming_matches_word_forms(store: Store, guide: Built) -> None:
    assert "notified" in _top(store, "notify claim")
    assert "waiting period" in _top(store, "periods")


def test_heading_text_is_searchable(store: Store, guide: Built) -> None:
    assert "funeral" in _top(store, "funeral").lower()


def test_ranks_and_scores_are_ordered(store: Store, guide: Built) -> None:
    hits = store.documents.keyword_search("monthly benefit waiting period", ALPHA, 10)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert store.documents.keyword_search("monthly", ALPHA, 0) == []


def test_collection_isolation(store: Store, guide: Built) -> None:
    twin = build("beta", "guide.md", [Prose(("Disability", "Option B"), "Option B waiting period of six months.")])
    save(store, twin)
    alpha = store.documents.keyword_search("Option B waiting period", ALPHA, 20)
    beta = store.documents.keyword_search("Option B waiting period", SearchScope(collections=("beta",)), 20)
    assert alpha and {h.document_id for h in alpha} == {guide.id}
    assert [h.document_id for h in beta] == [twin.id]


def test_superseded_blocked_and_pending_are_filtered(store: Store) -> None:
    old = build("alpha", "g25.md", [Prose(("Waiting",), "Old edition waiting period text")])
    new = build("alpha", "g26.md", [Prose(("Waiting",), "New edition waiting period text")])
    save(store, old, status=DocumentStatus.SUPERSEDED,
         manifest=entry(old, family="g", edition="2025", effective_from=date(2025, 1, 1)))
    save(store, new, manifest=entry(new, family="g", edition="2026", effective_from=date(2026, 1, 1)))
    for status, name in [(DocumentStatus.BLOCKED, "b.md"), (DocumentStatus.PENDING, "p.md")]:
        save(store, build("alpha", name, [Prose(("Waiting",), f"{name} waiting period text")]), status=status)

    def ids(scope: SearchScope) -> set[str]:
        return {h.document_id for h in store.documents.keyword_search("waiting period", scope, 20)}

    assert ids(ALPHA) == {new.id}
    assert ids(SearchScope(collections=("alpha",), edition="2025")) == {old.id}
    assert ids(SearchScope(collections=("alpha",), effective_on=date(2025, 5, 1))) == {old.id}
    assert ids(SearchScope(collections=("alpha",), statuses=frozenset(DocumentStatus) - {
        DocumentStatus.BLOCKED, DocumentStatus.PENDING})) == {old.id, new.id}


def test_resolve_hits_keeps_order_and_drops_out_of_scope(store: Store, guide: Built) -> None:
    other = build("beta", "x.md", [Prose(("H",), "beta text")])
    save(store, other)
    wanted = [guide.chunks[2].id, other.chunks[0].id, "missing", guide.chunks[0].id, guide.chunks[2].id]
    hits = store.documents.resolve_hits(wanted, ALPHA)
    assert [h.chunk_id for h in hits] == [guide.chunks[2].id, guide.chunks[0].id]
    assert [h.rank for h in hits] == [1, 2]
    assert store.documents.resolve_hits([], ALPHA) == []


def test_hits_carry_citation_fields(store: Store, guide: Built) -> None:
    row_hit = next(h for h in store.documents.keyword_search("18-30 cover", ALPHA, 10) if h.kind == "table_row")
    assert row_hit.row_index == 1
    assert row_hit.filename == "guide.md"
    assert row_hit.heading_path == ("Cover bands",)
    assert row_hit.locator.endswith("row 1")
    assert row_hit.status is DocumentStatus.CURRENT
