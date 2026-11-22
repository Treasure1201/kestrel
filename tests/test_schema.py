"""Schema migration, constraints and the append-only audit table."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kestrel.store.schema import SCHEMA_VERSION, migrate, open_database, schema_version, transaction

NOW = "2026-01-01T00:00:00+00:00"


def _add_document(conn: sqlite3.Connection, doc_id: str = "d1", collection: str = "alpha") -> None:
    conn.execute("INSERT OR IGNORE INTO collections VALUES (?, ?)", (collection, NOW))
    conn.execute(
        "INSERT INTO documents (id, collection, sha256, filename, title, media_type, status, ingested_at)"
        " VALUES (?, ?, ?, ?, 'Title', 'text/markdown', 'current', ?)",
        (doc_id, collection, "0" * 64, f"{doc_id}.md", NOW),
    )
    conn.execute(
        "INSERT INTO sections (id, document_id, ordinal, kind, locator, text) VALUES (?, ?, 0, 'prose', 'loc', 'text')",
        (f"{doc_id}:s000", doc_id),
    )
    conn.execute(
        "INSERT INTO chunks (id, collection, document_id, section_id, kind, text, locator)"
        " VALUES (?, ?, ?, ?, 'prose', 'waiting period of three months', 'loc')",
        (f"{doc_id}:s000:p00", collection, doc_id, f"{doc_id}:s000"),
    )


def test_open_database_migrates_to_current_version(tmp_path: Path) -> None:
    conn = open_database(tmp_path / "a.sqlite")
    assert schema_version(conn) == SCHEMA_VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "b.sqlite"
    conn = open_database(path)
    _add_document(conn)
    assert migrate(conn) == SCHEMA_VERSION
    conn.close()
    again = open_database(path)
    assert again.execute("SELECT count(*) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert again.execute("SELECT count(*) FROM documents").fetchone()[0] == 1


def test_newer_database_is_refused(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO schema_version VALUES (?, ?)", (SCHEMA_VERSION + 1, NOW))
    with pytest.raises(RuntimeError, match="newer"):
        migrate(db)


def test_foreign_keys_are_enforced(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO documents (id, collection, sha256, filename, title, media_type, status, ingested_at)"
            " VALUES ('d9', 'no-such-collection', ?, 'x.md', 'T', 'text/markdown', 'current', ?)",
            ("0" * 64, NOW),
        )


def test_deleting_a_document_cascades_and_clears_the_keyword_index(db: sqlite3.Connection) -> None:
    _add_document(db)
    assert db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'waiting'").fetchone()[0] == 1
    db.execute("DELETE FROM documents WHERE id = 'd1'")
    assert db.execute("SELECT count(*) FROM sections").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'waiting'").fetchone()[0] == 0


def test_status_and_kind_are_constrained(db: sqlite3.Connection) -> None:
    _add_document(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE documents SET status = 'approved' WHERE id = 'd1'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO chunks (id, collection, document_id, section_id, kind, text, locator)"
            " VALUES ('x', 'alpha', 'd1', 'd1:s000', 'table_row', 'row without index', 'loc')"
        )


def test_audit_rows_cannot_be_updated(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO audit (created_at, kind, trace_id, channel, user, prev_hash, hash)"
        " VALUES (?, 'ask', 't1', 'cli', 'u', 'genesis', 'h1')",
        (NOW,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE audit SET outcome = 'answered'")


def test_transaction_rolls_back_on_error(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError), transaction(db):
        db.execute("INSERT INTO collections VALUES ('beta', ?)", (NOW,))
        raise ValueError("boom")
    assert db.execute("SELECT count(*) FROM collections").fetchone()[0] == 0
