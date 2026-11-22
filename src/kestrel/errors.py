"""The one error type the application raises on purpose, with user-safe categories.

Every error a user can cause, or that the environment can cause (a missing key, an
unreachable model server), is a `KestrelError`. Front ends show `user_message` and
exit non-zero; they never show a traceback. `detail` is for logs and `kestrel doctor`
and must never contain secrets or unscreened personal information.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    """Why a request failed. Stored in the audit log and reported by evaluation."""

    CONFIGURATION = "configuration"
    """Settings, bot.yaml, manifest or MCP server definitions are missing or invalid."""
    ACCESS_DENIED = "access_denied"
    """The principal's roles do not allow this bot or this operation."""
    PRIVACY_BLOCKED = "privacy_blocked"
    """The bot's privacy policy blocks a question that contains personal information."""
    EGRESS_DENIED = "egress_denied"
    """The bot's classification forbids sending text to an external destination."""
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    """The model provider or an MCP server could not be reached or rejected the call."""
    BUDGET_EXCEEDED = "budget_exceeded"
    """The per-question budget (model calls, searches, repairs or wall-clock) ran out."""
    NOT_INGESTED = "not_ingested"
    """The bot's collections have no searchable documents yet; run `kestrel ingest`."""
    INVALID_INPUT = "invalid_input"
    """The request itself is malformed: empty question, unknown id, value out of range."""
    INTERNAL = "internal"
    """An unexpected failure. The request fails closed and is audited as an error."""


class KestrelError(Exception):
    """An expected failure with a message that is safe to show to the user.

    Args:
        category: Why the request failed.
        user_message: Plain-language explanation, ideally with the fix.
        detail: Optional technical detail for logs; never shown to end users.
    """

    category: ErrorCategory = ErrorCategory.INTERNAL

    def __init__(self, user_message: str, *, category: ErrorCategory | None = None, detail: str | None = None) -> None:
        super().__init__(user_message)
        if category is not None:
            self.category = category
        self.user_message = user_message
        self.detail = detail

    def __repr__(self) -> str:
        return f"{type(self).__name__}(category={self.category.value!r}, user_message={self.user_message!r})"


class ConfigurationError(KestrelError):
    """Settings or configuration files are missing or invalid."""

    category = ErrorCategory.CONFIGURATION


class AccessDeniedError(KestrelError):
    """The principal may not use this bot or perform this operation."""

    category = ErrorCategory.ACCESS_DENIED


class PrivacyBlockedError(KestrelError):
    """The privacy policy blocked the request, or screening failed (fail closed)."""

    category = ErrorCategory.PRIVACY_BLOCKED


class EgressDeniedError(KestrelError):
    """A destination's locality is not allowed by the bot's classification."""

    category = ErrorCategory.EGRESS_DENIED


class ProviderUnavailableError(KestrelError):
    """A model provider or MCP server is unreachable, unauthorised or failing."""

    category = ErrorCategory.PROVIDER_UNAVAILABLE


class BudgetExceededError(KestrelError):
    """The per-question budget ran out before an answer was produced."""

    category = ErrorCategory.BUDGET_EXCEEDED


class NotIngestedError(KestrelError):
    """No searchable documents exist for the bot's collections."""

    category = ErrorCategory.NOT_INGESTED


class InvalidInputError(KestrelError):
    """The request is malformed or names something that does not exist."""

    category = ErrorCategory.INVALID_INPUT
