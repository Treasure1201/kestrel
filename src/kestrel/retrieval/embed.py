"""Local CPU embeddings with fastembed (ONNX). The model loads on first use, not on import.

BGE English models are trained with an instruction prefix on the *query* side only;
passages are embedded as they are. E5 and Nomic models use a prefix on both sides.
Models not listed here get no prefix.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from typing import Any

from kestrel.config import Settings
from kestrel.errors import ProviderUnavailableError

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# (pattern on the lower-cased model name, query prefix, passage prefix)
_PREFIXES: tuple[tuple[str, str, str], ...] = (
    (r"^baai/bge-.*-en(-v\d+(\.\d+)?)?$", BGE_QUERY_PREFIX, ""),
    (r"^intfloat/(multilingual-)?e5-", "query: ", "passage: "),
    (r"^nomic-ai/nomic-embed-text", "search_query: ", "search_document: "),
)


def prefixes_for(model_name: str) -> tuple[str, str]:
    """(query prefix, passage prefix) for a model name; empty strings when none is needed."""
    name = model_name.lower()
    for pattern, query, passage in _PREFIXES:
        if re.search(pattern, name):
            return query, passage
    return "", ""


class FastEmbedEmbedder:
    """`Embedder` backed by fastembed.

    Args:
        model_name: A fastembed text model, default `BAAI/bge-small-en-v1.5`.
        cache_dir: Where model files are kept; fastembed's default (or
            `FASTEMBED_CACHE_PATH`) when None.
        local_files_only: Never download; fail if the model is not cached.
        threads: ONNX runtime threads; fastembed's default when None.
        batch_size: Texts per inference batch.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        cache_dir: str | None = None,
        local_files_only: bool = False,
        threads: int | None = None,
        batch_size: int = 32,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._local_only = local_files_only
        self._threads = threads
        self._batch_size = batch_size
        self._query_prefix, self._passage_prefix = prefixes_for(model_name)
        self._model: Any = None
        self._dimension: int | None = _known_dimension(model_name)
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> FastEmbedEmbedder:
        """The embedder named by `KESTREL_EMBEDDING_MODEL`."""
        return cls(settings.embedding_model)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        """Vector length; read from fastembed's model list, or by loading the model if not listed."""
        if self._dimension is None:
            self._dimension = len(self._embed([""])[0])
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed([self._passage_prefix + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._query_prefix + text])[0]

    def load(self) -> None:
        """Load the model now (downloading it if needed) instead of on first use."""
        self._load()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding

                    kwargs: dict[str, Any] = {"model_name": self._model_name, "local_files_only": self._local_only}
                    if self._cache_dir is not None:
                        kwargs["cache_dir"] = self._cache_dir
                    if self._threads is not None:
                        kwargs["threads"] = self._threads
                    self._model = TextEmbedding(**kwargs)
                except Exception as exc:  # fastembed raises many types: network, file, ONNX, value
                    hint = (
                        "it is not in the local model cache"
                        if self._local_only
                        else "the first run downloads it, so check the network connection, or pre-fetch it on a "
                        "connected machine and copy the fastembed cache folder"
                    )
                    raise ProviderUnavailableError(
                        f"The embedding model {self._model_name} could not be loaded: {hint}.",
                        detail=f"{type(exc).__name__}: {exc}",
                    ) from exc
            return self._model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        with self._lock:
            # ONNX sessions are not guaranteed safe for concurrent calls; serialise inference.
            try:
                vectors = [vector.tolist() for vector in model.embed(texts, batch_size=self._batch_size)]
            except Exception as exc:
                raise ProviderUnavailableError(
                    f"The embedding model {self._model_name} failed while embedding text.",
                    detail=f"{type(exc).__name__}: {exc}",
                ) from exc
        return [[float(value) for value in vector] for vector in vectors]


def _known_dimension(model_name: str) -> int | None:
    """The dimension fastembed publishes for a model, without loading it."""
    try:
        from fastembed import TextEmbedding

        for description in TextEmbedding.list_supported_models():
            if str(description.get("model", "")).lower() == model_name.lower():
                return int(description["dim"])
    except Exception:  # an unusual fastembed install: fall back to loading the model
        return None
    return None
