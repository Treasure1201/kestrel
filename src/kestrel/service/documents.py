"""Search, sections, documents, findings, ingest, block and unblock, with their access rules."""

from __future__ import annotations

import uuid
from pathlib import Path

from kestrel.contracts import (
    AuditEntry,
    BotConfig,
    DocumentRecord,
    DocumentStatus,
    Finding,
    IngestedFile,
    IngestReport,
    Principal,
    SearchResult,
    SectionView,
)
from kestrel.contracts.service import MAX_QUESTION_CHARS
from kestrel.errors import AccessDeniedError, InvalidInputError, PrivacyBlockedError
from kestrel.ingest import decide_statuses, ingest_collection, load_manifest
from kestrel.ingest.manifest import Manifest
from kestrel.service.ask import screen_question
from kestrel.service.runtime import Runtime

MAX_SEARCH_LIMIT = 50
MAX_REASON_CHARS = 500


def search(runtime: Runtime, bot_id: str, query: str, principal: Principal, limit: int = 8) -> SearchResult:
    """Screened hybrid search over the bot's documents in the scope the query implies."""
    bot = runtime.bot_for(bot_id, principal)
    if not isinstance(query, str) or not query.strip():
        raise InvalidInputError("The search query is empty.")
    if len(query) > MAX_QUESTION_CHARS:
        raise InvalidInputError(f"The search query is longer than {MAX_QUESTION_CHARS} characters.")
    if not 1 <= int(limit) <= MAX_SEARCH_LIMIT:
        raise InvalidInputError(f"The limit must be between 1 and {MAX_SEARCH_LIMIT}.")
    screened = screen_question(runtime, query, bot.privacy)
    if screened.found_personal_information and bot.privacy.on_personal_information == "block":
        raise PrivacyBlockedError("The query contains personal information, which this assistant does not accept.")
    decision = runtime.retriever.resolve_scope(screened.text, bot.knowledge)
    if decision.scope is None:
        return SearchResult(clarification=decision.clarification)
    return runtime.retriever.search(screened.text, decision.scope, int(limit))


def _document_for_principal(runtime: Runtime, document_id: str, principal: Principal) -> tuple[DocumentRecord, list[BotConfig]]:
    document = runtime.store.documents.get_document(document_id)
    bots = runtime.bots_for_collection(document.collection, principal) if document else []
    if document is None or not bots:
        # Same message whether the document is missing or out of reach, so ids cannot be probed.
        raise InvalidInputError(f"Unknown document {document_id!r}.")
    return document, bots


def get_section(runtime: Runtime, bot_id: str, section_id: str, principal: Principal) -> SectionView:
    """One whole section of the bot's collections, unless its document is blocked or pending."""
    bot = runtime.bot_for(bot_id, principal)
    section = runtime.store.documents.get_section(section_id)
    document = runtime.store.documents.get_document(section.document_id) if section else None
    if (
        section is None
        or document is None
        or document.collection not in bot.knowledge.collections
        or document.status in {DocumentStatus.BLOCKED, DocumentStatus.PENDING}
    ):
        raise InvalidInputError(f"Unknown section {section_id!r}.")
    return section


def list_documents(runtime: Runtime, bot_id: str, principal: Principal) -> list[DocumentRecord]:
    bot = runtime.bot_for(bot_id, principal)
    return runtime.store.documents.list_documents(bot.knowledge.collections)


def list_findings(runtime: Runtime, bot_id: str, principal: Principal, document_id: str | None = None) -> list[Finding]:
    bot = runtime.bot_for(bot_id, principal)
    ids = [d.id for d in runtime.store.documents.list_documents(bot.knowledge.collections)]
    if document_id is not None:
        if document_id not in ids:
            raise InvalidInputError(f"Unknown document {document_id!r}.")
        ids = [document_id]
    return runtime.store.documents.findings(ids)


def ingest(runtime: Runtime, principal: Principal, bot_id: str | None = None) -> IngestReport:
    """Ingest each bot's collections from `<source_dir>/<collection>/` with the bot's manifest."""
    runtime.require_operator(principal)
    bots = [runtime.bot_for(bot_id, principal)] if bot_id is not None else runtime.visible_bots(principal)
    if not bots:
        raise AccessDeniedError("You do not have access to any assistant.")
    files: list[IngestedFile] = []
    warnings: list[str] = []
    done: set[str] = set()
    for bot in bots:
        for collection in bot.knowledge.collections:
            if collection in done:
                warnings.append(f"Collection {collection!r} is shared by several bots; it was ingested once, with the manifest of the first.")
                continue
            done.add(collection)
            report = ingest_collection(
                Path(runtime.settings.source_dir) / collection, collection, bot.knowledge, bot.knowledge.manifest,
                runtime.store.documents, runtime.store.vectors, runtime.embedder,
            )
            files.extend(report.files)
            warnings.extend(report.warnings)
    return IngestReport(files=tuple(files), warnings=tuple(warnings))


def _reason(runtime: Runtime, bot: BotConfig, reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidInputError("Give a reason.")
    if len(reason) > MAX_REASON_CHARS:
        raise InvalidInputError(f"The reason is longer than {MAX_REASON_CHARS} characters.")
    return screen_question(runtime, reason.strip(), bot.privacy).text


def _audit_status(runtime: Runtime, kind: str, document: DocumentRecord, bot: BotConfig, principal: Principal, reason: str) -> None:
    runtime.store.audit.append(AuditEntry(
        kind=kind,  # type: ignore[arg-type]
        trace_id=uuid.uuid4().hex,
        bot_id=bot.id, channel=principal.channel, user=principal.user, roles=tuple(sorted(principal.roles)),
        document_id=document.id, detail=reason,
    ))


def block_document(runtime: Runtime, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
    """Withdraw a document from search immediately, and audit it."""
    runtime.require_operator(principal)
    document, bots = _document_for_principal(runtime, document_id, principal)
    text = _reason(runtime, bots[0], reason)
    updated = runtime.store.documents.set_status(document.id, DocumentStatus.BLOCKED, reason=text)
    _audit_status(runtime, "block", document, bots[0], principal, text)
    return updated


def manifest_status(bot: BotConfig, document: DocumentRecord, siblings: list[DocumentRecord]) -> tuple[DocumentStatus, str | None]:
    """The status the bot's manifest gives a document, ignoring any block."""
    manifest = load_manifest(bot.knowledge.manifest) if bot.knowledge.manifest else Manifest()
    files = {d.filename: d.sha256 for d in siblings}
    files[document.filename] = document.sha256
    decision = decide_statuses(files, manifest, bot.knowledge.approval)[document.filename]
    return decision.status, decision.reason


def unblock_document(runtime: Runtime, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
    """Restore a blocked document to the status its manifest entry gives it, and audit it."""
    runtime.require_operator(principal)
    document, bots = _document_for_principal(runtime, document_id, principal)
    if document.status != DocumentStatus.BLOCKED:
        raise InvalidInputError(f"Document {document_id!r} is not blocked (it is {document.status.value}).")
    text = _reason(runtime, bots[0], reason)
    siblings = runtime.store.documents.list_documents([document.collection])
    status, why = manifest_status(bots[0], document, siblings)
    updated = runtime.store.documents.set_status(document.id, status, reason=why)
    _audit_status(runtime, "unblock", document, bots[0], principal, text)
    return updated
