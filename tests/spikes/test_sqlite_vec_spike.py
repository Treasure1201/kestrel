"""Spike: sqlite-vec loads on this Python, and exact filtered cosine search plus FTS5 BM25 work.

Importing the package is not enough; this loads the bundled binary into a real
connection, turns extension loading off again, and runs the queries the vector index
and keyword search will use.
"""

from __future__ import annotations

import sqlite3

import pytest
import sqlite_vec
from fakes import FakeEmbedder

from kestrel.store.schema import load_vector_extension

pytestmark = pytest.mark.spike

NOW = "2026-01-01T00:00:00+00:00"
TEXTS = {
    # chunk id: (document id, text)
    "a1": ("doc-a", "Option B waiting period is three months"),
    "a2": ("doc-a", "Funeral cover for a child aged six to thirteen"),
    "b1": ("doc-b", "Option B waiting period is six months in the old guide"),
    "c1": ("doc-c", "Claims must be notified within six months of death"),
}


def _seed(conn: sqlite3.Connection, embedder: FakeEmbedder) -> None:
    conn.execute("INSERT INTO collections VALUES ('alpha', ?)", (NOW,))
    for doc in sorted({doc for doc, _ in TEXTS.values()}):
        conn.execute(
            "INSERT INTO documents (id, collection, sha256, filename, title, media_type, status, ingested_at)"
            " VALUES (?, 'alpha', ?, ?, 'T', 'text/markdown', 'current', ?)",
            (doc, "0" * 64, f"{doc}.md", NOW),
        )
        conn.execute(
            "INSERT INTO sections (id, document_id, ordinal, kind, locator, text) VALUES (?, ?, 0, 'prose', 'l', 't')",
            (f"{doc}:s000", doc),
        )
    for chunk, (doc, text) in TEXTS.items():
        conn.execute(
            "INSERT INTO chunks (id, collection, document_id, section_id, kind, text, heading, locator)"
            " VALUES (?, 'alpha', ?, ?, 'prose', ?, 'Benefits', 'l')",
            (chunk, doc, f"{doc}:s000", text),
        )
        conn.execute(
            "INSERT INTO chunk_vectors VALUES (?, ?, 'alpha', ?, ?, ?)",
            (chunk, doc, embedder.model_name, embedder.dimension, sqlite_vec.serialize_float32(embedder.embed_query(text))),
        )


def test_extension_loads_and_loading_is_disabled_afterwards(db: sqlite3.Connection) -> None:
    assert load_vector_extension(db) is True
    assert db.execute("SELECT vec_version()").fetchone()[0].startswith("v")
    # Loading is switched off again: a further load attempt through SQL must fail.
    with pytest.raises(sqlite3.OperationalError):
        db.execute("SELECT load_extension(?)", (sqlite_vec.loadable_path(),)).fetchone()


def test_filtered_cosine_search_ranks_only_allowed_documents(db: sqlite3.Connection) -> None:
    embedder = FakeEmbedder()
    assert load_vector_extension(db)
    _seed(db, embedder)
    query = sqlite_vec.serialize_float32(embedder.embed_query("What is the Option B waiting period?"))
    sql = (
        "SELECT chunk_id, vec_distance_cosine(embedding, ?) AS distance FROM chunk_vectors"
        " WHERE document_id IN (SELECT value FROM json_each(?)) ORDER BY distance, chunk_id LIMIT ?"
    )
    everything = db.execute(sql, (query, '["doc-a", "doc-b", "doc-c"]', 10)).fetchall()
    assert {row["chunk_id"] for row in everything[:2]} == {"a1", "b1"}

    only_a = db.execute(sql, (query, '["doc-a"]', 10)).fetchall()
    assert [row["chunk_id"] for row in only_a] == ["a1", "a2"]
    assert only_a[0]["distance"] < only_a[1]["distance"]

    assert db.execute(sql, (query, "[]", 10)).fetchall() == []


def test_fts5_bm25_query_with_scope_filter(db: sqlite3.Connection) -> None:
    _seed(db, FakeEmbedder())
    rows = db.execute(
        "SELECT c.id, bm25(chunks_fts) AS score FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid"
        " WHERE chunks_fts MATCH ? AND c.document_id IN (SELECT value FROM json_each(?)) ORDER BY score LIMIT 5",
        ('"waiting" AND "periods"', '["doc-a", "doc-c"]'),
    ).fetchall()
    # Porter stemming matches "periods" to "period"; doc-b is filtered out before ranking.
    assert [row["id"] for row in rows] == ["a1"]
    heading_hits = db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'heading:benefit'").fetchone()[0]
    assert heading_hits == len(TEXTS)
