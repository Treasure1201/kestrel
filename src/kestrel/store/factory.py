"""One place to build the store from settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kestrel.config import Settings
from kestrel.contracts import Embedder
from kestrel.store.audit import SqliteAuditLog
from kestrel.store.connection import ConnectionFactory
from kestrel.store.documents import SqliteDocumentRepository
from kestrel.store.vectors import SqliteVecIndex


@dataclass(frozen=True)
class Store:
    """The repository, vector index and audit log over one database file.

    Every part opens a short-lived connection per operation, so one `Store` may be
    shared by threads (the UI, the MCP server) and used beside other processes (the CLI).
    """

    connections: ConnectionFactory
    documents: SqliteDocumentRepository
    vectors: SqliteVecIndex
    audit: SqliteAuditLog

    @property
    def path(self) -> Path:
        """The database file."""
        return self.connections.path


def open_store_at(path: Path | str, *, embedding_model: str, embedding_dimension: int) -> Store:
    """Open (creating and migrating if needed) the store in the file at `path`."""
    connections = ConnectionFactory(path)
    return Store(
        connections=connections,
        documents=SqliteDocumentRepository(connections),
        vectors=SqliteVecIndex(connections, model_name=embedding_model, dimension=embedding_dimension),
        audit=SqliteAuditLog(connections),
    )


def open_store(settings: Settings, embedder: Embedder) -> Store:
    """Open the store at `settings.db`, with a vector index for `embedder`'s model and dimension."""
    return open_store_at(settings.db, embedding_model=embedder.model_name, embedding_dimension=embedder.dimension)
