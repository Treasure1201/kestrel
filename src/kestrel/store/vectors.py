"""sqlite-vec implementation of `VectorIndex`: exact cosine search, filtered before ranking.

Embeddings are float32 blobs in `chunk_vectors`, each stamped with the model name and
dimension that produced it. Search computes `vec_distance_cosine` only over rows whose
document is in the allow-list (a plain `WHERE` in the same statement), so a disallowed
document can never push allowed chunks out of the top `limit`.

Mixing vectors from two models would make distances meaningless, so writes and
searches check that stored vectors match the configured model and dimension, and
raise a `ConfigurationError` asking for a re-ingest when they do not.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Sequence

import sqlite_vec

from kestrel.contracts import VectorItem, VectorMatch
from kestrel.errors import ConfigurationError, InvalidInputError, ProviderUnavailableError
from kestrel.store.connection import ConnectionFactory

_SEARCH_SQL = (
    "SELECT chunk_id, vec_distance_cosine(embedding, ?) AS distance FROM chunk_vectors"
    " WHERE document_id IN (SELECT value FROM json_each(?)) AND model = ? AND dimension = ?"
    " ORDER BY distance, chunk_id LIMIT ?"
)


def _ids(values: Collection[str]) -> str:
    return json.dumps(sorted(set(values)))


class SqliteVecIndex:
    """Chunk embeddings in the SQLite file, searched with sqlite-vec.

    Args:
        connections: Connection factory for the database file.
        model_name: The embedding model in use (`Embedder.model_name`).
        dimension: Its vector length (`Embedder.dimension`).
    """

    def __init__(self, connections: ConnectionFactory, *, model_name: str, dimension: int) -> None:
        if dimension < 1:
            raise ConfigurationError("The embedding dimension must be positive.", detail=f"dimension={dimension}")
        self._connections = connections
        self._model = model_name
        self._dimension = dimension

    @property
    def model_name(self) -> str:
        """The embedding model this index expects."""
        return self._model

    @property
    def dimension(self) -> int:
        """The vector length this index expects."""
        return self._dimension

    @property
    def available(self) -> bool:
        """False when sqlite-vec cannot be loaded; storing still works, searching does not."""
        return self._connections.vector_extension_available()

    def stored_models(self) -> list[tuple[str, int]]:
        """Distinct (model, dimension) pairs of the vectors stored now."""
        with self._connections.connect() as conn:
            rows = conn.execute("SELECT DISTINCT model, dimension FROM chunk_vectors ORDER BY model, dimension").fetchall()
        return [(row["model"], int(row["dimension"])) for row in rows]

    def check_model(self) -> None:
        """Raise `ConfigurationError` if any stored vector was made by another model or dimension."""
        other = [pair for pair in self.stored_models() if pair != (self._model, self._dimension)]
        if other:
            found = ", ".join(f"{model} ({dim} dimensions)" for model, dim in other)
            raise ConfigurationError(
                f"The stored embeddings were made with {found}, but the configured embedding model is "
                f"{self._model} ({self._dimension} dimensions). Re-ingest the documents so every chunk is "
                "embedded with the configured model (clear the vectors first, or use a new database file).",
                detail=f"stored={other}",
            )

    def upsert(self, items: Sequence[VectorItem]) -> None:
        if not items:
            return
        rows = []
        for item in items:
            if len(item.embedding) != self._dimension:
                raise InvalidInputError(
                    f"Embedding for {item.chunk_id} has {len(item.embedding)} values; expected {self._dimension}."
                )
            if not all(math.isfinite(value) for value in item.embedding):
                raise InvalidInputError(f"Embedding for {item.chunk_id} contains a non-finite value.")
            rows.append(
                (item.chunk_id, item.document_id, item.collection, self._model, self._dimension,
                 sqlite_vec.serialize_float32(list(item.embedding)))
            )
        self.check_model()
        with self._connections.write() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO chunk_vectors (chunk_id, document_id, collection, model, dimension, embedding)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def delete_for_document(self, document_id: str) -> None:
        with self._connections.write() as conn:
            conn.execute("DELETE FROM chunk_vectors WHERE document_id = ?", (document_id,))

    def clear(self) -> int:
        """Delete every stored vector (before re-embedding with a new model). Returns the count removed."""
        with self._connections.write() as conn:
            return int(conn.execute("DELETE FROM chunk_vectors").rowcount)

    def existing(self, chunk_ids: Collection[str]) -> set[str]:
        """Chunk ids that already have a vector from the configured model."""
        if not chunk_ids:
            return set()
        with self._connections.connect() as conn:
            rows = conn.execute(
                "SELECT chunk_id FROM chunk_vectors WHERE chunk_id IN (SELECT value FROM json_each(?))"
                " AND model = ? AND dimension = ?",
                (_ids(chunk_ids), self._model, self._dimension),
            ).fetchall()
        return {row["chunk_id"] for row in rows}

    def search(self, query: Sequence[float], allowed_document_ids: Collection[str], limit: int) -> list[VectorMatch]:
        if not allowed_document_ids or limit < 1:
            return []
        if len(query) != self._dimension:
            raise InvalidInputError(f"The query vector has {len(query)} values; expected {self._dimension}.")
        if not self.available:
            raise ProviderUnavailableError("Vector search is unavailable: the sqlite-vec extension could not be loaded.")
        self.check_model()
        if not any(query):
            return []  # a zero vector has no direction, so cosine distance is undefined
        blob = sqlite_vec.serialize_float32(list(query))
        with self._connections.connect(vectors=True) as conn:
            rows = conn.execute(
                _SEARCH_SQL, (blob, _ids(allowed_document_ids), self._model, self._dimension, limit)
            ).fetchall()
        # A stored zero vector yields a NULL distance; it cannot be ranked, so skip it.
        return [
            VectorMatch(chunk_id=row["chunk_id"], distance=float(row["distance"]))
            for row in rows
            if row["distance"] is not None
        ]
