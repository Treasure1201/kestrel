"""Keep credentials out of logs, audit rows and error results.

Raw exception text from a provider or transport can echo request headers, so it is
never logged or stored. `describe_exception` gives the safe summary (class name and
HTTP status); `redact_secrets` cleans any other text derived from an exception
before it reaches a log, an audit row, an `AskResult` or an MCP error result.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable

from pydantic import SecretStr

REDACTED = "[redacted]"

KEY_VARIABLES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_KEY")
"""Environment variables holding the configured provider keys."""

_KEY_FIELDS = ("anthropic_api_key", "openai_api_key", "ollama_api_key")
_MIN_SECRET_CHARS = 8

_PATTERNS = (
    # An Authorization header, with or without its scheme word.
    re.compile(r"(?i)\b(proxy-)?authorization\b(\s*[\"']?\s*[:=]\s*[\"']?)(?:(?:bearer|basic|token)\s+)?[^\s\"',;}]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"),
    re.compile(
        r"(?i)\b(x-api-key|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|secret|password)"
        r"(\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+"
    ),
)


def _configured_secrets(settings: object | None) -> list[str]:
    values = [os.environ.get(name, "") for name in KEY_VARIABLES]
    for field in _KEY_FIELDS:
        value = getattr(settings, field, None) if settings is not None else None
        if isinstance(value, SecretStr):
            values.append(value.get_secret_value())
    return [v.strip() for v in values if len(v.strip()) >= _MIN_SECRET_CHARS]


def redact_secrets(text: str | None, settings: object | None = None, extra: Iterable[str] = ()) -> str:
    """`text` with bearer tokens, `sk-` keys, `api_key=...`, Authorization headers and configured key values replaced.

    Args:
        text: Any text derived from an exception or an external reply.
        settings: Optional settings whose provider keys are also removed verbatim.
        extra: Further literal secrets to remove.
    """
    if not text:
        return ""
    cleaned = str(text)
    secrets = _configured_secrets(settings) + [s.strip() for s in extra if len(s.strip()) >= _MIN_SECRET_CHARS]
    for secret in sorted(set(secrets), key=len, reverse=True):
        cleaned = cleaned.replace(secret, REDACTED)
    cleaned = _PATTERNS[0].sub(lambda m: f"{m.group(0)[: m.start(2) - m.start(0)]}{m.group(2)}{REDACTED}", cleaned)
    cleaned = _PATTERNS[1].sub(f"Bearer {REDACTED}", cleaned)
    cleaned = _PATTERNS[2].sub(REDACTED, cleaned)
    cleaned = _PATTERNS[3].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", cleaned)
    return cleaned


def http_status(exc: BaseException) -> int | None:
    """The HTTP status an exception carries, if any (`status_code` or `response.status_code`)."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def describe_exception(exc: BaseException, category: str | None = None) -> str:
    """A loggable summary that never includes the exception's text: class, our category, HTTP status."""
    parts = [type(exc).__name__]
    if category:
        parts.append(f"category={category}")
    status = http_status(exc)
    if status is not None:
        parts.append(f"HTTP {status}")
    return " ".join(parts)
