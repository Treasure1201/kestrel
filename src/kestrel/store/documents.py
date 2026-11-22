"""SQLite implementation of `DocumentRepository`."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime

from kestrel.contracts import (
    Chunk,
    DocumentRecord,
    DocumentStatus,
    Finding,
    ManifestEntry,
    ParsedDocument,
    SearchHit,
    SearchScope,
    SectionView,
    TableView,
    section_id,
)
from kestrel.errors import InvalidInputError
from kestrel.store.connection import ConnectionFactory
from kestrel.store.eligibility import Candidate, eligible
from kestrel.store.fts import match_expression
from kestrel.store.rows import (
    HEADING_SEPARATOR,
    HIT_COLUMNS,
    chunk_from_row,
    hit_from_row,
    record_from_row,
    section_rows,
    section_view_from_row,
    table_view_from_row,
)

# BM25 column weights for (text, heading). Headings are short and descriptive, so a
# heading match counts, but less than the same words in the body.
_BM25 = "bm25(chunks_fts, 1.0, 0.6)"

_RECORD_SQL = (
    "SELECT d.*,"
    " (SELECT count(*) FROM sections s WHERE s.document_id = d.id) AS section_count,"
    " (SELECT count(*) FROM findings f WHERE f.document_id = d.id) AS finding_count,"
    " (SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count,"
    " (SELECT count(*) FROM sections s WHERE s.document_id = d.id AND s.kind = 'table') AS table_count"
    " FROM documents d"
)


def _ids(values: Collection[str]) -> str:
    """A JSON array for `IN (SELECT value FROM json_each(?))`: one parameter, any length."""
    return json.dumps(sorted(set(values)))


class SqliteDocumentRepository:
    """Documents, sections, chunks, keyword index and findings in one SQLite file.

    Every method opens its own short-lived connection, so one instance may be used from
    several threads at once.
    """

    def __init__(self, connections: ConnectionFactory) -> None:
        self._connections = connections

    # ---- reads of documents ------------------------------------------------------

    def find_document(self, collection: str, filename: str) -> DocumentRecord | None:
        with self._connections.connect() as conn:
            row = conn.execute(f"{_RECORD_SQL} WHERE d.collection = ? AND d.filename = ?", (collection, filename)).fetchone()
        return record_from_row(row) if row else None

    def get_document(self, document_id: str) -> DocumentRecord | None:
        with self._connections.connect() as conn:
            row = conn.execute(f"{_RECORD_SQL} WHERE d.id = ?", (document_id,)).fetchone()
        return record_from_row(row) if row else None

    def list_documents(self, collections: Collection[str]) -> list[DocumentRecord]:
        with self._connections.connect() as conn:
            rows = conn.execute(
                f"{_RECORD_SQL} WHERE d.collection IN (SELECT value FROM json_each(?)) ORDER BY d.filename, d.collection",
                (_ids(collections),),
            ).fetchall()
        return [record_from_row(row) for row in rows]

    def list_chunks(self, document_id: str) -> list[Chunk]:
        with self._connections.connect() as conn:
            rows = conn.execute(
                "SELECT c.*, s.heading_path AS section_heading_path FROM chunks c JOIN sections s ON s.id = c.section_id"
                " WHERE c.document_id = ? ORDER BY s.ordinal, c.rowid",
                (document_id,),
            ).fetchall()
        return [chunk_from_row(row) for row in rows]

    def findings(self, document_ids: Collection[str]) -> list[Finding]:
        with self._connections.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM findings WHERE document_id IN (SELECT value FROM json_each(?)) ORDER BY document_id, id",
                (_ids(document_ids),),
            ).fetchall()
        return [
            Finding(
                document_id=row["document_id"],
                locator=row["locator"],
                kind=row["kind"],
                severity=row["severity"],
                detail=row["detail"],
                excerpt=row["excerpt"],
            )
            for row in rows
        ]

    # ---- writes ------------------------------------------------------------------

    def save_document(
        self,
        document: ParsedDocument,
        chunks: Sequence[Chunk],
        findings: Sequence[Finding],
        *,
        status: DocumentStatus,
        entry: ManifestEntry | None,
        status_reason: str | None = None,
    ) -> DocumentRecord:
        """Atomically store a document, replacing any earlier copy of the same file.

        When the stored copy already has identical sections and chunks, only the
        document's metadata and findings are brought up to date, so its vectors survive.
        When nothing at all differs, nothing is written.
        """
        doc_id = document.id
        _validate(document, chunks, findings)
        sections = section_rows(document)
        chunk_rows = [_chunk_row(chunk) for chunk in chunks]
        finding_rows = [(doc_id, f.locator, f.kind, f.severity, f.detail, f.excerpt) for f in findings]
        meta = _metadata(document, status, entry, status_reason)

        with self._connections.write() as conn:
            conn.execute("INSERT OR IGNORE INTO collections VALUES (?, ?)", (document.collection, _now()))
            previous = conn.execute(
                "SELECT * FROM documents WHERE (collection = ? AND filename = ?) OR id = ?",
                (document.collection, document.filename, doc_id),
            ).fetchall()
            same = [row for row in previous if row["id"] == doc_id and row["filename"] == document.filename]
            if len(previous) == 1 and same and _same_content(conn, doc_id, sections, chunk_rows):
                if _stored_metadata(same[0]) != meta or _stored_findings(conn, doc_id) != finding_rows:
                    conn.execute(
                        "UPDATE documents SET title = ?, media_type = ?, family = ?, edition = ?, effective_from = ?,"
                        " supersedes = ?, status = ?, status_reason = ?, approved_by = ?, ingested_at = ? WHERE id = ?",
                        (*meta, _now(), doc_id),
                    )
                    _replace_findings(conn, doc_id, finding_rows)
            else:
                for row in previous:
                    conn.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
                conn.execute(
                    "INSERT INTO documents (id, collection, sha256, filename, title, media_type, family, edition,"
                    " effective_from, supersedes, status, status_reason, approved_by, ingested_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (doc_id, document.collection, document.sha256, document.filename, *meta, _now()),
                )
                conn.executemany(
                    "INSERT INTO sections (id, document_id, ordinal, kind, heading_path, locator, text, table_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    sections,
                )
                conn.executemany(
                    "INSERT INTO chunks (id, collection, document_id, section_id, kind, row_index, text, heading, locator)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    chunk_rows,
                )
                _replace_findings(conn, doc_id, finding_rows)
            row = conn.execute(f"{_RECORD_SQL} WHERE d.id = ?", (doc_id,)).fetchone()
        return record_from_row(row)

    def set_status(self, document_id: str, status: DocumentStatus, *, reason: str | None = None) -> DocumentRecord:
        with self._connections.write() as conn:
            changed = conn.execute(
                "UPDATE documents SET status = ?, status_reason = ? WHERE id = ?",
                (DocumentStatus(status).value, reason, document_id),
            ).rowcount
            if not changed:
                raise InvalidInputError(f"No document has the id {document_id!r}.")
            row = conn.execute(f"{_RECORD_SQL} WHERE d.id = ?", (document_id,)).fetchone()
        return record_from_row(row)

    def delete_document(self, document_id: str) -> None:
        """Remove a document; sections, chunks, keyword rows, vectors and findings cascade. Unknown ids are ignored."""
        with self._connections.write() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    # ---- scope and search --------------------------------------------------------

    def eligible_document_ids(self, scope: SearchScope) -> list[str]:
        with self._connections.connect() as conn:
            return _eligible(conn, scope)

    def keyword_search(self, query: str, scope: SearchScope, limit: int) -> list[SearchHit]:
        expression = match_expression(query)
        if expression is None or limit < 1:
            return []
        with self._connections.read() as conn:
            allowed = _eligible(conn, scope)
            if not allowed:
                return []
            rows = conn.execute(
                f"SELECT {HIT_COLUMNS}, {_BM25} AS bm25 FROM chunks_fts"
                " JOIN chunks c ON c.rowid = chunks_fts.rowid JOIN documents d ON d.id = c.document_id"
                " JOIN sections s ON s.id = c.section_id"
                " WHERE chunks_fts MATCH ? AND c.document_id IN (SELECT value FROM json_each(?))"
                " ORDER BY bm25, c.id LIMIT ?",
                (expression, _ids(allowed), limit),
            ).fetchall()
        # bm25() is lower-is-better and negative; flip it so a higher score is better.
        return [hit_from_row(row, score=-float(row["bm25"]), rank=i) for i, row in enumerate(rows, start=1)]

    def resolve_hits(self, chunk_ids: Sequence[str], scope: SearchScope) -> list[SearchHit]:
        if not chunk_ids:
            return []
        with self._connections.read() as conn:
            allowed = _eligible(conn, scope)
            rows = conn.execute(
                f"SELECT {HIT_COLUMNS} FROM chunks c JOIN documents d ON d.id = c.document_id"
                " JOIN sections s ON s.id = c.section_id"
                " WHERE c.id IN (SELECT value FROM json_each(?)) AND c.document_id IN (SELECT value FROM json_each(?))",
                (_ids(chunk_ids), _ids(allowed)),
            ).fetchall()
        by_id = {row["chunk_id"]: row for row in rows}
        ordered = [by_id[cid] for cid in dict.fromkeys(chunk_ids) if cid in by_id]
        return [hit_from_row(row, score=0.0, rank=i) for i, row in enumerate(ordered, start=1)]

    def get_section(self, section_id: str) -> SectionView | None:
        with self._connections.connect() as conn:
            row = _section_row(conn, section_id)
        return section_view_from_row(row) if row else None

    def get_table(self, section_id: str) -> TableView | None:
        with self._connections.connect() as conn:
            row = _section_row(conn, section_id)
        return table_view_from_row(row) if row else None

    def list_sections(self, document_id: str) -> list[SectionView]:
        with self._connections.connect() as conn:
            rows = conn.execute(
                "SELECT s.*, d.filename, d.title, d.status, d.edition FROM sections s JOIN documents d ON d.id = s.document_id"
                " WHERE s.document_id = ? ORDER BY s.ordinal",
                (document_id,),
            ).fetchall()
        return [section_view_from_row(row) for row in rows]

    def section_document_id(self, section_id: str) -> str | None:
        """The document a section belongs to, or None for an unknown section."""
        with self._connections.connect() as conn:
            row = conn.execute("SELECT document_id FROM sections WHERE id = ?", (section_id,)).fetchone()
        return row["document_id"] if row else None


# ---- helpers -----------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _eligible(conn: sqlite3.Connection, scope: SearchScope) -> list[str]:
    rows = conn.execute(
        "SELECT id, collection, status, family, edition, effective_from FROM documents"
        " WHERE collection IN (SELECT value FROM json_each(?))",
        (_ids(scope.collections),),
    ).fetchall()
    candidates = [
        Candidate(
            id=row["id"],
            collection=row["collection"],
            status=DocumentStatus(row["status"]),
            family=row["family"],
            edition=row["edition"],
            effective_from=date.fromisoformat(row["effective_from"]) if row["effective_from"] else None,
        )
        for row in rows
    ]
    return eligible(candidates, scope)


def _section_row(conn: sqlite3.Connection, section_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT s.*, d.filename, d.title, d.status, d.edition FROM sections s JOIN documents d ON d.id = s.document_id"
        " WHERE s.id = ?",
        (section_id,),
    ).fetchone()
    return row


def _validate(document: ParsedDocument, chunks: Sequence[Chunk], findings: Sequence[Finding]) -> None:
    doc_id = document.id
    ordinals = [s.ordinal for s in document.sections]
    if len(set(ordinals)) != len(ordinals):
        raise InvalidInputError(f"{document.filename}: section ordinals repeat.")
    known_sections = {section_id(doc_id, o) for o in ordinals}
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.document_id != doc_id or chunk.collection != document.collection:
            raise InvalidInputError(f"{document.filename}: chunk {chunk.id} belongs to another document or collection.")
        if chunk.section_id not in known_sections:
            raise InvalidInputError(f"{document.filename}: chunk {chunk.id} names an unknown section {chunk.section_id}.")
        if (chunk.kind == "table_row") != (chunk.row_index is not None):
            raise InvalidInputError(f"{document.filename}: chunk {chunk.id} must have a row index only if it is a table row.")
        if chunk.id in seen:
            raise InvalidInputError(f"{document.filename}: chunk id {chunk.id} repeats.")
        seen.add(chunk.id)
    for finding in findings:
        if finding.document_id != doc_id:
            raise InvalidInputError(f"{document.filename}: a finding belongs to another document.")


def _metadata(
    document: ParsedDocument, status: DocumentStatus, entry: ManifestEntry | None, reason: str | None
) -> tuple[object, ...]:
    """(title, media_type, family, edition, effective_from, supersedes, status, status_reason, approved_by)."""
    return (
        document.title,
        document.media_type,
        entry.family if entry else None,
        entry.edition if entry else None,
        entry.effective_from.isoformat() if entry else None,
        entry.supersedes if entry else None,
        DocumentStatus(status).value,
        reason,
        entry.approved_by if entry else None,
    )


def _stored_metadata(row: sqlite3.Row) -> tuple[object, ...]:
    return tuple(
        row[name]
        for name in (
            "title", "media_type", "family", "edition", "effective_from",
            "supersedes", "status", "status_reason", "approved_by",
        )
    )


def _chunk_row(chunk: Chunk) -> tuple[object, ...]:
    return (
        chunk.id,
        chunk.collection,
        chunk.document_id,
        chunk.section_id,
        chunk.kind,
        chunk.row_index,
        chunk.text,
        HEADING_SEPARATOR.join(chunk.heading_path),
        chunk.locator,
    )


def _same_content(
    conn: sqlite3.Connection, doc_id: str, sections: list[tuple[object, ...]], chunk_rows: list[tuple[object, ...]]
) -> bool:
    stored_sections = [
        tuple(row)
        for row in conn.execute(
            "SELECT id, document_id, ordinal, kind, heading_path, locator, text, table_json FROM sections"
            " WHERE document_id = ? ORDER BY ordinal",
            (doc_id,),
        )
    ]
    if stored_sections != sorted(sections, key=lambda s: int(str(s[2]))):
        return False
    stored_chunks = [
        tuple(row)
        for row in conn.execute(
            "SELECT id, collection, document_id, section_id, kind, row_index, text, heading, locator FROM chunks"
            " WHERE document_id = ? ORDER BY rowid",
            (doc_id,),
        )
    ]
    return stored_chunks == chunk_rows


def _stored_findings(conn: sqlite3.Connection, doc_id: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT document_id, locator, kind, severity, detail, excerpt FROM findings WHERE document_id = ? ORDER BY id",
            (doc_id,),
        )
    ]


def _replace_findings(conn: sqlite3.Connection, doc_id: str, rows: list[tuple[object, ...]]) -> None:
    conn.execute("DELETE FROM findings WHERE document_id = ?", (doc_id,))
    conn.executemany(
        "INSERT INTO findings (document_id, locator, kind, severity, detail, excerpt) VALUES (?, ?, ?, ?, ?, ?)", rows
    )
