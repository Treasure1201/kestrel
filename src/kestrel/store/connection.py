"""Short-lived SQLite connections, one per operation.

The Streamlit app, the MCP server and the CLI may use the same database file at the
same time, from several threads and processes. A `sqlite3.Connection` must never be
shared across threads, so every repository operation opens its own connection from
the database path, uses it, and closes it. WAL journaling lets readers run beside one
writer; the busy timeout makes a second writer wait instead of failing.

The schema is migrated once, when the factory is created.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kestrel.errors import ConfigurationError
from kestrel.store.schema import load_vector_extension, open_database, transaction

BUSY_TIMEOUT_S = 10.0


class ConnectionFactory:
    """Opens connections to one database file.

    Args:
        path: The SQLite file. Created and migrated if missing. `:memory:` is refused,
            because every operation opens a new connection and would see an empty database.
    """

    def __init__(self, path: Path | str) -> None:
        if str(path) == ":memory:" or not str(path).strip():
            raise ConfigurationError(
                "The store needs a database file path, not an in-memory database.",
                detail=f"path={path!r}",
            )
        self._path = Path(path)
        self._vector_probe: bool | None = None
        self._probe_lock = threading.Lock()
        try:
            open_database(self._path).close()
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            raise ConfigurationError(
                f"Could not open the database at {self._path}. Check the path and that the file is not corrupt.",
                detail=str(exc),
            ) from exc

    @property
    def path(self) -> Path:
        """The database file."""
        return self._path

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=BUSY_TIMEOUT_S, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_S * 1000)}")
        return conn

    @contextmanager
    def connect(self, *, vectors: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a fresh connection, closed afterwards. `vectors=True` loads sqlite-vec into it."""
        conn = self._open()
        try:
            if vectors and not load_vector_extension(conn):
                raise ConfigurationError(
                    "The sqlite-vec extension could not be loaded; vector search is unavailable.",
                )
            yield conn
        finally:
            conn.close()

    @contextmanager
    def read(self, *, vectors: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside one read transaction, so several queries see one snapshot."""
        with self.connect(vectors=vectors) as conn:
            conn.execute("BEGIN")
            try:
                yield conn
            finally:
                conn.execute("COMMIT")

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside one write transaction (`BEGIN IMMEDIATE`), committed on success."""
        with self.connect() as conn, transaction(conn):
            yield conn

    def vector_extension_available(self) -> bool:
        """Whether sqlite-vec loads on this Python. Probed once, then cached."""
        with self._probe_lock:
            if self._vector_probe is None:
                conn = self._open()
                try:
                    self._vector_probe = load_vector_extension(conn)
                finally:
                    conn.close()
            return self._vector_probe
