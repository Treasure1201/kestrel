"""Ingest one collection's source folder: parse, scan, decide status, chunk, store, embed.

The run is idempotent. A file whose bytes and manifest metadata are unchanged is
reported `unchanged` and nothing is written for it; the only work done for it is
embedding chunks that still lack vectors (for example after a run where the vector
index was unavailable). A file that fails to parse is reported `failed` with a
reason and never stops the other files.

Transaction boundaries follow the repository contract: each document is saved
atomically with its sections, chunks and findings; vectors are written afterwards.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from kestrel.contracts import (
    Chunk,
    DocumentRecord,
    DocumentRepository,
    DocumentStatus,
    Embedder,
    Finding,
    IngestedFile,
    IngestReport,
    KnowledgeConfig,
    ParsedDocument,
    VectorIndex,
    VectorItem,
)
from kestrel.errors import ConfigurationError, KestrelError
from kestrel.ingest.chunker import DEFAULT_MAX_CHARS, chunk_document
from kestrel.ingest.files import discover_files
from kestrel.ingest.manifest import Manifest, StatusDecision, decide_statuses, load_manifest
from kestrel.ingest.parsers import media_type_for, parse_bytes
from kestrel.ingest.scan import scan_document

log = logging.getLogger(__name__)

EMBED_BATCH = 64


@dataclass
class _Prepared:
    document: ParsedDocument
    chunks: list[Chunk]
    findings: list[Finding]


def embedding_text(chunk: Chunk) -> str:
    """The text embedded for a chunk: its heading path as context, then the chunk text."""
    context = " > ".join(chunk.heading_path)
    return f"{context}\n{chunk.text}" if context else chunk.text


def _prepare(path: Path, data: bytes, collection: str, max_chars: int) -> _Prepared:
    parsed = parse_bytes(data, filename=path.name, collection=collection)
    scanned, findings = scan_document(parsed)
    return _Prepared(scanned, chunk_document(scanned, max_chars=max_chars), findings)


def _failure_detail(exc: Exception) -> str:
    if isinstance(exc, KestrelError):
        return exc.user_message
    return f"could not be parsed ({type(exc).__name__}: {exc})"


def _is_unchanged(record: DocumentRecord, sha256: str, decision: StatusDecision) -> bool:
    if record.sha256 != sha256 or record.status != decision.status:
        return False
    entry = decision.entry
    if entry is None:
        wanted = (None, None, None, None, None)
    else:
        wanted = (entry.family, entry.edition, entry.effective_from, entry.supersedes, entry.approved_by)
    stored = (record.family, record.edition, record.effective_from, record.supersedes, record.approved_by)
    return stored == wanted and (entry is None or record.title == entry.label)


def _resolve_manifest(collection: str, knowledge: KnowledgeConfig, manifest_path: Path | None) -> Manifest:
    path = manifest_path or knowledge.manifest
    if path is None:
        if knowledge.approval == "required":
            raise ConfigurationError(
                f"Collection {collection!r} requires approval but no manifest.yaml was given.",
            )
        return Manifest()
    manifest = load_manifest(path)
    if manifest.collection is not None and manifest.collection != collection:
        raise ConfigurationError(
            f"Manifest {path} is for collection {manifest.collection!r}, not {collection!r}.",
        )
    return manifest


def ingest_collection(
    source_dir: Path,
    collection: str,
    knowledge: KnowledgeConfig,
    manifest_path: Path | None,
    repository: DocumentRepository,
    vectors: VectorIndex,
    embedder: Embedder,
    *,
    max_chunk_chars: int = DEFAULT_MAX_CHARS,
) -> IngestReport:
    """Bring the stored copy of one collection in line with its source folder.

    Raises `ConfigurationError` for a missing folder or a missing or invalid manifest;
    every per-file problem is reported in the returned `IngestReport` instead.
    """
    if collection not in knowledge.collections:
        raise ConfigurationError(f"Collection {collection!r} is not one of the bot's collections.")
    manifest = _resolve_manifest(collection, knowledge, manifest_path)
    warnings: list[str] = []
    results: list[IngestedFile] = []
    stored = {record.filename: record for record in repository.list_documents([collection])}

    # 1. Read and hash every supported file.
    contents: dict[str, tuple[Path, bytes, str]] = {}
    for path in discover_files(source_dir):
        if media_type_for(path.name) is None:
            warnings.append(f"Skipped {path.name}: unsupported file type.")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            detail = f"could not be read: {exc}"
            results.append(IngestedFile(collection=collection, filename=path.name, action="failed", detail=detail))
            continue
        contents[path.name] = (path, data, hashlib.sha256(data).hexdigest())

    # 2. Parse new or changed files first, so a file that fails cannot supersede another.
    prepared: dict[str, _Prepared] = {}
    failed: dict[str, str] = {}
    for name, (path, data, sha256) in contents.items():
        record = stored.get(name)
        if record is None or record.sha256 != sha256:
            try:
                prepared[name] = _prepare(path, data, collection, max_chunk_chars)
            except Exception as exc:  # noqa: BLE001 - any parser failure is reported per file
                failed[name] = _failure_detail(exc)

    # 3. Decide statuses over the files that are usable.
    hashes = {name: sha for name, (_, _, sha) in contents.items() if name not in failed}
    decisions = decide_statuses(hashes, manifest, knowledge.approval, stored)

    # 4. Store what changed.
    to_embed: list[Chunk] = []
    for name, (path, data, sha256) in contents.items():
        record = stored.get(name)
        if name in failed:
            results.append(_report_failure(repository, collection, name, record, failed[name]))
            continue
        decision = decisions[name]
        if record is not None and _is_unchanged(record, sha256, decision):
            results.append(
                IngestedFile(
                    collection=collection,
                    filename=name,
                    document_id=record.id,
                    status=record.status,
                    action="unchanged",
                    findings=record.finding_count,
                )
            )
            to_embed.extend(repository.list_chunks(record.id))
            continue
        item = prepared.get(name)
        if item is None:  # same bytes, new manifest metadata: rebuild from the file
            try:
                item = _prepare(path, data, collection, max_chunk_chars)
            except Exception as exc:  # noqa: BLE001
                results.append(_report_failure(repository, collection, name, record, _failure_detail(exc)))
                continue
        document = item.document
        if decision.entry is not None:
            document = document.model_copy(update={"title": decision.entry.label})
        saved = repository.save_document(
            document, item.chunks, item.findings, status=decision.status, entry=decision.entry, status_reason=decision.reason
        )
        if record is not None and record.id != saved.id:
            _drop_vectors(vectors, record.id, warnings)
        results.append(
            IngestedFile(
                collection=collection,
                filename=name,
                document_id=saved.id,
                status=saved.status,
                action="added" if record is None else "updated",
                findings=len(item.findings),
                detail=decision.reason,
            )
        )
        to_embed.extend(item.chunks)

    # 5. Files that left the folder.
    for name, record in sorted(stored.items()):
        if name in contents or any(r.filename == name for r in results):
            continue
        repository.delete_document(record.id)
        _drop_vectors(vectors, record.id, warnings)
        results.append(
            IngestedFile(
                collection=collection,
                filename=name,
                document_id=record.id,
                action="removed",
                detail="no longer in the source folder",
            )
        )

    # 6. Vectors for chunks that lack them.
    _embed_missing(to_embed, vectors, embedder, warnings)
    results.sort(key=lambda f: f.filename.casefold())
    return IngestReport(files=tuple(results), warnings=tuple(warnings))


def _report_failure(
    repository: DocumentRepository, collection: str, name: str, record: DocumentRecord | None, detail: str
) -> IngestedFile:
    """Report a failed file; a stored older copy is withdrawn to `pending` rather than left current."""
    status = None
    if record is not None:
        status = record.status
        if record.status in (DocumentStatus.CURRENT, DocumentStatus.SUPERSEDED):
            reason = f"latest file failed to ingest: {detail}"
            status = repository.set_status(record.id, DocumentStatus.PENDING, reason=reason).status
    return IngestedFile(
        collection=collection,
        filename=name,
        document_id=record.id if record else None,
        status=status,
        action="failed",
        detail=detail,
    )


def _drop_vectors(vectors: VectorIndex, document_id: str, warnings: list[str]) -> None:
    if not vectors.available:
        return
    try:
        vectors.delete_for_document(document_id)
    except Exception as exc:  # noqa: BLE001 - the keyword index is already consistent
        warnings.append(f"Could not delete old vectors of document {document_id}: {exc}")


def _embed_missing(chunks: Sequence[Chunk], vectors: VectorIndex, embedder: Embedder, warnings: list[str]) -> None:
    if not chunks:
        return
    if not vectors.available:
        warnings.append("Vector index unavailable: documents were stored for keyword search only; re-run ingest to add vectors.")
        return
    try:
        have = vectors.existing([c.id for c in chunks])
        missing = [c for c in chunks if c.id not in have]
        for start in range(0, len(missing), EMBED_BATCH):
            batch = missing[start : start + EMBED_BATCH]
            embeddings = embedder.embed_documents([embedding_text(c) for c in batch])
            if len(embeddings) != len(batch):
                raise ValueError(f"embedder returned {len(embeddings)} vectors for {len(batch)} texts")
            vectors.upsert(
                [
                    VectorItem(chunk_id=c.id, document_id=c.document_id, collection=c.collection, embedding=tuple(e))
                    for c, e in zip(batch, embeddings, strict=True)
                ]
            )
    except Exception as exc:  # noqa: BLE001 - keyword search still works; the next run retries
        log.warning("embedding failed: %s", exc)
        warnings.append(f"Embedding failed ({type(exc).__name__}: {exc}); keyword search still works. Re-run ingest to retry.")
