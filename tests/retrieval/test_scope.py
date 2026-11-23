"""Reading edition and date references in questions."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from kestrel.contracts import DocumentRecord, DocumentStatus, KnowledgeConfig, SearchScope
from kestrel.retrieval.scope import resolve_scope

KNOWLEDGE = KnowledgeConfig(collections=("alpha", "beta"))
DEFAULT = SearchScope(collections=("alpha", "beta"))


def doc(edition: str | None, status: DocumentStatus, effective: date | None, family: str | None = "guide") -> DocumentRecord:
    return DocumentRecord(
        id=f"id-{edition}-{status}", collection="alpha", sha256="0" * 64, filename=f"{edition}.md", title="Guide",
        media_type="text/markdown", family=family, edition=edition, effective_from=effective, status=status,
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


DOCS = [
    doc("2025", DocumentStatus.SUPERSEDED, date(2025, 1, 1)),
    doc("2026", DocumentStatus.CURRENT, date(2026, 1, 1)),
    doc("2027", DocumentStatus.PENDING, date(2027, 1, 1)),
    doc("2023", DocumentStatus.BLOCKED, date(2023, 1, 1)),
]

EDITION_2025 = SearchScope(collections=("alpha", "beta"), edition="2025")
JUNE_2025 = SearchScope(collections=("alpha", "beta"), effective_on=date(2025, 6, 1))

CLARIFY = "clarify"

CASES: list[tuple[str, SearchScope | str]] = [
    # No mention: the default scope.
    ("What is the waiting period for Option B?", DEFAULT),
    ("What is the difference between Option A and Option B?", DEFAULT),
    ("How has the benefit changed for children?", DEFAULT),
    # One edition: switches.
    ("What is the waiting period in the 2025 edition?", EDITION_2025),
    ("Under the 2025 guide, what is the waiting period?", EDITION_2025),
    ("under the 2025 product guide what applies", EDITION_2025),
    ("What does version 2025 say about claims?", EDITION_2025),
    ("What did the guide from 2025 say?", EDITION_2025),
    ("In the 2025 edition, and again per the 2025 guide, what applies?", EDITION_2025),  # same edition twice
    ("What did the 2026 edition say?", SearchScope(collections=("alpha", "beta"), edition="2026")),
    # One date with a cue: switches.
    ("As of 2025-06-01, what is the waiting period?", JUNE_2025),
    ("What applied as at 1 June 2025?", JUNE_2025),
    ("as of June 1, 2025 what was the cover", JUNE_2025),
    ("effective 1st of June 2025, what applies?", JUNE_2025),
    ("What rules were in force on 2025-06-01?", JUNE_2025),
    # Two editions or dates, or comparisons: clarify.
    ("Compare the 2025 and 2026 editions", CLARIFY),
    ("What changed between the 2025 edition and the 2026 edition?", CLARIFY),
    ("2025 edition vs 2026 edition waiting period", CLARIFY),
    ("As of 2025-06-01 under the 2026 edition, what applies?", CLARIFY),
    ("as of 2025-06-01 and as of 2026-02-01", CLARIFY),
    ("How do the editions differ on waiting periods?", CLARIFY),
    ("What changed in the 2026 edition?", CLARIFY),
    # Quantities are not editions.
    ("A scheme with 2025 members wants cover", DEFAULT),
    ("Is R2025 the monthly premium?", DEFAULT),
    ("Is a premium of R 2025 allowed?", DEFAULT),
    ("Can 2025 employees join the guide's scheme?", DEFAULT),
    ("Does a 2025% increase apply?", DEFAULT),
    ("Is $2025 enough?", DEFAULT),
    # A bare year or uncued date could be an event or an edition: clarify.
    ("A member died in 2025; how long do we have to claim?", CLARIFY),
    ("What happens to a claim from 2025?", CLARIFY),
    ("The member died on 2025-03-01, what is the claim period?", CLARIFY),
    ("Under the 2025 guide, a member who joined in 2019 is covered?", CLARIFY),
    # Editions must exist; invalid dates and dates before any edition clarify.
    ("What did the 2024 edition say?", CLARIFY),
    ("What does the 2027 edition say?", CLARIFY),  # pending: not an available edition
    ("What does the 2023 edition say?", CLARIFY),  # blocked
    ("As of 2025-02-30, what applies?", CLARIFY),
    ("As of 2020-01-01, what applies?", CLARIFY),
]


@pytest.mark.parametrize(("question", "expected"), CASES)
def test_scope_decisions(question: str, expected: SearchScope | str) -> None:
    decision = resolve_scope(question, KNOWLEDGE, DOCS)
    if expected == CLARIFY:
        assert decision.scope is None and decision.clarification, decision
        assert decision.explicit is False
    else:
        assert decision.clarification is None, decision.clarification
        assert decision.scope == expected
        assert decision.explicit is (expected != DEFAULT)


def test_unknown_edition_lists_available_editions() -> None:
    decision = resolve_scope("What did the 2024 edition say?", KNOWLEDGE, DOCS)
    assert decision.clarification is not None
    assert "2025, 2026" in decision.clarification
    assert "2027" not in decision.clarification and "2023" not in decision.clarification


def test_year_maps_to_a_dotted_edition_label() -> None:
    docs = [doc("2025.1", DocumentStatus.SUPERSEDED, date(2025, 1, 1)), doc("2026.2", DocumentStatus.CURRENT, None)]
    decision = resolve_scope("Under the 2025 edition what applies?", KNOWLEDGE, docs)
    assert decision.scope is not None and decision.scope.edition == "2025.1"
    exact = resolve_scope("What does version 2026.2 say?", KNOWLEDGE, docs)
    assert exact.scope is not None and exact.scope.edition == "2026.2" and exact.explicit


def test_several_matching_editions_ask_which() -> None:
    docs = [doc("2025.1", DocumentStatus.SUPERSEDED, None), doc("2025.2", DocumentStatus.CURRENT, None, family="other")]
    decision = resolve_scope("In the 2025 edition, what applies?", KNOWLEDGE, docs)
    assert decision.clarification is not None and "2025.1" in decision.clarification and "2025.2" in decision.clarification
    picked = resolve_scope("In version 2025.2, what applies?", KNOWLEDGE, docs)
    assert picked.scope is not None and picked.scope.edition == "2025.2"


def test_documents_without_editions() -> None:
    docs = [doc(None, DocumentStatus.CURRENT, None, family=None)]
    assert resolve_scope("What did the 2025 edition say?", KNOWLEDGE, docs).clarification
    assert resolve_scope("What is covered?", KNOWLEDGE, docs).scope == DEFAULT
    # A date still selects: undated documents stay at their current edition.
    assert resolve_scope("as of 2025-06-01 what is covered", KNOWLEDGE, docs).scope == JUNE_2025


def test_word_edition_labels_are_found_by_name() -> None:
    docs = [doc("Second", DocumentStatus.SUPERSEDED, None), doc("Third", DocumentStatus.CURRENT, None)]
    for question in ("what does the second edition say", "under edition Second, what applies", "edition 'second'"):
        decision = resolve_scope(question, KNOWLEDGE, docs)
        assert decision.scope is not None and decision.scope.edition == "Second", question
        assert decision.explicit
    assert resolve_scope("the second edition versus the third edition", KNOWLEDGE, docs).clarification
    assert resolve_scope("is a second claim allowed", KNOWLEDGE, docs).scope == DEFAULT
    decision = resolve_scope("what does version 2 say", KNOWLEDGE, [doc("2", DocumentStatus.CURRENT, None)])
    assert decision.scope is not None and decision.scope.edition == "2"


SINGLE = [
    doc("2026", DocumentStatus.CURRENT, date(2026, 1, 1)),
    doc("2024", DocumentStatus.CURRENT, date(2024, 1, 1), family="other"),
    doc("2025", DocumentStatus.BLOCKED, date(2025, 1, 1)),
]


@pytest.mark.parametrize("question", [
    "How many working days are there between 2026-03-11 and 2026-03-20?",
    "Can leave taken in December 2025 be carried over?",
    "What was decided on 3 March 2026?",
])
def test_years_and_dates_are_content_when_no_family_has_two_editions(question: str) -> None:
    # Each family has one approved edition (a blocked edition does not count), so there is nothing to choose.
    decision = resolve_scope(question, KNOWLEDGE, SINGLE)
    assert decision.clarification is None and decision.scope == DEFAULT and not decision.explicit
    assert resolve_scope(question, KNOWLEDGE, [doc(None, DocumentStatus.CURRENT, None, family=None)]).scope == DEFAULT


@pytest.mark.parametrize("question", [
    "How many working days are there between 2026-03-11 and 2026-03-20?",
    "Can leave taken in December 2025 be carried over?",
])
def test_years_and_dates_still_ask_when_a_family_has_two_editions(question: str) -> None:
    assert resolve_scope(question, KNOWLEDGE, DOCS).clarification is not None
