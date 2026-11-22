"""SQLite schema, migrations and connection setup.

One file holds documents, sections, chunks, the FTS5 keyword index, embeddings,
scanner findings and the audit log. Connections are opened in autocommit mode
(`isolation_level=None`); writers group statements with `transaction(conn)`, which
takes the write lock up front (`BEGIN IMMEDIATE`) so concurrent writers queue instead
of failing half-way.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2

# Keyword index tokenizer: `porter unicode61 remove_diacritics 2`.
# unicode61 folds case and diacritics and splits on punctuation, so amounts with
# spaces ("50 000"), single-letter labels and hyphenated words become ordinary tokens.
# The porter stemmer on top lets "periods" match "period" and "notified" match
# "notify", which matters because users phrase questions differently from the
# documents. Exact phrases still work through FTS5 phrase queries, and numbers are
# not stemmed.
_V1 = """
CREATE TABLE collections (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

CREATE TABLE documents (
    id              TEXT PRIMARY KEY,
    collection      TEXT NOT NULL REFERENCES collections(id),
    sha256          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    title           TEXT NOT NULL,
    media_type      TEXT NOT NULL,
    family          TEXT,
    edition         TEXT,
    effective_from  TEXT,
    supersedes      TEXT,
    status          TEXT NOT NULL CHECK (status IN ('current', 'superseded', 'pending', 'blocked')),
    status_reason   TEXT,
    approved_by     TEXT,
    ingested_at     TEXT NOT NULL,
    UNIQUE (collection, filename)
);
CREATE INDEX documents_scope ON documents (collection, status, family, edition);

CREATE TABLE sections (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal       INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('prose', 'table')),
    heading_path  TEXT NOT NULL DEFAULT '[]',   -- JSON array of headings
    locator       TEXT NOT NULL,
    text          TEXT NOT NULL,
    table_json    TEXT,                         -- JSON {"header": [...], "rows": [...]} for tables
    UNIQUE (document_id, ordinal)
);

CREATE TABLE chunks (
    id            TEXT PRIMARY KEY,
    collection    TEXT NOT NULL REFERENCES collections(id),
    document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id    TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('prose', 'table', 'table_row')),
    row_index     INTEGER,
    text          TEXT NOT NULL,
    heading       TEXT NOT NULL DEFAULT '',     -- heading path joined with " > ", indexed for search
    locator       TEXT NOT NULL,
    CHECK ((kind = 'table_row') = (row_index IS NOT NULL))
);
CREATE INDEX chunks_document ON chunks (document_id);
CREATE INDEX chunks_section ON chunks (section_id);

-- External-content FTS5 index kept in step with `chunks` by triggers, so a chunk
-- write and its index rows always commit in the same transaction.
CREATE VIRTUAL TABLE chunks_fts USING fts5 (
    text, heading,
    content = 'chunks', content_rowid = 'rowid',
    tokenize = 'porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts (rowid, text, heading) VALUES (new.rowid, new.text, new.heading);
END;
CREATE TRIGGER chunks_fts_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text, heading) VALUES ('delete', old.rowid, old.text, old.heading);
END;
CREATE TRIGGER chunks_fts_update AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text, heading) VALUES ('delete', old.rowid, old.text, old.heading);
    INSERT INTO chunks_fts (rowid, text, heading) VALUES (new.rowid, new.text, new.heading);
END;

-- Float32 embeddings for exact cosine search with sqlite-vec's vec_distance_cosine.
-- document_id and collection are stored beside each vector so the pre-ranking filter
-- is a plain indexed WHERE clause.
CREATE TABLE chunk_vectors (
    chunk_id     TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    collection   TEXT NOT NULL,
    model        TEXT NOT NULL,
    dimension    INTEGER NOT NULL CHECK (dimension > 0),
    embedding    BLOB NOT NULL CHECK (length(embedding) = 4 * dimension)
);
CREATE INDEX chunk_vectors_document ON chunk_vectors (document_id);

CREATE TABLE findings (
    id           INTEGER PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    locator      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'high')),
    detail       TEXT NOT NULL,
    excerpt      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX findings_document ON findings (document_id);

-- Append-only, hash-chained. hash = SHA-256 over prev_hash and the row's canonical
-- JSON. Rows are never updated; pruning deletes old rows and appends a checkpoint.
-- No foreign keys: the audit trail must outlive the documents it mentions.
CREATE TABLE audit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('ask', 'block', 'unblock', 'checkpoint')),
    trace_id         TEXT NOT NULL,
    bot_id           TEXT,
    channel          TEXT NOT NULL,
    user             TEXT NOT NULL,
    roles            TEXT NOT NULL DEFAULT '[]',
    question_text    TEXT,
    question_digest  TEXT,
    redactions       TEXT NOT NULL DEFAULT '[]',
    retrieved        TEXT NOT NULL DEFAULT '[]',  -- JSON array of chunk ids
    citations        TEXT NOT NULL DEFAULT '[]',  -- JSON array of AuditCitation
    calculations     TEXT NOT NULL DEFAULT '[]',
    outcome          TEXT,
    error_category   TEXT,
    model            TEXT,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL,
    latency_ms       INTEGER NOT NULL DEFAULT 0,
    document_id      TEXT,
    detail           TEXT,
    prev_hash        TEXT NOT NULL,
    hash             TEXT NOT NULL UNIQUE
);
CREATE INDEX audit_bot_time ON audit (bot_id, created_at);
CREATE INDEX audit_trace ON audit (trace_id);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit BEGIN
    SELECT RAISE(ABORT, 'audit records are append-only');
END;

-- Which documents each answer cited, for "which answers cited this document".
CREATE TABLE audit_documents (
    audit_id     INTEGER NOT NULL REFERENCES audit(id) ON DELETE CASCADE,
    document_id  TEXT NOT NULL,
    PRIMARY KEY (audit_id, document_id)
);
CREATE INDEX audit_documents_document ON audit_documents (document_id);

CREATE TABLE meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

# Schema 2: why a question was not answered. Added columns only, so an existing
# database is upgraded in place and its rows keep their hashes: an audit row hashes
# these fields only when they hold a value (see `store.audit.record_hash`).
_AUDIT_REASONS = """
ALTER TABLE audit ADD COLUMN reason TEXT;
ALTER TABLE audit ADD COLUMN failed_checks TEXT NOT NULL DEFAULT '[]';
ALTER TABLE audit ADD COLUMN closest TEXT;
"""

MIGRATIONS: tuple[tuple[int, str], ...] = ((1, _V1), (2, _AUDIT_REASONS))
"""(version, DDL) in order. Append new versions; never edit an applied one."""


def open_database(path: Path | str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the database at `path`.

    The connection uses WAL journaling (concurrent readers alongside one writer),
    enforces foreign keys, waits up to 10 s for a busy lock, and returns rows as
    `sqlite3.Row`. Pass `":memory:"` for a private in-memory database.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied schema version, or 0 for an empty database."""
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'").fetchone()
    if not exists:
        return 0
    row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every pending migration in one transaction. Safe to call repeatedly and concurrently.

    The version is re-read after taking the write lock, so two processes opening a new
    database at once apply the DDL exactly once. Returns the schema version after
    migrating. Raises `RuntimeError` if the database is newer than this code.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    if schema_version(conn) == SCHEMA_VERSION:
        return SCHEMA_VERSION
    with transaction(conn):
        current = schema_version(conn)
        if current > SCHEMA_VERSION:
            raise RuntimeError(f"database schema version {current} is newer than this code ({SCHEMA_VERSION})")
        for version, ddl in MIGRATIONS:
            if version > current:
                for statement in _statements(ddl):
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_version VALUES (?, ?)", (version, datetime.now(UTC).isoformat()))
    return schema_version(conn)


def _statements(script: str) -> Iterator[str]:
    """Split a DDL script into complete statements (trigger bodies stay whole)."""
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                yield pending.strip()
            pending = ""
    if pending.strip():
        raise ValueError(f"incomplete SQL statement: {pending.strip()[:80]}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one write transaction: commit on success, roll back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def load_vector_extension(conn: sqlite3.Connection) -> bool:
    """Load sqlite-vec into `conn`, then disable extension loading again.

    Returns False instead of raising when this Python build cannot load extensions or
    the bundled binary fails to load; callers then fall back to keyword-only search.
    """
    try:
        import sqlite_vec
    except ImportError:
        return False
    try:
        conn.enable_load_extension(True)
    except (AttributeError, sqlite3.Error):
        return False
    try:
        sqlite_vec.load(conn)
        conn.execute("SELECT vec_version()").fetchone()
    except (sqlite3.Error, OSError):
        return False
    finally:
        conn.enable_load_extension(False)
    return True
