"""fastembed embedder: prefixes, lazy loading, clear errors, and one real-model check."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from kestrel.config import Settings
from kestrel.contracts import Embedder
from kestrel.errors import ProviderUnavailableError
from kestrel.retrieval.embed import BGE_QUERY_PREFIX, DEFAULT_MODEL, FastEmbedEmbedder, prefixes_for


class _Recorder:
    """Stands in for fastembed's TextEmbedding and records the texts it embeds."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed(self, texts: list[str], batch_size: int = 32) -> Any:
        import numpy as np

        self.seen.extend(texts)
        return [np.full(384, 0.5, dtype="float32") for _ in texts]


def test_prefixes() -> None:
    assert prefixes_for("BAAI/bge-small-en-v1.5") == (BGE_QUERY_PREFIX, "")
    assert prefixes_for("BAAI/bge-base-en-v1.5") == (BGE_QUERY_PREFIX, "")
    assert prefixes_for("intfloat/multilingual-e5-large") == ("query: ", "passage: ")
    assert prefixes_for("sentence-transformers/all-MiniLM-L6-v2") == ("", "")


def test_construction_is_lazy_and_dimension_needs_no_model() -> None:
    embedder = FastEmbedEmbedder()
    assert isinstance(embedder, Embedder)
    assert embedder.model_name == DEFAULT_MODEL
    assert embedder.dimension == 384
    assert embedder._model is None  # nothing loaded yet


def test_from_settings(settings: Settings) -> None:
    assert FastEmbedEmbedder.from_settings(settings).model_name == settings.embedding_model


def test_query_gets_the_bge_prefix_and_passages_do_not() -> None:
    embedder = FastEmbedEmbedder()
    recorder = _Recorder()
    embedder._model = recorder
    vectors = embedder.embed_documents(["passage one", "passage two"])
    query = embedder.embed_query("what is covered")
    assert recorder.seen == ["passage one", "passage two", BGE_QUERY_PREFIX + "what is covered"]
    assert len(vectors) == 2 and len(query) == 384 and isinstance(query[0], float)
    assert embedder.embed_documents([]) == []


def test_missing_model_is_a_clear_provider_error(tmp_path: Path) -> None:
    embedder = FastEmbedEmbedder("BAAI/bge-small-en-v1.5", cache_dir=str(tmp_path / "empty"), local_files_only=True)
    with pytest.raises(ProviderUnavailableError, match="could not be loaded"):
        embedder.embed_query("hello")


def _real_model() -> FastEmbedEmbedder | None:
    """The default model from the local cache only; None when it is not cached."""
    embedder = FastEmbedEmbedder(local_files_only=True)
    try:
        embedder.load()
    except ProviderUnavailableError:
        return None
    return embedder


def test_real_model_ranks_a_relevant_passage_first() -> None:
    embedder = _real_model()
    if embedder is None:
        pytest.skip("the default embedding model is not in the local fastembed cache")
    passages = [
        "Claims must be reported within six months of the date of death.",
        "The office canteen opens at eight in the morning.",
    ]
    vectors = embedder.embed_documents(passages)
    query = embedder.embed_query("How long do I have to report a death claim?")
    assert len(query) == embedder.dimension == 384

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))

    assert cosine(query, vectors[0]) > cosine(query, vectors[1]) + 0.1
