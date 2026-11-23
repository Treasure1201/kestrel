"""Settings read from the environment and `.env`.

Every setting has a default, so `Settings(_env_file=None)` works in tests. Relative
paths are resolved against the current working directory, which is the repository
root when commands are run as documented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from kestrel.contracts import Channel, Principal


class Settings(BaseSettings):
    """Application settings. Prefixed `KESTREL_`, except the standard provider key names."""

    model_config = SettingsConfigDict(
        env_prefix="KESTREL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    model: str = "anthropic:claude-sonnet-5-5"
    """`<provider>:<model>` used by bots whose bot.yaml says `${KESTREL_MODEL}`."""
    ollama_base_url: str = "http://127.0.0.1:11434"

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY"))
    openai_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY"))
    ollama_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("OLLAMA_API_KEY"))

    db: Path = Path("data/kestrel.sqlite")
    bots_dir: Path = Path("bots")
    source_dir: Path = Path("data/source")
    config_dir: Path = Path("config")
    default_bot: str = "kestrel-products"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    user: str = "local-user"
    roles: Annotated[tuple[str, ...], NoDecode] = ("call-centre-agent", "broker", "staff", "operator")
    """Comma-separated in the environment: `KESTREL_ROLES=call-centre-agent,broker,operator`. `operator` allows ingest, block and audit administration."""

    timeout_s: float = Field(default=120.0, gt=0)
    """Wall-clock limit per question."""

    @field_validator("roles", mode="before")
    @classmethod
    def _split_roles(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(role.strip() for role in value.split(",") if role.strip())
        return value

    @field_validator("anthropic_api_key", "openai_api_key", "ollama_api_key", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    def principal(self, channel: Channel) -> Principal:
        """The local principal for requests from `channel`."""
        return Principal(user=self.user, roles=frozenset(self.roles), channel=channel)

    def key_present(self, name: str) -> bool:
        """Whether a provider key is set, without exposing it. `name` is e.g. `anthropic_api_key`."""
        value = getattr(self, name, None)
        return isinstance(value, SecretStr) and bool(value.get_secret_value())
