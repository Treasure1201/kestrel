"""Storage, retrieval and privacy seams. Each has one local implementation.

Transaction boundaries:

- `DocumentRepository.save_document` is atomic: the document row, its sections,
  chunks, keyword index rows and findings commit together or not at all, replacing any
  previously stored copy of the same file. Vectors are written afterwards by
  `VectorIndex.upsert`; a document whose vectors are missing is still keyword
  searchable, and the next ingest embeds chunks that lack vectors.
- `AuditLog.append` reads the chain head and writes the new row in one write
  transaction, so concurrent writers cannot fork the chain.
- `DocumentRepository.set_status` changes one row; the service writes the matching
  `block`/`unblock` audit entry immediately after.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from kestrel.contracts.answers import Screened
from kestrel.contracts.audit import AuditEntry, AuditReceipt, AuditRecord, AuditVerification, PruneResult
from kestrel.contracts.bots import KnowledgeConfig, PrivacyPolicy
from kestrel.contracts.documents import Chunk, DocumentRecord, DocumentStatus, Finding, ManifestEntry, ParsedDocument
from kestrel.contracts.retrieval import (
    ScopeDecision,
    SearchHit,
    SearchResult,
    SearchScope,
    SectionView,
    TableView,
    VectorItem,
    VectorMatch,
)


@runtime_checkable
class Embedder(Protocol):
    """Turns text into fixed-length vectors. Runs on the local CPU by default."""

    @property
    def model_name(self) -> str:
        """Identifier stored beside each vector, so a model change is detectable."""
        ...

    @property
    def dimension(self) -> int:
        """Length of every vector returned."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for storage, one vector per text, in order."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (some models use a different prefix for queries)."""
        ...


@runtime_checkable
class VectorIndex(Protocol):
    """Nearest-neighbour search over chunk embeddings, filtered before ranking."""

    @property
    def available(self) -> bool:
        """False when the backing extension or service cannot be used; search then falls back to keywords."""
        ...

    def upsert(self, items: Sequence[VectorItem]) -> None:
        """Insert or replace vectors by chunk id, in one transaction."""
        ...

    def delete_for_document(self, document_id: str) -> None:
        """Remove every vector of one document."""
        ...

    def existing(self, chunk_ids: Collection[str]) -> set[str]:
        """Return the subset of `chunk_ids` that already have a vector."""
        ...

    def search(self, query: Sequence[float], allowed_document_ids: Collection[str], limit: int) -> list[VectorMatch]:
        """Return up to `limit` nearest chunks by cosine distance, closest first.

        Only chunks of `allowed_document_ids` are ranked: the filter is applied before
        ranking, never by trimming a global top-k. An empty allow-list returns [].
        """
        ...


@runtime_checkable
class DocumentRepository(Protocol):
    """Documents, sections, chunks and findings. Search methods honour `SearchScope`."""

    def find_document(self, collection: str, filename: str) -> DocumentRecord | None:
        """Return the stored version of a file in a collection, if any."""
        ...

    def get_document(self, document_id: str) -> DocumentRecord | None:
        """Return one document by id."""
        ...

    def save_document(
        self,
        document: ParsedDocument,
        chunks: Sequence[Chunk],
        findings: Sequence[Finding],
        *,
        status: DocumentStatus,
        entry: ManifestEntry | None,
        status_reason: str | None = None,
    ) -> DocumentRecord:
        """Atomically store a parsed document, replacing any previously stored copy of the same file.

        Saving the same content and metadata again changes nothing (idempotent).
        """
        ...

    def set_status(self, document_id: str, status: DocumentStatus, *, reason: str | None = None) -> DocumentRecord:
        """Change a document's lifecycle state. Raises `InvalidInputError` for an unknown id."""
        ...

    def delete_document(self, document_id: str) -> None:
        """Remove a document and everything derived from it (file removed from the source folder)."""
        ...

    def list_documents(self, collections: Collection[str]) -> list[DocumentRecord]:
        """Documents in the given collections, ordered by filename."""
        ...

    def list_chunks(self, document_id: str) -> list[Chunk]:
        """Every chunk of a document, in reading order."""
        ...

    def eligible_document_ids(self, scope: SearchScope) -> list[str]:
        """Ids of documents a search under `scope` may rank (the pre-ranking filter)."""
        ...

    def keyword_search(self, query: str, scope: SearchScope, limit: int) -> list[SearchHit]:
        """BM25 search restricted to `scope`. The query is sanitised for FTS5 syntax."""
        ...

    def resolve_hits(self, chunk_ids: Sequence[str], scope: SearchScope) -> list[SearchHit]:
        """Turn chunk ids (for example vector matches) into hits, keeping order and dropping any outside `scope`."""
        ...

    def get_section(self, section_id: str) -> SectionView | None:
        """One whole section, including its table if it has one."""
        ...

    def get_table(self, section_id: str) -> TableView | None:
        """The table of a table section, every row numbered from 1."""
        ...

    def findings(self, document_ids: Collection[str]) -> list[Finding]:
        """Scanner findings for the given documents."""
        ...


@runtime_checkable
class Retriever(Protocol):
    """Hybrid search (keyword and vector, fused by reciprocal rank) plus scope reading."""

    def resolve_scope(self, question: str, knowledge: KnowledgeConfig) -> ScopeDecision:
        """Read edition and date references in a question and decide the search scope."""
        ...

    def search(self, query: str, scope: SearchScope, limit: int) -> SearchResult:
        """Search within `scope`. Falls back to keywords, with a warning, when vectors are unavailable."""
        ...

    def get_table(self, section_id: str, scope: SearchScope) -> TableView | None:
        """The whole table of a section, or None if that section is outside `scope`."""
        ...


@runtime_checkable
class AuditLog(Protocol):
    """Append-only, hash-chained audit trail."""

    def append(self, entry: AuditEntry) -> AuditReceipt:
        """Append one record, linking it to the previous record's hash."""
        ...

    def tail(self, limit: int, bot_ids: Collection[str] | None = None) -> list[AuditRecord]:
        """Most recent records first, optionally only for some bots."""
        ...

    def verify(self) -> AuditVerification:
        """Recompute every hash and link; report the first broken record."""
        ...

    def prune(self, bot_id: str, older_than: datetime) -> PruneResult:
        """Delete one bot's records older than a cut-off and write a checkpoint that carries the chain forward."""
        ...

    def answers_citing(self, document_id: str, bot_ids: Collection[str] | None = None) -> list[AuditRecord]:
        """`ask` records whose citations include the document, most recent first."""
        ...


@runtime_checkable
class PrivacyScreen(Protocol):
    """Finds and removes personal information from text before anything else sees it."""

    def screen(self, text: str, policy: PrivacyPolicy) -> Screened:
        """Return the text with each match replaced by a `[KIND]` placeholder.

        Must raise rather than return unscreened text if screening cannot complete;
        the service then fails the request closed. Blocking (as opposed to redacting)
        is decided by the caller from `policy.on_personal_information`.
        """
        ...
