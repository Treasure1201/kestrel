"""Schema version 2: an existing database is upgraded in place and its audit chain still verifies.

Rows written by the first schema hash an entry without `reason`, `failed_checks` and
`closest`. After the upgrade they must verify unchanged, and new rows that carry those
fields must be covered by their hash.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from kestrel.contracts import AuditEntry, Outcome
from kestrel.store import ConnectionFactory, SqliteAuditLog
from kestrel.store.audit import GENESIS, canonical_json
from kestrel.store.schema import MIGRATIONS, SCHEMA_VERSION, _statements, open_database, schema_version

FIRST_SCHEMA = MIGRATIONS[0][1]

FIRST_ENTRY_KEYS = (
    "kind", "trace_id", "bot_id", "channel", "user", "roles", "question_text", "question_digest", "redactions",
    "retrieved_chunk_ids", "citations", "calculations", "outcome", "error_category", "model", "input_tokens",
    "output_tokens", "cost_usd", "latency_ms", "document_id", "detail",
)


def _first_schema_database(path: Path) -> None:
    """A database exactly as the first schema created it, with two audit rows hashed the old way."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for statement in _statements(FIRST_SCHEMA):
        conn.execute(statement)
    conn.execute("INSERT INTO schema_version VALUES (1, '2026-11-01T00:00:00+00:00')")
    prev = GENESIS
    for record_id, outcome in ((1, "answered"), (2, "not_found")):
        entry = {key: None for key in FIRST_ENTRY_KEYS}
        entry.update(kind="ask", trace_id=f"old-{record_id}", bot_id="bot-a", channel="cli", user="u", roles=["agent"],
                     question_text="Is dental cover offered?", question_digest="d" * 64, redactions=[],
                     retrieved_chunk_ids=[], citations=[], calculations=[], outcome=outcome, input_tokens=0,
                     output_tokens=0, latency_ms=10)
        created = f"2026-11-01T00:0{record_id}:00.000000+00:00"
        body = canonical_json({"id": record_id, "created_at": created, "entry": entry})
        digest = hashlib.sha256(f"{prev}\n{body}".encode()).hexdigest()
        conn.execute(
            "INSERT INTO audit (id, created_at, kind, trace_id, bot_id, channel, user, roles, question_text, question_digest,"
            " outcome, latency_ms, prev_hash, hash) VALUES (?, ?, 'ask', ?, 'bot-a', 'cli', 'u', ?, ?, ?, ?, 10, ?, ?)",
            (record_id, created, f"old-{record_id}", json.dumps(["agent"]), entry["question_text"], "d" * 64, outcome, prev, digest),
        )
        prev = digest
    conn.close()


def test_existing_database_is_upgraded_in_place_and_old_rows_still_verify(tmp_path: Path) -> None:
    path = tmp_path / "kestrel.sqlite"
    _first_schema_database(path)
    conn = open_database(path)
    assert schema_version(conn) == SCHEMA_VERSION == 2
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit)")}
    assert {"reason", "failed_checks", "closest"} <= columns
    conn.close()

    log = SqliteAuditLog(ConnectionFactory(path), clock=lambda: datetime(2026, 11, 23, tzinfo=UTC))
    assert log.verify().ok and log.verify().checked == 2
    old = log.tail(5)
    assert [r.entry.trace_id for r in old] == ["old-2", "old-1"]
    assert old[0].entry.reason is None and old[0].entry.failed_checks == () and old[0].entry.closest is None

    log.append(AuditEntry(trace_id="new", bot_id="bot-a", channel="ui", user="u", outcome=Outcome.WITHHELD,
                          reason="The evidence checks did not pass after one repair attempt.",
                          failed_checks=("numbers_grounded", "quote_verbatim"), closest="guide.docx > section 5"))
    result = log.verify()
    assert result.ok and result.checked == 3
    newest = log.tail(1)[0].entry
    assert newest.failed_checks == ("numbers_grounded", "quote_verbatim")
    assert newest.closest == "guide.docx > section 5"


def _tamper(path: Path, sql: str, *params: object) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("DROP TRIGGER IF EXISTS audit_no_update")
    conn.execute(sql, params)
    conn.close()


def test_new_fields_are_covered_by_the_hash(tmp_path: Path) -> None:
    path = tmp_path / "kestrel.sqlite"
    _first_schema_database(path)
    log = SqliteAuditLog(ConnectionFactory(path))
    receipt = log.append(AuditEntry(trace_id="n", channel="cli", user="u", outcome=Outcome.NOT_FOUND,
                                    reason="The documents do not say.", failed_checks=(), closest="faq.docx > section 2"))
    assert log.verify().ok
    for column, value in (("reason", "Something else."), ("closest", "other.docx > section 9"), ("failed_checks", '["schema"]')):
        _tamper(path, f"UPDATE audit SET {column} = ? WHERE id = ?", value, receipt.record_id)
        broken = log.verify()
        assert not broken.ok and broken.first_broken_id == receipt.record_id, column
        _tamper(path, "UPDATE audit SET reason = ?, closest = ?, failed_checks = '[]' WHERE id = ?",
                "The documents do not say.", "faq.docx > section 2", receipt.record_id)
        assert log.verify().ok


def test_adding_a_value_to_an_old_row_breaks_the_chain(tmp_path: Path) -> None:
    path = tmp_path / "kestrel.sqlite"
    _first_schema_database(path)
    log = SqliteAuditLog(ConnectionFactory(path))
    _tamper(path, "UPDATE audit SET reason = 'invented later' WHERE id = 1")
    result = log.verify()
    assert not result.ok and result.first_broken_id == 1
