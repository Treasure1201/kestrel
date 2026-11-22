"""Saving, replacing, deleting and reading documents."""

from __future__ import annotations

from datetime import date

import pytest
from docbuilders import Built, Prose, Table, build, entry, finding, save
from fakes import FakeEmbedder

from kestrel.contracts import DocumentRepository, DocumentStatus, SearchScope, VectorIndex
from kestrel.errors import ConfigurationError, InvalidInputError
from kestrel.store import ConnectionFactory, Store

SCOPE = SearchScope(collections=("alpha",))


def _guide(text: str = "The waiting period is three months.") -> Built:
    return build(
        "alpha",
        "guide.md",
        [
            Prose(("Benefits", "Waiting periods"), text),
            Table(("Benefits", "Bands"), ("Age", "Cover"), [("18-30", "R50 000"), ("31-50", "R40 000")]),
        ],
    )


def _count(store: Store, sql: str, *params: object) -> int:
    with store.connections.connect() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def test_implements_protocols(store: Store) -> None:
    assert isinstance(store.documents, DocumentRepository)
    assert isinstance(store.vectors, VectorIndex)


def test_in_memory_database_is_refused() -> None:
    with pytest.raises(ConfigurationError):
        ConnectionFactory(":memory:")


def test_save_round_trips_document_sections_chunks_and_findings(store: Store) -> None:
    built = _guide()
    manifest = entry(built, family="guide", edition="2026", effective_from=date(2026, 1, 1), supersedes="2025")
    record = save(store, built, manifest=manifest, findings=[finding(built)])

    assert record.id == built.id
    assert (record.family, record.edition, record.effective_from, record.supersedes) == (
        "guide", "2026", date(2026, 1, 1), "2025",
    )
    assert record.status is DocumentStatus.CURRENT
    assert (record.section_count, record.finding_count) == (2, 1)
    assert store.documents.find_document("alpha", "guide.md") == record
    assert store.documents.get_document(built.id) == record
    assert store.documents.find_document("alpha", "other.md") is None
    assert store.documents.list_chunks(built.id) == built.chunks
    assert [f.detail for f in store.documents.findings([built.id])] == ["white text on white page"]


def test_saving_the_same_document_twice_changes_nothing(store: Store) -> None:
    built = _guide()
    first = save(store, built, findings=[finding(built)])
    second = save(store, built, findings=[finding(built)])
    assert second == first  # same ingested_at: no write happened
    assert _count(store, "SELECT count(*) FROM chunks") == len(built.chunks)
    assert _count(store, "SELECT count(*) FROM findings") == 1


def test_metadata_change_keeps_chunks_and_vectors(store: Store, embedder: FakeEmbedder) -> None:
    built = _guide()
    save(store, built, embedder=embedder)
    record = save(store, built, status=DocumentStatus.SUPERSEDED, findings=[finding(built)])
    assert record.status is DocumentStatus.SUPERSEDED
    assert record.finding_count == 1
    assert store.vectors.existing([c.id for c in built.chunks]) == {c.id for c in built.chunks}


def test_changed_file_replaces_the_previous_copy_and_its_index_rows(store: Store, embedder: FakeEmbedder) -> None:
    old = _guide("The waiting period is three months.")
    save(store, old, embedder=embedder, findings=[finding(old)])
    new = _guide("The waiting period is six weeks.")
    assert new.id != old.id
    save(store, new)

    assert store.documents.get_document(old.id) is None
    assert [d.id for d in store.documents.list_documents(["alpha"])] == [new.id]
    assert store.documents.findings([old.id]) == []
    assert store.vectors.existing([c.id for c in old.chunks]) == set()
    assert store.documents.keyword_search("three months", SCOPE, 5) == []
    assert [h.document_id for h in store.documents.keyword_search("six weeks", SCOPE, 5)] == [new.id]


def test_delete_cascades_everything(store: Store, embedder: FakeEmbedder) -> None:
    built = _guide()
    save(store, built, embedder=embedder, findings=[finding(built)])
    store.documents.delete_document(built.id)
    for table in ("documents", "sections", "chunks", "chunk_vectors", "findings"):
        assert _count(store, f"SELECT count(*) FROM {table}") == 0, table
    assert _count(store, "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'waiting'") == 0
    store.documents.delete_document(built.id)  # unknown ids are ignored


def test_fts_stays_in_sync_with_chunks(store: Store) -> None:
    built = _guide()
    save(store, built)
    assert _count(store, "SELECT count(*) FROM chunks_fts") == len(built.chunks)
    # The external-content index must agree with its content table.
    with store.connections.connect() as conn:
        conn.execute("INSERT INTO chunks_fts (chunks_fts, rank) VALUES ('integrity-check', 1)")


def test_set_status(store: Store) -> None:
    built = _guide()
    save(store, built)
    record = store.documents.set_status(built.id, DocumentStatus.BLOCKED, reason="withdrawn by operator")
    assert (record.status, record.status_reason) == (DocumentStatus.BLOCKED, "withdrawn by operator")
    with pytest.raises(InvalidInputError):
        store.documents.set_status("no-such-id", DocumentStatus.CURRENT)


def test_list_documents_filters_by_collection_and_orders_by_filename(store: Store) -> None:
    for collection, name in [("alpha", "b.md"), ("alpha", "a.md"), ("beta", "c.md")]:
        save(store, build(collection, name, [Prose(("H",), f"text of {name}")]))
    assert [d.filename for d in store.documents.list_documents(["alpha"])] == ["a.md", "b.md"]
    assert [d.filename for d in store.documents.list_documents(["alpha", "beta"])] == ["a.md", "b.md", "c.md"]
    assert store.documents.list_documents([]) == []


def test_get_section_and_get_table(store: Store) -> None:
    built = _guide()
    save(store, built)
    prose_id, table_id = (s for s in dict.fromkeys(c.section_id for c in built.chunks))

    prose = store.documents.get_section(prose_id)
    assert prose is not None and prose.table is None and prose.heading_path == ("Benefits", "Waiting periods")
    assert store.documents.get_table(prose_id) is None

    table = store.documents.get_table(table_id)
    assert table is not None
    assert table.header == ("Age", "Cover")
    assert [(r.row_number, r.cells) for r in table.rows] == [(1, ("18-30", "R50 000")), (2, ("31-50", "R40 000"))]
    assert table.rows[1].as_text(table.header) == "Age: 31-50; Cover: R40 000"
    assert store.documents.get_table(table_id) == table  # stable numbering
    section = store.documents.get_section(table_id)
    assert section is not None and section.table == table
    assert store.documents.get_section("missing:s000") is None


def test_invalid_chunks_are_rejected_before_writing(store: Store) -> None:
    built = _guide()
    foreign = build("beta", "x.md", [Prose(("H",), "other")]).chunks
    with pytest.raises(InvalidInputError):
        store.documents.save_document(built.document, foreign, [], status=DocumentStatus.CURRENT, entry=None)
    with pytest.raises(InvalidInputError):
        store.documents.save_document(
            built.document, built.chunks + built.chunks[:1], [], status=DocumentStatus.CURRENT, entry=None
        )
    assert store.documents.list_documents(["alpha"]) == []


def test_a_failed_save_leaves_the_previous_copy_intact(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _guide()
    save(store, old)
    new = _guide("A different text.")
    import kestrel.store.documents as documents_module

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(documents_module, "_replace_findings", boom)
    with pytest.raises(RuntimeError):
        store.documents.save_document(new.document, new.chunks, [], status=DocumentStatus.CURRENT, entry=None)
    assert [d.id for d in store.documents.list_documents(["alpha"])] == [old.id]
    assert len(store.documents.list_chunks(old.id)) == len(old.chunks)
