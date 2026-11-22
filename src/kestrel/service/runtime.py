"""Shared parts of the service: settings, registry, store, retriever, screen, models and the event loop."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel

from kestrel.bots import BotRegistry
from kestrel.config import Settings
from kestrel.contracts import BotConfig, Embedder, Principal
from kestrel.contracts.service import OPERATOR_ROLE
from kestrel.errors import AccessDeniedError, BudgetExceededError
from kestrel.llm import resolve_model
from kestrel.privacy import RuleBasedScreen
from kestrel.retrieval import HybridRetriever
from kestrel.store import Store

T = TypeVar("T")
ChatModelFactory = Callable[[str], BaseChatModel]
"""Builds the chat model for a `<provider>:<model>` reference (tests inject a scripted model)."""

_LOOP_GRACE_S = 15.0


class BackgroundLoop:
    """An asyncio event loop on its own daemon thread, owned by the service.

    `run` may be called from any thread, including one that is itself running an event
    loop (the MCP server's), because the coroutine runs on this loop, not the caller's.
    Loop-bound clients (HTTP connection pools) therefore never cross loops.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._main, name="assistant-loop", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _main(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        self._loop.run_forever()

    def run(self, coroutine: Coroutine[Any, Any, T], timeout_s: float) -> T:
        """Run `coroutine` on the service loop and wait for its result."""
        if threading.current_thread() is self._thread:
            raise RuntimeError("BackgroundLoop.run must not be called from the service loop itself")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout_s + _LOOP_GRACE_S)
        except TimeoutError:
            future.cancel()
            raise BudgetExceededError(f"The question took longer than {timeout_s:g} seconds.") from None

    def close(self) -> None:
        """Stop the loop and its thread."""
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)


@dataclass
class Runtime:
    """Everything the service methods share. Each part is safe to use from several threads."""

    settings: Settings
    registry: BotRegistry
    store: Store
    embedder: Embedder
    retriever: HybridRetriever
    screen: RuleBasedScreen
    loop: BackgroundLoop
    chat_model_factory: ChatModelFactory | None = None
    embedder_injected: bool = False
    _models: dict[str, BaseChatModel] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def chat_model(self, ref: str) -> BaseChatModel:
        """The chat model for `ref`: from the injected factory, or resolved and cached per reference."""
        if self.chat_model_factory is not None:
            return self.chat_model_factory(ref)
        with self._lock:
            if ref not in self._models:
                self._models[ref] = resolve_model(ref, self.settings).chat_model
            return self._models[ref]

    def bot_for(self, bot_id: str, principal: Principal) -> BotConfig:
        """The bot, if the principal may use it. Raises InvalidInputError or AccessDeniedError."""
        bot = self.registry.get(bot_id)
        if not principal.may_use(bot.audience):
            raise AccessDeniedError(f"You do not have access to the assistant {bot_id!r}.")
        return bot

    def visible_bots(self, principal: Principal) -> list[BotConfig]:
        """Bots the principal may use."""
        return self.registry.visible_to(principal)

    def require_operator(self, principal: Principal) -> None:
        """Raise AccessDeniedError unless the principal has the operator role."""
        if OPERATOR_ROLE not in principal.roles:
            raise AccessDeniedError(f"This operation needs the {OPERATOR_ROLE!r} role.")

    def bots_for_collection(self, collection: str, principal: Principal) -> list[BotConfig]:
        """Bots the principal may use whose knowledge includes `collection`."""
        return [bot for bot in self.visible_bots(principal) if collection in bot.knowledge.collections]
