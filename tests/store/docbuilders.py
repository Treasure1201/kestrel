"""Build parsed documents and chunks directly, without the ingestion parsers."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from fakes import FakeEmbedder

from kestrel.contracts import (
    Chunk,
    DocumentRecord,
    DocumentStatus,
    Finding,
    ManifestEntry,
    ParsedDocument,
    ParsedSection,
    ParsedTable,
    TableRow,
    TableRowView,
    VectorItem,
    chunk_id,
    section_id,
)
from kestrel.store import Store, open_store_at


@dataclass
class Prose:
    heading: tuple[str, ...]
    text: str


@dataclass
class Table:
    heading: tuple[str, ...]
    header: tuple[str, ...]
    rows: list[tuple[str, ...]] = field(default_factory=list)


@dataclass
class Built:
    document: ParsedDocument
    chunks: list[Chunk]

    @property
    def id(self) -> str:
        return self.document.id


def build(collection: str, filename: str, parts: Sequence[Prose | Table], *, title: str | None = None) -> Built:
    """A parsed document whose sha256 is derived from its content, with prose and table chunks."""
    content = repr((collection, filename, [vars(p) for p in parts])).encode()
    sha = hashlib.sha256(content).hexdigest()
    sections: list[ParsedSection] = []
    chunks: list[Chunk] = []
    doc_id = ParsedDocument(
        collection=collection, filename=filename, sha256=sha, media_type="text/markdown", title="x", sections=()
    ).id
    for ordinal, part in enumerate(parts):
        sid = section_id(doc_id, ordinal)
        locator = f"{filename} > {' > '.join(part.heading) or 'top'}"
        if isinstance(part, Prose):
            sections.append(
                ParsedSection(ordinal=ordinal, kind="prose", heading_path=part.heading, locator=locator, text=part.text)
            )
            chunks.append(
                Chunk(
                    id=chunk_id(sid, "prose", 0), collection=collection, document_id=doc_id, section_id=sid,
                    kind="prose", text=part.text, heading_path=part.heading, locator=locator,
                )
            )
        else:
            table = ParsedTable(
                header=part.header,
                rows=tuple(TableRow(cells=cells, address=f"row {i}") for i, cells in enumerate(part.rows, start=1)),
            )
            row_texts = [TableRowView(row_number=i, cells=cells).as_text(part.header) for i, cells in enumerate(part.rows, 1)]
            whole = " | ".join(part.header) + "\n" + "\n".join(" | ".join(r) for r in part.rows)
            sections.append(
                ParsedSection(
                    ordinal=ordinal, kind="table", heading_path=part.heading, locator=locator, text=whole, table=table
                )
            )
            chunks.append(
                Chunk(
                    id=chunk_id(sid, "table", 0), collection=collection, document_id=doc_id, section_id=sid,
                    kind="table", text=whole, heading_path=part.heading, locator=locator,
                )
            )
            for i, text in enumerate(row_texts, start=1):
                chunks.append(
                    Chunk(
                        id=chunk_id(sid, "table_row", i), collection=collection, document_id=doc_id, section_id=sid,
                        kind="table_row", row_index=i, text=text, heading_path=part.heading, locator=f"{locator} > row {i}",
                    )
                )
    document = ParsedDocument(
        collection=collection,
        filename=filename,
        sha256=sha,
        media_type="text/markdown",
        title=title or filename.rsplit(".", 1)[0],
        sections=tuple(sections),
    )
    return Built(document=document, chunks=chunks)


def entry(
    built: Built, *, family: str, edition: str, effective_from: date, supersedes: str | None = None
) -> ManifestEntry:
    return ManifestEntry(
        sha256=built.document.sha256,
        filename=built.document.filename,
        label=built.document.title,
        family=family,
        edition=edition,
        effective_from=effective_from,
        supersedes=supersedes,
        approved=True,
        approved_by="reviewer",
    )


def finding(built: Built, detail: str = "white text on white page") -> Finding:
    return Finding(
        document_id=built.id, locator=built.document.filename, kind="low_contrast", severity="high", detail=detail,
        excerpt="hidden words",
    )


def make_store(path: Path, embedder: FakeEmbedder) -> Store:
    return open_store_at(path, embedding_model=embedder.model_name, embedding_dimension=embedder.dimension)


def save(
    store: Store,
    built: Built,
    *,
    status: DocumentStatus = DocumentStatus.CURRENT,
    manifest: ManifestEntry | None = None,
    embedder: FakeEmbedder | None = None,
    findings: Sequence[Finding] = (),
) -> DocumentRecord:
    """Save a document and, when an embedder is given, its vectors."""
    record = store.documents.save_document(built.document, built.chunks, findings, status=status, entry=manifest)
    if embedder is not None:
        vectors = embedder.embed_documents([c.text for c in built.chunks])
        store.vectors.upsert(
            [
                VectorItem(chunk_id=c.id, document_id=c.document_id, collection=c.collection, embedding=tuple(v))
                for c, v in zip(built.chunks, vectors, strict=True)
            ]
        )
    return record
