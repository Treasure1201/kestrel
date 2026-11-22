"""sqlite-vec index: storage, filter-before-rank, and embedding model changes."""

from __future__ import annotations

from pathlib import Path

import pytest
from docbuilders import Prose, build, make_store, save
from fakes import FakeEmbedder

from kestrel.contracts import VectorItem
from kestrel.errors import ConfigurationError, InvalidInputError, ProviderUnavailableError
from kestrel.store import SqliteVecIndex, Store


def test_extension_is_available_here(store: Store) -> None:
    assert store.vectors.available is True


def test_upsert_existing_and_delete(store: Store, embedder: FakeEmbedder) -> None:
    built = build("alpha", "a.md", [Prose(("H",), "waiting period"), Prose(("I",), "funeral benefit")])
    save(store, built, embedder=embedder)
    ids = [c.id for c in built.chunks]
    assert store.vectors.existing(ids + ["missing"]) == set(ids)
    assert store.vectors.existing([]) == set()
    # Upserting again replaces rather than duplicates.
    save(store, built, embedder=embedder)
    with store.connections.connect() as conn:
        assert conn.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == 2
    store.vectors.delete_for_document(built.id)
    assert store.vectors.existing(ids) == set()


def test_search_returns_closest_first(store: Store, embedder: FakeEmbedder) -> None:
    built = build("alpha", "a.md", [Prose(("H",), "waiting period of six months"), Prose(("I",), "funeral benefit for a child")])
    save(store, built, embedder=embedder)
    matches = store.vectors.search(embedder.embed_query("child funeral benefit"), [built.id], 5)
    assert [m.chunk_id for m in matches] == [built.chunks[1].id, built.chunks[0].id]
    assert matches[0].distance < matches[1].distance
    assert store.vectors.search(embedder.embed_query("x"), [], 5) == []


def test_filter_is_applied_before_ranking(store: Store, embedder: FakeEmbedder) -> None:
    """Many near-identical vectors in a disallowed document must not crowd out allowed ones."""
    query_text = "option b waiting period"
    noise = build("alpha", "noise.md", [Prose(("N", str(i)), f"{query_text} copy {i}") for i in range(60)])
    allowed = build("alpha", "allowed.md", [Prose(("A",), "the waiting period for disability cover")])
    save(store, noise, embedder=embedder)
    save(store, allowed, embedder=embedder)

    query = embedder.embed_query(query_text)
    everything = store.vectors.search(query, [noise.id, allowed.id], 5)
    assert all(m.chunk_id.startswith(noise.id) for m in everything)  # noise wins a global top-5

    only_allowed = store.vectors.search(query, [allowed.id], 5)
    assert [m.chunk_id for m in only_allowed] == [allowed.chunks[0].id]


def test_wrong_dimension_is_rejected(store: Store, embedder: FakeEmbedder) -> None:
    built = build("alpha", "a.md", [Prose(("H",), "text")])
    save(store, built)
    with pytest.raises(InvalidInputError):
        store.vectors.upsert([VectorItem(chunk_id=built.chunks[0].id, document_id=built.id, collection="alpha",
                                         embedding=(0.1, 0.2))])
    with pytest.raises(InvalidInputError):
        store.vectors.search([0.1, 0.2], [built.id], 3)


def test_embedding_model_change_is_detected(tmp_path: Path, embedder: FakeEmbedder) -> None:
    path = tmp_path / "k.sqlite"
    first = make_store(path, embedder)
    built = build("alpha", "a.md", [Prose(("H",), "waiting period")])
    save(first, built, embedder=embedder)

    for other in (FakeEmbedder(dimension=32), FakeEmbedder(dimension=64)):
        name = "another-model" if other.dimension == 64 else other.model_name
        index = SqliteVecIndex(first.connections, model_name=name, dimension=other.dimension)
        with pytest.raises(ConfigurationError, match="Re-ingest"):
            index.check_model()
        with pytest.raises(ConfigurationError, match="Re-ingest"):
            index.search(other.embed_query("waiting"), [built.id], 3)
        with pytest.raises(ConfigurationError, match="Re-ingest"):
            index.upsert([VectorItem(chunk_id=built.chunks[0].id, document_id=built.id, collection="alpha",
                                     embedding=tuple(other.embed_query("waiting")))])
        # Vectors of another model never count as present, so the next ingest re-embeds.
        assert index.existing([built.chunks[0].id]) == set()

    assert first.vectors.stored_models() == [(embedder.model_name, embedder.dimension)]
    first.vectors.check_model()  # the original model is fine
    # After clearing, the new model can be used.
    index = SqliteVecIndex(first.connections, model_name="another-model", dimension=64)
    assert index.clear() == 1
    index.check_model()


def test_unavailable_extension_reports_itself(store: Store, embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch) -> None:
    built = build("alpha", "a.md", [Prose(("H",), "waiting period")])
    monkeypatch.setattr(store.connections, "vector_extension_available", lambda: False)
    assert store.vectors.available is False
    save(store, built, embedder=embedder)  # storing still works without the extension
    with pytest.raises(ProviderUnavailableError):
        store.vectors.search(embedder.embed_query("waiting"), [built.id], 3)


def test_non_finite_values_are_rejected(store: Store) -> None:
    built = build("alpha", "a.md", [Prose(("H",), "text")])
    save(store, built)
    bad = (float("nan"),) + (0.0,) * 63
    with pytest.raises(InvalidInputError):
        store.vectors.upsert([VectorItem(chunk_id=built.chunks[0].id, document_id=built.id, collection="alpha", embedding=bad)])
