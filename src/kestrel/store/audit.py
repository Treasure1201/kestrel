"""SQLite implementation of `AuditLog`: append-only and hash-chained.

Each row's hash is SHA-256 over `prev_hash`, a newline, and the canonical JSON
(sorted keys, no whitespace) of `{"id", "created_at", "entry"}`, where `entry` is the
`AuditEntry` as JSON. `prev_hash` is the hash of the row with the next lower id, or
`GENESIS` for the first row.

Pruning deletes one bot's old rows, which may sit between other bots' rows. So that
`verify` still passes, the checkpoint row written by the prune records every run of
consecutive deleted rows as a *span*: first id, last id, the first row's `prev_hash`
and the last row's `hash`. `verify` walks surviving rows and spans in id order and
checks every link. A row deleted without a checkpoint leaves a gap that breaks a link;
an edited row no longer matches its hash; a deleted newest row is caught because
`sqlite_sequence` remembers the highest id ever used.

This is tamper evidence, not tamper proofing: someone able to rewrite the file can
rebuild the whole chain. Production anchors the head hash elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Collection, Iterable
from datetime import UTC, datetime

from pydantic import ValidationError

from kestrel.contracts import AuditCitation, AuditEntry, AuditReceipt, AuditRecord, AuditVerification, PruneResult
from kestrel.store.connection import ConnectionFactory

GENESIS = "0" * 64
CHECKPOINT_USER = "system"

_LIST_COLUMNS = ("roles", "redactions", "retrieved", "citations", "calculations", "failed_checks")


def canonical_json(value: object) -> str:
    """JSON with sorted keys and no insignificant whitespace, so equal values hash equally."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


_ADDED_FIELDS: dict[str, object] = {"reason": None, "failed_checks": [], "closest": None}
"""Entry fields added after the first schema, with their empty values. A field that is
still empty is left out of the hashed JSON, so rows written before it existed keep their
hashes, while a value written into a newer row is covered by that row's hash."""


def hashed_entry(entry: AuditEntry) -> dict[str, object]:
    """The entry as the JSON that is hashed: every field, except added fields that are empty."""
    data = entry.model_dump(mode="json")
    for name, empty in _ADDED_FIELDS.items():
        if data.get(name) == empty:
            data.pop(name, None)
    return data


def record_hash(prev_hash: str, record_id: int, created_at: str, entry: AuditEntry) -> str:
    """The hash of one audit row."""
    body = canonical_json({"id": record_id, "created_at": created_at, "entry": hashed_entry(entry)})
    return hashlib.sha256(f"{prev_hash}\n{body}".encode()).hexdigest()


def _timestamp(moment: datetime) -> str:
    """UTC ISO-8601 with microseconds; the fixed width makes string order equal time order."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _entry_from_row(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        kind=row["kind"],
        trace_id=row["trace_id"],
        bot_id=row["bot_id"],
        channel=row["channel"],
        user=row["user"],
        roles=tuple(json.loads(row["roles"])),
        question_text=row["question_text"],
        question_digest=row["question_digest"],
        redactions=tuple(json.loads(row["redactions"])),
        retrieved_chunk_ids=tuple(json.loads(row["retrieved"])),
        citations=tuple(AuditCitation(**c) for c in json.loads(row["citations"])),
        calculations=tuple(json.loads(row["calculations"])),
        outcome=row["outcome"],
        error_category=row["error_category"],
        model=row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_usd"],
        latency_ms=row["latency_ms"],
        document_id=row["document_id"],
        detail=row["detail"],
        reason=row["reason"],
        failed_checks=tuple(json.loads(row["failed_checks"] or "[]")),
        closest=row["closest"],
    )


def _record_from_row(row: sqlite3.Row) -> AuditRecord:
    return AuditRecord(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        entry=_entry_from_row(row),
        prev_hash=row["prev_hash"],
        hash=row["hash"],
    )


class SqliteAuditLog:
    """The audit trail in the SQLite file.

    Args:
        connections: Connection factory for the database file.
        clock: Returns the current time; injectable for tests. Defaults to UTC now.
    """

    def __init__(self, connections: ConnectionFactory, *, clock: Callable[[], datetime] | None = None) -> None:
        self._connections = connections
        self._clock = clock or (lambda: datetime.now(UTC))

    def append(self, entry: AuditEntry) -> AuditReceipt:
        # One BEGIN IMMEDIATE transaction: reading the head and writing the row cannot
        # interleave with another writer, so the chain never forks.
        with self._connections.write() as conn:
            record_id, digest = self._insert(conn, entry)
        return AuditReceipt(record_id=record_id, hash=digest)

    def _insert(self, conn: sqlite3.Connection, entry: AuditEntry, *, prev_hash: str | None = None) -> tuple[int, str]:
        if prev_hash is None:
            head = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = head["hash"] if head else GENESIS
        record_id = _highest_id(conn) + 1
        created_at = _timestamp(self._clock())
        digest = record_hash(prev_hash, record_id, created_at, entry)
        data = entry.model_dump(mode="json")
        conn.execute(
            "INSERT INTO audit (id, created_at, kind, trace_id, bot_id, channel, user, roles, question_text,"
            " question_digest, redactions, retrieved, citations, calculations, outcome, error_category, model,"
            " input_tokens, output_tokens, cost_usd, latency_ms, document_id, detail, reason, failed_checks,"
            " closest, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, created_at, data["kind"], data["trace_id"], data["bot_id"], data["channel"], data["user"],
                json.dumps(data["roles"]), data["question_text"], data["question_digest"],
                json.dumps(data["redactions"]), json.dumps(data["retrieved_chunk_ids"]),
                json.dumps(data["citations"], ensure_ascii=False), json.dumps(data["calculations"], ensure_ascii=False),
                data["outcome"], data["error_category"], data["model"], data["input_tokens"], data["output_tokens"],
                data["cost_usd"], data["latency_ms"], data["document_id"], data["detail"], data["reason"],
                json.dumps(data["failed_checks"]), data["closest"], prev_hash, digest,
            ),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO audit_documents (audit_id, document_id) VALUES (?, ?)",
            [(record_id, doc) for doc in entry.cited_document_ids],
        )
        return record_id, digest

    def tail(self, limit: int, bot_ids: Collection[str] | None = None) -> list[AuditRecord]:
        if limit < 1:
            return []
        with self._connections.connect() as conn:
            if bot_ids is None:
                rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit WHERE bot_id IN (SELECT value FROM json_each(?)) ORDER BY id DESC LIMIT ?",
                    (json.dumps(sorted(bot_ids)), limit),
                ).fetchall()
        return [_record_from_row(row) for row in rows]

    def answers_citing(self, document_id: str, bot_ids: Collection[str] | None = None) -> list[AuditRecord]:
        sql = (
            "SELECT a.* FROM audit a JOIN audit_documents ad ON ad.audit_id = a.id"
            " WHERE ad.document_id = ? AND a.kind = 'ask'"
        )
        params: list[object] = [document_id]
        if bot_ids is not None:
            sql += " AND a.bot_id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(sorted(bot_ids)))
        with self._connections.connect() as conn:
            rows = conn.execute(sql + " ORDER BY a.id DESC", params).fetchall()
        return [_record_from_row(row) for row in rows]

    def prune(self, bot_id: str, older_than: datetime) -> PruneResult:
        cutoff = _timestamp(older_than)
        with self._connections.write() as conn:
            doomed = {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM audit WHERE bot_id = ? AND created_at < ? AND kind != 'checkpoint'",
                    (bot_id, cutoff),
                )
            }
            if not doomed:
                return PruneResult(removed=0)
            head = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()["hash"]
            earlier = _checkpoint_spans(conn.execute("SELECT id, kind, detail FROM audit WHERE kind = 'checkpoint'"))
            spans = _spans(conn, doomed, earlier)
            conn.execute(
                "DELETE FROM audit WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(doomed)),)
            )
            detail = canonical_json(
                {"pruned_bot": bot_id, "older_than": cutoff, "removed": len(doomed), "spans": spans}
            )
            checkpoint = AuditEntry(
                kind="checkpoint", trace_id=uuid.uuid4().hex, bot_id=bot_id, channel="cli",
                user=CHECKPOINT_USER, detail=detail,
            )
            # The checkpoint links to the head as it was before deleting, which may itself be pruned.
            checkpoint_id, _ = self._insert(conn, checkpoint, prev_hash=head)
        return PruneResult(removed=len(doomed), checkpoint_id=checkpoint_id)

    def verify(self) -> AuditVerification:
        with self._connections.read() as conn:
            rows = conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
            highest = _highest_id(conn)
        try:
            spans = _checkpoint_spans(rows)
        except _BadCheckpoint as exc:
            return AuditVerification(ok=False, checked=0, first_broken_id=exc.record_id, detail=str(exc))

        # Walk surviving rows and pruned spans together, in id order.
        items: list[tuple[int, int, str, str, sqlite3.Row | None]] = [
            (first, last, prev, digest, None) for first, last, prev, digest in spans
        ]
        items.extend((row["id"], row["id"], row["prev_hash"], row["hash"], row) for row in rows)
        items.sort(key=lambda item: item[0])

        expected_prev = GENESIS
        last_id = 0
        checked = 0
        for first, last, prev, digest, row in items:
            if first <= last_id:
                return AuditVerification(ok=False, checked=checked, first_broken_id=first, detail="ids overlap a pruned span")
            if prev != expected_prev:
                return AuditVerification(
                    ok=False, checked=checked, first_broken_id=first,
                    detail="link to the previous record does not match (a record was removed or changed)",
                )
            if row is not None:
                try:
                    recomputed = record_hash(row["prev_hash"], row["id"], row["created_at"], _entry_from_row(row))
                except (ValidationError, ValueError, TypeError):
                    recomputed = None
                if recomputed != row["hash"]:
                    return AuditVerification(
                        ok=False, checked=checked, first_broken_id=row["id"], detail="record contents do not match its hash"
                    )
                checked += 1
            expected_prev = digest
            last_id = last
        if highest > last_id:
            return AuditVerification(
                ok=False, checked=checked, first_broken_id=last_id + 1,
                detail="the newest records are missing (the chain was truncated)",
            )
        return AuditVerification(ok=True, checked=checked)


def _highest_id(conn: sqlite3.Connection) -> int:
    """The highest id ever used, including deleted rows (AUTOINCREMENT keeps it in sqlite_sequence)."""
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'audit'").fetchone()
    top = conn.execute("SELECT max(id) FROM audit").fetchone()[0]
    return max(int(seq["seq"]) if seq else 0, int(top or 0))


Span = tuple[int, int, str, str]
"""(first id, last id, prev_hash of the first row, hash of the last row) of a pruned run."""


class _BadCheckpoint(ValueError):
    def __init__(self, record_id: int) -> None:
        super().__init__(f"checkpoint {record_id} is unreadable")
        self.record_id = record_id


def _checkpoint_spans(rows: Iterable[sqlite3.Row]) -> list[Span]:
    """Every span recorded by the checkpoint rows among `rows`. Raises `_BadCheckpoint`."""
    spans: list[Span] = []
    for row in rows:
        if row["kind"] != "checkpoint":
            continue
        try:
            for first, last, prev, digest in json.loads(row["detail"] or "{}").get("spans", []):
                spans.append((int(first), int(last), str(prev), str(digest)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise _BadCheckpoint(row["id"]) from exc
    return spans


def _spans(conn: sqlite3.Connection, doomed: set[int], earlier: list[Span]) -> list[list[object]]:
    """Group the rows about to be deleted into runs with no surviving row, and no earlier span, between them."""
    sequence: list[tuple[int, sqlite3.Row | None]] = [(first, None) for first, *_ in earlier]
    sequence.extend((row["id"], row) for row in conn.execute("SELECT id, prev_hash, hash FROM audit"))
    sequence.sort(key=lambda item: item[0])
    spans: list[list[object]] = []
    current: list[object] | None = None
    for record_id, row in sequence:
        if row is not None and record_id in doomed:
            if current is None:
                current = [record_id, record_id, row["prev_hash"], row["hash"]]
            else:
                current[1], current[3] = record_id, row["hash"]
        elif current is not None:
            spans.append(current)
            current = None
    if current is not None:
        spans.append(current)
    return spans
