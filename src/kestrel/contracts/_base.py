"""Base model and the small value types every other contract module uses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Channel = Literal["cli", "ui", "mcp", "eval"]
"""The front end a request arrived through. Recorded in every audit row."""

Locality = Literal["on-device", "external"]
"""Where a destination that can receive text runs. `confidential` bots allow only `on-device`."""

ProviderName = Literal["ollama", "ollama-cloud", "anthropic", "openai"]
"""Model provider prefixes accepted in `KESTREL_MODEL=<provider>:<model>`."""

Classification = Literal["public", "internal", "confidential"]
"""A bot's data classification. `confidential` locks the bot to on-device destinations."""


class Frozen(BaseModel):
    """Immutable, strict base for every contract model.

    Unknown fields are rejected, so a typo in a YAML file or a renamed field fails
    loudly with the field name instead of being silently ignored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Principal(Frozen):
    """Who is asking, and through which front end.

    Every `AssistantService` method takes one. Access checks compare `roles` with a
    bot's `audience`. Locally it is built from settings; production maps it
    from single sign-on.
    """

    user: str = Field(min_length=1, max_length=200)
    roles: frozenset[str] = frozenset()
    channel: Channel

    @field_validator("roles", mode="before")
    @classmethod
    def _strip_roles(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, (list, tuple, set, frozenset)):
            return frozenset(str(role).strip() for role in value if str(role).strip())
        return value

    def may_use(self, audience: frozenset[str] | tuple[str, ...]) -> bool:
        """Return True when at least one of the principal's roles is in `audience`."""
        return bool(self.roles.intersection(audience))
