"""`AssistantService`: the only entry point the CLI, the Streamlit app and the MCP server use.

    from kestrel.service import build_service
    service = build_service()
    result = service.ask("my-bot", "What is the waiting period?", settings.principal("cli"))

Every method is synchronous and safe to call from several threads, and from inside
another running event loop: the agent runs on an event loop the service owns.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kestrel.bots import BotRegistry
from kestrel.config import Settings
from kestrel.contracts import (
    AskResult,
    AuditRecord,
    AuditVerification,
    BotSummary,
    DocumentRecord,
    DoctorReport,
    Embedder,
    Finding,
    IngestReport,
    Principal,
    PruneResult,
    SearchResult,
    SectionView,
)
from kestrel.errors import AccessDeniedError, InvalidInputError
from kestrel.llm import disable_third_party_tracing
from kestrel.privacy import RuleBasedScreen
from kestrel.retrieval import FastEmbedEmbedder, open_retriever
from kestrel.service import documents as _documents
from kestrel.service.ask import ask as _ask
from kestrel.service.doctor import doctor as _doctor
from kestrel.service.runtime import BackgroundLoop, ChatModelFactory, Runtime
from kestrel.store import open_store

MAX_AUDIT_LIMIT = 500


class LocalAssistantService:
    """The `AssistantService` over a local SQLite store. Build it with `build_service`."""

    def __init__(self, runtime: Runtime) -> None:
        self._rt = runtime

    @property
    def settings(self) -> Settings:
        """The settings the service was built with."""
        return self._rt.settings

    def ask(self, bot_id: str, question: str, principal: Principal, model: str | None = None) -> AskResult:
        return _ask(self._rt, bot_id, question, principal, model)

    def search(self, bot_id: str, query: str, principal: Principal, limit: int = 8) -> SearchResult:
        return _documents.search(self._rt, bot_id, query, principal, limit)

    def get_section(self, bot_id: str, section_id: str, principal: Principal) -> SectionView:
        return _documents.get_section(self._rt, bot_id, section_id, principal)

    def list_bots(self, principal: Principal) -> list[BotSummary]:
        return [
            BotSummary(
                id=bot.id, name=bot.name, description=bot.description, classification=bot.privacy.classification,
                model=bot.model.default, collections=bot.knowledge.collections,
            )
            for bot in self._rt.visible_bots(principal)
        ]

    def list_documents(self, bot_id: str, principal: Principal) -> list[DocumentRecord]:
        return _documents.list_documents(self._rt, bot_id, principal)

    def list_findings(self, bot_id: str, principal: Principal, document_id: str | None = None) -> list[Finding]:
        return _documents.list_findings(self._rt, bot_id, principal, document_id)

    def ingest(self, principal: Principal, bot_id: str | None = None) -> IngestReport:
        return _documents.ingest(self._rt, principal, bot_id)

    def block_document(self, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
        return _documents.block_document(self._rt, document_id, principal, reason)

    def unblock_document(self, document_id: str, principal: Principal, reason: str) -> DocumentRecord:
        return _documents.unblock_document(self._rt, document_id, principal, reason)

    def affected_answers(self, document_id: str, principal: Principal) -> list[AuditRecord]:
        document = self._rt.store.documents.get_document(document_id)
        bots = self._rt.bots_for_collection(document.collection, principal) if document else []
        if document is None or not bots:
            raise InvalidInputError(f"Unknown document {document_id!r}.")
        return self._rt.store.audit.answers_citing(document_id, bot_ids=[bot.id for bot in self._rt.visible_bots(principal)])

    def audit_tail(self, principal: Principal, limit: int = 20, bot_id: str | None = None) -> list[AuditRecord]:
        if not 1 <= int(limit) <= MAX_AUDIT_LIMIT:
            raise InvalidInputError(f"The limit must be between 1 and {MAX_AUDIT_LIMIT}.")
        if bot_id is not None:
            bot_ids = [self._rt.bot_for(bot_id, principal).id]
        else:
            bot_ids = [bot.id for bot in self._rt.visible_bots(principal)]
            if not bot_ids:
                raise AccessDeniedError("You do not have access to any assistant, so there is no audit trail to show.")
        return self._rt.store.audit.tail(int(limit), bot_ids=bot_ids)

    def audit_verify(self, principal: Principal) -> AuditVerification:
        self._rt.require_operator(principal)
        return self._rt.store.audit.verify()

    def audit_prune(self, principal: Principal, bot_id: str | None = None) -> list[PruneResult]:
        self._rt.require_operator(principal)
        bots = [self._rt.bot_for(bot_id, principal)] if bot_id is not None else self._rt.visible_bots(principal)
        now = datetime.now(UTC)
        return [self._rt.store.audit.prune(bot.id, now - timedelta(days=bot.privacy.retention_days)) for bot in bots]

    def doctor(self) -> DoctorReport:
        return _doctor(self._rt)

    def close(self) -> None:
        """Stop the service's event loop. The service must not be used afterwards."""
        self._rt.loop.close()


def build_service(
    settings: Settings | None = None,
    *,
    chat_model_factory: ChatModelFactory | None = None,
    embedder: Embedder | None = None,
) -> LocalAssistantService:
    """Build the service from settings (`.env` and the environment when None).

    Args:
        settings: Application settings.
        chat_model_factory: Builds the chat model for a `<provider>:<model>` reference;
            by default models are resolved from settings. Tests inject a scripted model.
        embedder: The embedder; by default the fastembed model named in settings
            (loaded on first use, not here).
    """
    disable_third_party_tracing()
    settings = settings or Settings()
    chosen = embedder or FastEmbedEmbedder.from_settings(settings)
    store = open_store(settings, chosen)
    runtime = Runtime(
        settings=settings,
        registry=BotRegistry(settings),
        store=store,
        embedder=chosen,
        retriever=open_retriever(store, chosen),
        screen=RuleBasedScreen(),
        loop=BackgroundLoop(),
        chat_model_factory=chat_model_factory,
        embedder_injected=embedder is not None,
    )
    return LocalAssistantService(runtime)


__all__ = ["LocalAssistantService", "build_service"]
