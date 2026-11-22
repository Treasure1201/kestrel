"""Hybrid search: fusion, isolation through every path, scope, and keyword-only fallback."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date

import pytest
from docbuilders import Built, Prose, Table, build, entry, save
from fakes import FakeEmbedder

from kestrel.contracts import (
    DocumentStatus,
    KnowledgeConfig,
    Retriever,
    SearchScope,
    VectorItem,
    VectorMatch,
)
from kestrel.errors import InvalidInputError, ProviderUnavailableError
from kestrel.retrieval import HybridRetriever, open_retriever
from kestrel.store import SqliteVecIndex, Store

ALPHA = SearchScope(collections=("alpha",))
BETA = SearchScope(collections=("beta",))


def _bot_docs(store: Store, embedder: FakeEmbedder, collection: str) -> Built:
    """A guide with similar wording in each collection."""
    built = build(
        collection,
        "guide.md",
        [
            Prose(("Disability", "Option B"), f"Option B waiting period is six months ({collection})."),
            Prose(("Funeral",), f"Funeral benefit for a spouse ({collection})."),
            Table(("Bands",), ("Age", "Cover"), [("18-30", "R50 000"), ("31-50", "R40 000")]),
        ],
    )
    save(store, built, embedder=embedder)
    return built


@pytest.fixture
def two_bots(store: Store, embedder: FakeEmbedder) -> tuple[Built, Built]:
    return _bot_docs(store, embedder, "alpha"), _bot_docs(store, embedder, "beta")


def _table_section(built: Built) -> str:
    return next(c.section_id for c in built.chunks if c.kind == "table")


def test_implements_protocol(store: Store, embedder: FakeEmbedder) -> None:
    assert isinstance(open_retriever(store, embedder), Retriever)


def test_search_fuses_and_ranks(store: Store, embedder: FakeEmbedder, two_bots: tuple[Built, Built]) -> None:
    result = open_retriever(store, embedder).search("Option B waiting period", ALPHA, 3)
    assert result.warnings == ()
    assert result.scope == ALPHA
    assert len(result.hits) <= 3
    assert "Option B" in result.hits[0].text
    assert [h.rank for h in result.hits] == list(range(1, len(result.hits) + 1))
    assert [h.score for h in result.hits] == sorted((h.score for h in result.hits), reverse=True)
    assert len({h.chunk_id for h in result.hits}) == len(result.hits)


def test_rrf_prefers_chunks_found_by_both_rankers(store: Store, embedder: FakeEmbedder) -> None:
    built = build("alpha", "a.md", [Prose(("H",), "alpha beta"), Prose(("I",), "alpha gamma"), Prose(("J",), "delta")])
    save(store, built, embedder=embedder)
    retriever = HybridRetriever(store.documents, store.vectors, embedder)
    hits = retriever.search("alpha beta", ALPHA, 5).hits
    assert hits[0].chunk_id == built.chunks[0].id
    assert hits[0].score == pytest.approx(2 / 61)  # rank 1 in both rankers


def test_collection_isolation_through_every_path(
    store: Store, embedder: FakeEmbedder, two_bots: tuple[Built, Built]
) -> None:
    alpha, beta = two_bots
    retriever = open_retriever(store, embedder)
    for query in ("Option B waiting period", "funeral spouse", "18-30 cover R50 000", "beta", "alpha"):
        assert {h.document_id for h in store.documents.keyword_search(query, ALPHA, 50)} <= {alpha.id}
        assert {h.document_id for h in retriever.search(query, ALPHA, 50).hits} == {alpha.id}
        assert {h.document_id for h in retriever.search(query, BETA, 50).hits} == {beta.id}
    query = embedder.embed_query("Option B waiting period")
    allowed = store.documents.eligible_document_ids(ALPHA)
    assert {m.chunk_id.split(":")[0] for m in store.vectors.search(query, allowed, 50)} == {alpha.id}
    assert store.documents.resolve_hits([c.id for c in beta.chunks], ALPHA) == []
    # Section and table lookups are scoped too.
    assert retriever.get_table(_table_section(beta), ALPHA) is None
    assert retriever.get_section(_table_section(beta), ALPHA) is None
    assert retriever.get_section(beta.chunks[0].section_id, ALPHA) is None
    table = retriever.get_table(_table_section(alpha), ALPHA)
    assert table is not None and [r.row_number for r in table.rows] == [1, 2]
    assert retriever.get_section(alpha.chunks[0].section_id, ALPHA) is not None
    assert retriever.get_table(alpha.chunks[0].section_id, ALPHA) is None  # prose section has no table
    assert retriever.get_table("unknown:s000", ALPHA) is None


def test_editions_are_never_mixed(store: Store, embedder: FakeEmbedder) -> None:
    ids = {}
    for edition, status in [("2025", DocumentStatus.SUPERSEDED), ("2026", DocumentStatus.CURRENT)]:
        built = build("alpha", f"g{edition}.md", [Prose(("Waiting",), f"The waiting period in edition {edition}.")])
        save(store, built, status=status, embedder=embedder,
             manifest=entry(built, family="guide", edition=edition, effective_from=date(int(edition), 1, 1)))
        ids[edition] = built.id
    for status in (DocumentStatus.BLOCKED, DocumentStatus.PENDING):
        bad = build("alpha", f"{status}.md", [Prose(("Waiting",), "The waiting period, withdrawn text.")])
        save(store, bad, status=status, embedder=embedder)
    retriever = open_retriever(store, embedder)

    def found(scope: SearchScope) -> set[str]:
        return {h.document_id for h in retriever.search("waiting period", scope, 20).hits}

    assert found(ALPHA) == {ids["2026"]}
    assert found(SearchScope(collections=("alpha",), edition="2025")) == {ids["2025"]}
    assert found(SearchScope(collections=("alpha",), effective_on=date(2025, 3, 1))) == {ids["2025"]}
    assert found(SearchScope(collections=("alpha",), effective_on=date(2026, 3, 1))) == {ids["2026"]}


def test_resolve_scope_uses_the_bots_editions(store: Store, embedder: FakeEmbedder) -> None:
    built = build("alpha", "g.md", [Prose(("H",), "text")])
    save(store, built, manifest=entry(built, family="guide", edition="2026", effective_from=date(2026, 1, 1)))
    retriever = open_retriever(store, embedder)
    knowledge = KnowledgeConfig(collections=("alpha",))
    decision = retriever.resolve_scope("Under the 2026 guide, what applies?", knowledge)
    assert decision.scope is not None and decision.scope.edition == "2026" and decision.explicit
    unknown = retriever.resolve_scope("Under the 2025 guide, what applies?", knowledge)
    assert unknown.clarification is not None and "2026" in unknown.clarification
    # Another collection's editions are not offered.
    assert retriever.resolve_scope("Under the 2026 guide?", KnowledgeConfig(collections=("beta",))).clarification


class _UnavailableIndex:
    available = False

    def upsert(self, items: Sequence[VectorItem]) -> None: ...
    def delete_for_document(self, document_id: str) -> None: ...
    def existing(self, chunk_ids: Collection[str]) -> set[str]:
        return set()

    def search(self, query: Sequence[float], allowed_document_ids: Collection[str], limit: int) -> list[VectorMatch]:
        raise AssertionError("must not be called when unavailable")


def test_keyword_only_with_a_warning_when_vectors_are_unavailable(
    store: Store, embedder: FakeEmbedder, two_bots: tuple[Built, Built], monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha, _ = two_bots
    result = HybridRetriever(store.documents, _UnavailableIndex(), embedder).search("Option B waiting", ALPHA, 5)
    assert result.hits and {h.document_id for h in result.hits} == {alpha.id}
    assert any("keyword-only" in w for w in result.warnings)

    # The real index, when the extension fails to load, behaves the same way.
    monkeypatch.setattr(store.connections, "vector_extension_available", lambda: False)
    result = open_retriever(store, embedder).search("Option B waiting", ALPHA, 5)
    assert result.hits and any("keyword-only" in w for w in result.warnings)

    assert HybridRetriever(store.documents, None, None).search("Option B", ALPHA, 5).warnings


class _BrokenEmbedder(FakeEmbedder):
    def embed_query(self, text: str) -> list[float]:
        raise ProviderUnavailableError("The embedding model could not be loaded.")


def test_embedder_failure_falls_back_to_keywords(store: Store, two_bots: tuple[Built, Built]) -> None:
    result = HybridRetriever(store.documents, store.vectors, _BrokenEmbedder()).search("Option B", ALPHA, 5)
    assert result.hits and any("could not be loaded" in w for w in result.warnings)


def test_model_mismatch_is_reported_and_keyword_search_continues(
    store: Store, embedder: FakeEmbedder, two_bots: tuple[Built, Built]
) -> None:
    other = FakeEmbedder(dimension=32)
    index = SqliteVecIndex(store.connections, model_name=other.model_name, dimension=other.dimension)
    result = HybridRetriever(store.documents, index, other).search("Option B", ALPHA, 5)
    assert result.hits
    assert any("Re-ingest" in w for w in result.warnings)


def test_empty_query_and_bad_limit(store: Store, embedder: FakeEmbedder, two_bots: tuple[Built, Built]) -> None:
    retriever = open_retriever(store, embedder)
    assert retriever.search("   ", ALPHA, 5).hits == ()
    retriever.search('"(*)" AND NEAR(', ALPHA, 5)  # FTS syntax never raises
    with pytest.raises(InvalidInputError):
        retriever.search("x", ALPHA, 0)


def test_vector_only_matches_are_returned(store: Store, embedder: FakeEmbedder) -> None:
    """A chunk sharing no keyword with the query can still come back through the vector ranker."""
    built = build("alpha", "a.md", [Prose(("H",), "zeta eta theta"), Prose(("I",), "unrelated words")])
    save(store, built, embedder=embedder)

    class Echo(FakeEmbedder):
        def embed_query(self, text: str) -> list[float]:
            return super().embed_query("zeta eta theta")

    hits = HybridRetriever(store.documents, store.vectors, Echo()).search("nothing in common", ALPHA, 5).hits
    assert hits and hits[0].chunk_id == built.chunks[0].id
