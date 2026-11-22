"""The pre-ranking filter: collections, statuses, one edition per family."""

from __future__ import annotations

from datetime import date

import pytest
from docbuilders import Prose, build, entry, save

from kestrel.contracts import DocumentStatus, SearchScope
from kestrel.store import Store
from kestrel.store.eligibility import Candidate, eligible

C, S, P, B = DocumentStatus.CURRENT, DocumentStatus.SUPERSEDED, DocumentStatus.PENDING, DocumentStatus.BLOCKED

# Family "guide" has three editions; family "faq" has one; "loose" has no family; one of each bad status.
DOCS = [
    Candidate("g24", "alpha", S, "guide", "2024", date(2024, 1, 1)),
    Candidate("g25", "alpha", S, "guide", "2025", date(2025, 1, 1)),
    Candidate("g26", "alpha", C, "guide", "2026", date(2026, 1, 1)),
    Candidate("faq", "alpha", C, "faq", "2025", date(2025, 3, 1)),
    Candidate("loose", "alpha", C),
    Candidate("pend", "alpha", P, "guide", "2027", date(2027, 1, 1)),
    Candidate("blk", "alpha", B, "notes", "2025", date(2020, 1, 1)),
    Candidate("other", "beta", C, "guide", "2026", date(2026, 1, 1)),
]


def _scope(**kwargs: object) -> SearchScope:
    return SearchScope(collections=("alpha",), **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        # Default: current only. Superseded, pending, blocked and other collections are out.
        (_scope(), ["faq", "g26", "loose"]),
        # Explicit statuses without a selector: the caller asked for every approved edition.
        (_scope(statuses=frozenset({C, S})), ["faq", "g24", "g25", "g26", "loose"]),
        # An edition replaces each family's current one where the family has it; others unaffected.
        (_scope(edition="2025"), ["faq", "g25", "loose"]),
        (_scope(edition="2024"), ["faq", "g24", "loose"]),
        (_scope(edition="2026"), ["faq", "g26", "loose"]),
        # A pending or blocked edition is never chosen, even when named.
        (_scope(edition="2027"), ["faq", "g26", "loose"]),
        # A label no family has leaves every family at its current edition.
        (_scope(edition="1999"), ["faq", "g26", "loose"]),
        # Dates: the latest edition in force on the date, per dated family.
        (_scope(effective_on=date(2025, 6, 1)), ["faq", "g25", "loose"]),
        (_scope(effective_on=date(2025, 1, 1)), ["g25", "loose"]),  # faq starts 1 March 2025
        (_scope(effective_on=date(2024, 12, 31)), ["g24", "loose"]),
        (_scope(effective_on=date(2030, 1, 1)), ["faq", "g26", "loose"]),  # pending 2027 never counts
        (_scope(effective_on=date(2000, 1, 1)), ["loose"]),
        # Collections always filter.
        (SearchScope(collections=("beta",), edition="2025"), ["other"]),
        (SearchScope(collections=("alpha", "beta")), ["faq", "g26", "loose", "other"]),
        (SearchScope(collections=("gamma",)), []),
    ],
)
def test_eligibility_rules(scope: SearchScope, expected: list[str]) -> None:
    assert eligible(DOCS, scope) == expected


def test_never_two_editions_of_one_family_under_a_selector() -> None:
    scopes = [_scope(edition=e) for e in ("2024", "2025", "2026", "2027")]
    scopes += [_scope(effective_on=date(y, m, 1)) for y in range(2023, 2028) for m in (1, 6)]
    for scope in scopes:
        chosen = eligible(DOCS, scope)
        assert sum(doc in chosen for doc in ("g24", "g25", "g26")) <= 1, scope


def test_blocked_and_pending_cannot_be_requested() -> None:
    with pytest.raises(ValueError):
        _scope(statuses=frozenset({B}))
    with pytest.raises(ValueError):
        _scope(statuses=frozenset({C, P}))


def test_duplicate_edition_labels_in_a_family_pick_one() -> None:
    docs = [
        Candidate("a", "alpha", S, "guide", "2025", date(2025, 1, 1)),
        Candidate("b", "alpha", C, "guide", "2025", date(2025, 1, 1)),
    ]
    assert eligible(docs, _scope(edition="2025")) == ["b"]
    assert eligible(docs, _scope(effective_on=date(2025, 2, 1))) == ["b"]


def test_edition_label_matching_ignores_case() -> None:
    docs = [Candidate("a", "alpha", S, "guide", "Second", date(2025, 1, 1)), Candidate("b", "alpha", C, "guide", "Third")]
    assert eligible(docs, _scope(edition="second")) == ["a"]


def test_repository_applies_the_same_rules(store: Store) -> None:
    ids = {}
    for edition, status in [("2025", S), ("2026", C)]:
        built = build("alpha", f"guide-{edition}.md", [Prose(("H",), f"edition {edition} text")])
        save(store, built, status=status,
             manifest=entry(built, family="guide", edition=edition, effective_from=date(int(edition), 1, 1)))
        ids[edition] = built.id
    blocked = build("alpha", "withdrawn.md", [Prose(("H",), "withdrawn text")])
    save(store, blocked, status=B)
    pending = build("alpha", "unapproved.md", [Prose(("H",), "unapproved text")])
    save(store, pending, status=P)

    assert store.documents.eligible_document_ids(_scope()) == [ids["2026"]]
    assert store.documents.eligible_document_ids(_scope(edition="2025")) == [ids["2025"]]
    assert store.documents.eligible_document_ids(_scope(effective_on=date(2025, 7, 1))) == [ids["2025"]]
    everything = _scope(statuses=frozenset({C, S}))
    assert set(store.documents.eligible_document_ids(everything)) == {ids["2025"], ids["2026"]}
