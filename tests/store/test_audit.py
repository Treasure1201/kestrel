"""Hash-chained audit log: append, verify, tamper detection, pruning and citations."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kestrel.contracts import AuditCitation, AuditEntry, AuditLog, Outcome
from kestrel.errors import ErrorCategory
from kestrel.store import ConnectionFactory, SqliteAuditLog

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def connections(tmp_path: Path) -> ConnectionFactory:
    return ConnectionFactory(tmp_path / "audit.sqlite")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def log(connections: ConnectionFactory, clock: Clock) -> SqliteAuditLog:
    return SqliteAuditLog(connections, clock=clock)


def ask(bot: str = "bot-a", *docs: str, trace: str = "t") -> AuditEntry:
    return AuditEntry(
        trace_id=trace,
        bot_id=bot,
        channel="cli",
        user="u1",
        roles=("agent",),
        question_digest="d" * 64,
        retrieved_chunk_ids=tuple(f"{d}:s000:p00" for d in docs),
        citations=tuple(
            AuditCitation(label=f"c{i}", document_id=d, section_id=f"{d}:s000", locator="loc", quote="quoted words")
            for i, d in enumerate(docs, start=1)
        ),
        calculations=("0.75 * 1000 = 750",),
        outcome=Outcome.ANSWERED,
        model="ollama:test",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0012,
        latency_ms=321,
    )


def _tamper(connections: ConnectionFactory, sql: str, *params: object) -> None:
    with connections.connect() as conn:
        conn.execute("DROP TRIGGER audit_no_update")
        conn.execute(sql, params)


def test_implements_protocol(log: SqliteAuditLog) -> None:
    assert isinstance(log, AuditLog)


def test_empty_chain_verifies(log: SqliteAuditLog) -> None:
    assert log.verify().ok


def test_append_links_records_and_round_trips(log: SqliteAuditLog) -> None:
    first = log.append(ask("bot-a", "doc1", trace="t1"))
    second = log.append(AuditEntry(kind="block", trace_id="t2", channel="ui", user="op", document_id="doc1",
                                   detail="withdrawn", error_category=ErrorCategory.INVALID_INPUT))
    records = log.tail(10)
    assert [r.id for r in records] == [second.record_id, first.record_id]
    assert records[1].prev_hash == "0" * 64
    assert records[0].prev_hash == first.hash
    assert records[1].entry == ask("bot-a", "doc1", trace="t1")
    assert records[0].entry.document_id == "doc1"
    result = log.verify()
    assert result.ok and result.checked == 2


def test_tail_filters_and_limits(log: SqliteAuditLog) -> None:
    for bot in ("bot-a", "bot-b", "bot-a"):
        log.append(ask(bot))
    assert [r.entry.bot_id for r in log.tail(10, ["bot-b"])] == ["bot-b"]
    assert len(log.tail(2)) == 2
    assert log.tail(0) == []
    assert log.tail(5, []) == []


def test_edited_row_is_detected(log: SqliteAuditLog, connections: ConnectionFactory) -> None:
    receipts = [log.append(ask(trace=f"t{i}")) for i in range(4)]
    _tamper(connections, "UPDATE audit SET outcome = 'refused' WHERE id = ?", receipts[2].record_id)
    result = log.verify()
    assert not result.ok
    assert result.first_broken_id == receipts[2].record_id


def test_edited_hash_is_detected(log: SqliteAuditLog, connections: ConnectionFactory) -> None:
    receipts = [log.append(ask(trace=f"t{i}")) for i in range(3)]
    _tamper(connections, "UPDATE audit SET hash = ? WHERE id = ?", "f" * 64, receipts[1].record_id)
    assert log.verify().first_broken_id == receipts[1].record_id


def test_deleted_row_is_detected(log: SqliteAuditLog, connections: ConnectionFactory) -> None:
    receipts = [log.append(ask(trace=f"t{i}")) for i in range(4)]
    with connections.connect() as conn:
        conn.execute("DELETE FROM audit WHERE id = ?", (receipts[1].record_id,))
    result = log.verify()
    assert not result.ok
    assert result.first_broken_id == receipts[2].record_id


def test_deleted_newest_row_is_detected(log: SqliteAuditLog, connections: ConnectionFactory) -> None:
    receipts = [log.append(ask(trace=f"t{i}")) for i in range(3)]
    with connections.connect() as conn:
        conn.execute("DELETE FROM audit WHERE id = ?", (receipts[-1].record_id,))
    result = log.verify()
    assert not result.ok and result.first_broken_id == receipts[-1].record_id


def test_prune_writes_a_checkpoint_and_the_chain_still_verifies(log: SqliteAuditLog, clock: Clock) -> None:
    # Interleave two bots so pruning one leaves gaps between the other's rows.
    for i in range(6):
        log.append(ask("bot-a" if i % 2 == 0 else "bot-b", f"doc{i}", trace=f"t{i}"))
    cutoff = clock.now + timedelta(seconds=1)
    log.append(ask("bot-a", "doc9", trace="late"))

    result = log.prune("bot-a", cutoff)
    assert result.removed == 3 and result.checkpoint_id is not None
    remaining = log.tail(20)
    assert {r.entry.trace_id for r in remaining if r.entry.kind != "checkpoint"} == {"t1", "t3", "t5", "late"}
    checkpoint = remaining[0]
    assert checkpoint.id == result.checkpoint_id and checkpoint.entry.kind == "checkpoint"
    assert log.verify().ok

    # Later appends, a second prune of the other bot, and a no-op prune keep it valid.
    log.append(ask("bot-b", trace="after"))
    assert log.prune("bot-b", cutoff).removed == 3
    assert log.prune("bot-b", cutoff).removed == 0
    verification = log.verify()
    assert verification.ok, verification
    assert log.answers_citing("doc0") == []  # pruned rows leave the citation index too


def test_prune_of_the_newest_rows_still_verifies(log: SqliteAuditLog, clock: Clock) -> None:
    for i in range(3):
        log.append(ask("bot-a", trace=f"t{i}"))
    assert log.prune("bot-a", clock.now + timedelta(days=1)).removed == 3
    assert log.verify().ok
    log.append(ask("bot-a", trace="next"))
    assert log.verify().ok


def test_deleting_a_row_after_pruning_is_still_detected(log: SqliteAuditLog, clock: Clock,
                                                         connections: ConnectionFactory) -> None:
    for i in range(4):
        log.append(ask("bot-a" if i < 2 else "bot-b", trace=f"t{i}"))
    log.prune("bot-a", clock.now + timedelta(days=1))
    survivor = next(r for r in log.tail(10) if r.entry.trace_id == "t2")
    with connections.connect() as conn:
        conn.execute("DELETE FROM audit WHERE id = ?", (survivor.id,))
    assert not log.verify().ok


def test_tampered_checkpoint_is_detected(log: SqliteAuditLog, clock: Clock, connections: ConnectionFactory) -> None:
    for i in range(3):
        log.append(ask("bot-a", trace=f"t{i}"))
    log.append(ask("bot-b", trace="keep"))
    result = log.prune("bot-a", clock.now + timedelta(days=1))
    _tamper(connections, "UPDATE audit SET detail = '{\"spans\": []}' WHERE id = ?", result.checkpoint_id)
    assert not log.verify().ok


def test_answers_citing(log: SqliteAuditLog) -> None:
    log.append(ask("bot-a", "doc1", "doc2", trace="one"))
    log.append(ask("bot-b", "doc1", trace="two"))
    log.append(ask("bot-a", "doc3", trace="three"))
    log.append(AuditEntry(kind="block", trace_id="blk", channel="cli", user="op", document_id="doc1"))
    assert [r.entry.trace_id for r in log.answers_citing("doc1")] == ["two", "one"]
    assert [r.entry.trace_id for r in log.answers_citing("doc1", ["bot-a"])] == ["one"]
    assert log.answers_citing("nothing") == []


def test_concurrent_appends_do_not_fork_the_chain(connections: ConnectionFactory) -> None:
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def worker(name: str) -> None:
        log = SqliteAuditLog(connections)
        try:
            barrier.wait()
            for i in range(25):
                log.append(ask(name, trace=f"{name}-{i}"))
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"bot-{n}",)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    log = SqliteAuditLog(connections)
    records = log.tail(1000)
    assert len(records) == 100
    assert len({r.prev_hash for r in records}) == 100  # every row links to a different predecessor
    assert log.verify().ok
