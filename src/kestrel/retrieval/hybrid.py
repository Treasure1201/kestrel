"""Hybrid retrieval: BM25 keyword ranking and vector ranking fused by reciprocal rank.

Both rankers see only the documents `DocumentRepository.eligible_document_ids` allows,
applied inside their SQL before ranking. Reciprocal rank fusion scores a chunk as
`sum(1 / (k + rank))` over the rankings it appears in, so a chunk found by both
rankers rises, and neither ranker's raw score scale matters.

When the vector index cannot be used (the extension does not load, the embedding
model is missing, or stored vectors come from another model), search still runs on
keywords alone and says so in `SearchResult.warnings`.
"""

from __future__ import annotations

from kestrel.contracts import (
    DocumentRepository,
    Embedder,
    KnowledgeConfig,
    ScopeDecision,
    SearchHit,
    SearchResult,
    SearchScope,
    SectionView,
    TableView,
    VectorIndex,
)
from kestrel.errors import InvalidInputError, KestrelError
from kestrel.retrieval.scope import resolve_scope

RRF_K = 60
"""The usual reciprocal rank fusion constant: damps the advantage of the very top ranks."""

VECTORS_UNAVAILABLE = "Vector search is unavailable (the sqlite-vec extension could not be loaded); results are keyword-only."


class HybridRetriever:
    """`Retriever` over a document repository, a vector index and an embedder.

    Args:
        documents: The repository; also the source of the pre-ranking filter.
        vectors: The vector index, or None for keyword-only search.
        embedder: Embeds queries for the vector index, or None for keyword-only search.
        rrf_k: Reciprocal rank fusion constant.
        candidates_per_ranker: How many results each ranker contributes before fusion;
            defaults to four times the requested limit, at least 20.
    """

    def __init__(
        self,
        documents: DocumentRepository,
        vectors: VectorIndex | None,
        embedder: Embedder | None,
        *,
        rrf_k: int = RRF_K,
        candidates_per_ranker: int | None = None,
    ) -> None:
        if rrf_k < 1:
            raise InvalidInputError("rrf_k must be at least 1.")
        self._documents = documents
        self._vectors = vectors
        self._embedder = embedder
        self._k = rrf_k
        self._pool = candidates_per_ranker

    def resolve_scope(self, question: str, knowledge: KnowledgeConfig) -> ScopeDecision:
        return resolve_scope(question, knowledge, self._documents.list_documents(knowledge.collections))

    def search(self, query: str, scope: SearchScope, limit: int) -> SearchResult:
        if limit < 1:
            raise InvalidInputError("The search limit must be at least 1.")
        if not query.strip():
            return SearchResult(scope=scope)
        pool = self._pool or max(limit * 4, 20)
        keyword = self._documents.keyword_search(query, scope, pool)
        semantic, warnings = self._vector_hits(query, scope, pool)
        return SearchResult(hits=tuple(self._fuse(keyword, semantic, limit)), scope=scope, warnings=tuple(warnings))

    def _vector_hits(self, query: str, scope: SearchScope, pool: int) -> tuple[list[SearchHit], list[str]]:
        if self._vectors is None or self._embedder is None:
            return [], ["Vector search is not configured; results are keyword-only."]
        if not self._vectors.available:
            return [], [VECTORS_UNAVAILABLE]
        allowed = self._documents.eligible_document_ids(scope)
        if not allowed:
            return [], []
        try:
            matches = self._vectors.search(self._embedder.embed_query(query), allowed, pool)
        except KestrelError as exc:
            return [], [f"{exc.user_message} Results are keyword-only."]
        return self._documents.resolve_hits([m.chunk_id for m in matches], scope), []

    def _fuse(self, keyword: list[SearchHit], semantic: list[SearchHit], limit: int) -> list[SearchHit]:
        scores: dict[str, float] = {}
        best: dict[str, SearchHit] = {}
        first_seen: dict[str, tuple[int, int]] = {}
        for ranker, hits in enumerate((keyword, semantic)):
            for position, hit in enumerate(hits, start=1):
                scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (self._k + position)
                best.setdefault(hit.chunk_id, hit)
                first_seen.setdefault(hit.chunk_id, (position, ranker))
        # Higher fused score first; ties go to the better single rank, keyword before vector, then id.
        order = sorted(scores, key=lambda cid: (-scores[cid], first_seen[cid], cid))[:limit]
        return [best[cid].model_copy(update={"score": scores[cid], "rank": rank}) for rank, cid in enumerate(order, start=1)]

    def _in_scope(self, section_id: str, scope: SearchScope) -> bool:
        section = self._documents.get_section(section_id)
        return section is not None and section.document_id in set(self._documents.eligible_document_ids(scope))

    def get_table(self, section_id: str, scope: SearchScope) -> TableView | None:
        if not self._in_scope(section_id, scope):
            return None
        return self._documents.get_table(section_id)

    def get_section(self, section_id: str, scope: SearchScope) -> SectionView | None:
        """One whole section, or None if it is unknown or outside `scope`."""
        if not self._in_scope(section_id, scope):
            return None
        return self._documents.get_section(section_id)
