"""Settings, the assistant service, the local principal and friendly error handling.

The service factory is imported lazily, so the app starts (and shows a clear message)
even when the service cannot be built. Tests call `use_service` to inject a fake.
The real service is cached with `st.cache_resource`; it owns its own event loop, so
nothing loop-bound is created or cached here.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import streamlit as st
from pydantic import ValidationError

from kestrel.config import Settings
from kestrel.contracts import OPERATOR_ROLE, AssistantService, BotSummary, Principal
from kestrel.errors import ConfigurationError, KestrelError

T = TypeVar("T")
log = logging.getLogger("kestrel.app")

ServiceBuilder = Callable[[Settings], AssistantService]

_builder: ServiceBuilder | None = None
_settings: Settings | None = None
_results_root: Path | None = None


def use_service(builder: ServiceBuilder | None, settings: Settings | None = None, results_root: Path | None = None) -> None:
    """Replace the service builder (and optionally settings and the results folder). For tests."""
    global _builder, _settings, _results_root
    _builder, _settings, _results_root = builder, settings, results_root


def settings() -> Settings:
    """Settings from the environment and `.env`."""
    if _settings is not None:
        return _settings
    try:
        return Settings()
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) or "?" for e in exc.errors())
        raise ConfigurationError(
            f"Invalid setting ({fields}); check your environment variables and .env file.", detail=str(exc)
        ) from exc


@st.cache_resource(show_spinner="Starting the assistant service...")
def _cached_service() -> AssistantService:
    try:
        from kestrel.service import build_service  # noqa: PLC0415 - lazy by design
    except ImportError as exc:
        raise ConfigurationError(
            "The assistant service is not available in this installation; reinstall with `uv sync`.",
            detail=repr(exc),
        ) from exc
    return build_service(settings())


def service() -> AssistantService:
    """The assistant service: the injected one in tests, otherwise the cached real one."""
    if _builder is not None:
        return _builder(settings())
    return _cached_service()


def principal() -> Principal:
    """The local principal (KESTREL_USER, KESTREL_ROLES) on the `ui` channel."""
    return settings().principal("ui")


def is_operator(who: Principal) -> bool:
    """Whether the principal holds the operator role."""
    return OPERATOR_ROLE in who.roles


def results_root() -> Path:
    """Folder that holds evaluation run folders and flags (`KESTREL_RESULTS_DIR`, default `results`)."""
    if _results_root is not None:
        return _results_root
    return Path(os.environ.get("KESTREL_RESULTS_DIR", "results"))


def attempt(func: Callable[..., T], *args: Any, default: T | None = None, where: Any = None, **kwargs: Any) -> T | None:
    """Call `func`; on failure show a friendly message (never a traceback) and return `default`."""
    target = where if where is not None else st
    try:
        return func(*args, **kwargs)
    except KestrelError as exc:
        log.info("service error %s: %s", exc.category.value, exc.detail or exc.user_message)
        target.error(exc.user_message, icon=":material/error:")
    except Exception as exc:  # noqa: BLE001 - last line of defence for the user
        log.exception("unexpected error in the app")
        target.error(
            f"Something went wrong ({type(exc).__name__}). The details are in the server log.",
            icon=":material/error:",
        )
    return default


def context() -> tuple[AssistantService, Principal, BotSummary] | None:
    """Service, principal and the bot chosen in the sidebar; None (after a message) when unavailable."""
    try:
        svc = service()
    except Exception:  # noqa: BLE001 - the entry script has already shown the message
        return None
    bot = st.session_state.get("_bot")
    if bot is None:
        st.info("No assistant is available to your roles, so there is nothing to show yet.", icon=":material/info:")
        return None
    return svc, principal(), bot


def memo(key: str, func: Callable[[], T], ttl_s: float = 60.0) -> T:
    """Per-session cache of a service read, so reruns do not repeat it. Errors are not cached."""
    store: dict[str, tuple[float, Any]] = st.session_state.setdefault("_memo", {})
    hit = store.get(key)
    if hit is not None and time.monotonic() - hit[0] < ttl_s:
        return hit[1]
    value = func()
    store[key] = (time.monotonic(), value)
    return value


def forget(prefix: str = "") -> None:
    """Drop cached reads whose key starts with `prefix` (all when empty)."""
    store: dict[str, Any] = st.session_state.setdefault("_memo", {})
    for key in [k for k in store if k.startswith(prefix)]:
        del store[key]
