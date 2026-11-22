"""In-memory stand-ins for the document repository and vector index, for pipeline tests.

They implement only what ingest uses, follow the Protocol contracts (atomic replace by
file name, idempotent save, filter-before-rank is irrelevant here), and count writes
so tests can assert that a second ingest is a no-op.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path

from kestrel.contracts import (
    Chunk,
    DocumentRecord,
    DocumentStatus,
    Finding,
    ManifestEntry,
    ParsedDocument,
    VectorItem,
    VectorMatch,
)
from kestrel.errors import InvalidInputError

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "bots" / "kestrel-products" / "manifest.yaml"
COLLECTION = "kestrel-products"


class MemoryRepository:
    """Documents, chunks and findings in dictionaries; `writes` counts every mutation."""

    def __init__(self) -> None:
        self.records: dict[str, DocumentRecord] = {}
        self.documents: dict[str, ParsedDocument] = {}
        self.chunks: dict[str, list[Chunk]] = {}
        self.finding_rows: dict[str, list[Finding]] = {}
        self.writes = 0

    def find_document(self, collection: str, filename: str) -> DocumentRecord | None:
        return next((r for r in self.records.values() if r.collection == collection and r.filename == filename), None)

    def get_document(self, document_id: str) -> DocumentRecord | None:
        return self.records.get(document_id)

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
        self.writes += 1
        old = self.find_document(document.collection, document.filename)
        if old is not None:
            self.delete_document(old.id)
            self.writes -= 1
        record = DocumentRecord(
            id=document.id,
            collection=document.collection,
            sha256=document.sha256,
            filename=document.filename,
            title=document.title,
            media_type=document.media_type,
            family=entry.family if entry else None,
            edition=entry.edition if entry else None,
            effective_from=entry.effective_from if entry else None,
            supersedes=entry.supersedes if entry else None,
            status=status,
            status_reason=status_reason,
            approved_by=entry.approved_by if entry else None,
            ingested_at=datetime.now(UTC),
            section_count=len(document.sections),
            finding_count=len(findings),
        )
        self.records[record.id] = record
        self.documents[record.id] = document
        self.chunks[record.id] = list(chunks)
        self.finding_rows[record.id] = list(findings)
        return record

    def set_status(self, document_id: str, status: DocumentStatus, *, reason: str | None = None) -> DocumentRecord:
        if document_id not in self.records:
            raise InvalidInputError(f"unknown document {document_id}")
        self.writes += 1
        record = self.records[document_id].model_copy(update={"status": status, "status_reason": reason})
        self.records[document_id] = record
        return record

    def delete_document(self, document_id: str) -> None:
        self.writes += 1
        for table in (self.records, self.documents, self.chunks, self.finding_rows):
            table.pop(document_id, None)

    def list_documents(self, collections: Collection[str]) -> list[DocumentRecord]:
        return sorted((r for r in self.records.values() if r.collection in collections), key=lambda r: r.filename)

    def list_chunks(self, document_id: str) -> list[Chunk]:
        return list(self.chunks.get(document_id, []))

    def findings(self, document_ids: Collection[str]) -> list[Finding]:
        return [f for d in document_ids for f in self.finding_rows.get(d, [])]

    def by_filename(self, filename: str) -> DocumentRecord:
        record = self.find_document(COLLECTION, filename)
        assert record is not None, filename
        return record


class MemoryVectorIndex:
    """Vectors by chunk id. Set `available = False` to simulate a missing extension."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.vectors: dict[str, VectorItem] = {}
        self.upserts = 0

    def upsert(self, items: Sequence[VectorItem]) -> None:
        self.upserts += 1
        for item in items:
            self.vectors[item.chunk_id] = item

    def delete_for_document(self, document_id: str) -> None:
        for key in [k for k, v in self.vectors.items() if v.document_id == document_id]:
            del self.vectors[key]

    def existing(self, chunk_ids: Collection[str]) -> set[str]:
        return {c for c in chunk_ids if c in self.vectors}

    def search(self, query: Sequence[float], allowed_document_ids: Collection[str], limit: int) -> list[VectorMatch]:
        return []
