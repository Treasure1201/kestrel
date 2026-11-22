"""`AssistantService.compare_editions`: what changed between two editions of one family."""

from __future__ import annotations

from datetime import date

from kestrel.contracts import DocumentRecord, DocumentStatus, EditionComparison, Principal
from kestrel.editions import compare_sections
from kestrel.errors import InvalidInputError
from kestrel.service.runtime import Runtime

COMPARABLE = frozenset({DocumentStatus.CURRENT, DocumentStatus.SUPERSEDED})
"""Blocked and pending documents are never shown, so they are never compared either."""


def _order(document: DocumentRecord) -> tuple[bool, date, str]:
    return (document.effective_from is None, document.effective_from or date.min, document.edition or "")


def family_editions(documents: list[DocumentRecord], document: DocumentRecord) -> list[DocumentRecord]:
    """Every edition in the same collection and family as `document`, oldest first (just itself without a family)."""
    if not document.family:
        return [document]
    family = [d for d in documents if d.family == document.family and d.collection == document.collection]
    return sorted(family, key=_order)


def edition_pair(document: DocumentRecord, editions: list[DocumentRecord]) -> tuple[DocumentRecord, DocumentRecord] | None:
    """(older, newer): the edition `document` supersedes, else the one that supersedes it, else its neighbour by date."""
    others = [d for d in editions if d.id != document.id and d.status in COMPARABLE]
    if not others:
        return None
    if document.supersedes:
        older = next((d for d in others if d.edition == document.supersedes), None)
        if older is not None:
            return older, document
    newer = next((d for d in others if d.supersedes and d.supersedes == document.edition), None)
    if newer is not None:
        return document, newer
    ordered = sorted([*others, document], key=_order)
    index = ordered.index(document)
    return (ordered[index - 1], document) if index > 0 else (document, ordered[index + 1])


def compare_editions(runtime: Runtime, bot_id: str, document_id: str, principal: Principal) -> EditionComparison:
    """Compare a document with the other edition of its family; deterministic, no model."""
    bot = runtime.bot_for(bot_id, principal)
    documents = runtime.store.documents.list_documents(bot.knowledge.collections)
    document = next((d for d in documents if d.id == document_id), None)
    if document is None:
        raise InvalidInputError(f"Unknown document {document_id!r}.")
    editions = family_editions(documents, document)
    base = {"bot_id": bot.id, "document_id": document.id, "family": document.family, "editions": tuple(editions)}
    if document.status not in COMPARABLE:
        return EditionComparison(**base, note=f"{document.filename} is {document.status.value}; only current and superseded editions are compared.")
    pair = edition_pair(document, editions)
    if pair is None:
        return EditionComparison(**base, note=f"{document.filename} has no other current or superseded edition to compare with.")
    older, newer = pair
    repository = runtime.store.documents
    changes = compare_sections(repository.list_sections(older.id), repository.list_sections(newer.id))
    note = None if changes else f"The text of {older.filename} and {newer.filename} is the same, section by section."
    return EditionComparison(**base, older=older, newer=newer, changes=tuple(changes), note=note)
