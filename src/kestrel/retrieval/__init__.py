"""Retrieval: embeddings, hybrid search and edition and date scope."""

from __future__ import annotations

from kestrel.contracts import Embedder
from kestrel.retrieval.embed import FastEmbedEmbedder
from kestrel.retrieval.hybrid import HybridRetriever
from kestrel.retrieval.scope import resolve_scope
from kestrel.store.factory import Store


def open_retriever(store: Store, embedder: Embedder) -> HybridRetriever:
    """A hybrid retriever over `store`, embedding queries with `embedder`."""
    return HybridRetriever(store.documents, store.vectors, embedder)


__all__ = ["FastEmbedEmbedder", "HybridRetriever", "open_retriever", "resolve_scope"]
