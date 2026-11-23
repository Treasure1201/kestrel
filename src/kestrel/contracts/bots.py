"""Bot configuration (`bots/<id>/bot.yaml`) and centrally approved MCP servers.

These models describe configuration *after* loading: `${ENV}` references are
expanded, relative paths are resolved against the bot folder, and the prompt file
has been read. The loader (bot registry) owns that translation; these models own
validation, so an invalid file fails with the field name.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from kestrel.contracts._base import Classification, Frozen, Locality, ProviderName

BOT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"
"""Bot ids, collection ids and MCP server names: lower-case words joined by hyphens."""

BUILTIN_TOOLS: tuple[str, ...] = ("search_documents", "get_table", "calculate")
"""The read-only tools the agent package provides. A bot lists the ones it uses."""

MAX_EXAMPLES = 6
"""Most example questions a bot may list."""
MAX_EXAMPLE_CHARS = 120
"""Longest example question, so each fits on a button."""


class KnowledgeConfig(Frozen):
    """Which documents a bot may read, and which of them it sees by default."""

    collections: tuple[str, ...] = Field(min_length=1)
    """Collection ids. Search filters by these in SQL before ranking."""
    approval: Literal["required", "none"] = "required"
    """`required`: a document must be listed and approved in the bot's manifest to be searchable."""
    default_scope: Literal["current"] = "current"
    """Only `current` documents are searched unless the question names an edition or date."""
    manifest: Path | None = None
    """Absolute path to the bot's `manifest.yaml`, or None when approval is `none`."""

    @field_validator("collections")
    @classmethod
    def _valid_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for collection in value:
            if not re.match(BOT_ID_PATTERN, collection):
                raise ValueError(f"invalid collection id {collection!r}")
        if len(set(value)) != len(value):
            raise ValueError("collections must not repeat")
        return value


class ModelPolicy(Frozen):
    """Which model the bot uses and the per-question budget."""

    default: str = Field(min_length=3)
    """`<provider>:<model>`, usually expanded from `${KESTREL_MODEL}`."""
    allowed_providers: tuple[ProviderName, ...] = ("ollama", "ollama-cloud", "anthropic", "openai")
    max_model_calls: int = Field(default=12, ge=1, le=100)
    """Total model calls per question, including structured-output retries and the repair turn."""
    max_searches: int = Field(default=6, ge=1, le=50)
    """Total `search_documents` calls per question."""
    timeout_s: float | None = Field(default=None, gt=0)
    """Wall-clock limit per question; None means use `KESTREL_TIMEOUT_S`."""


class RedactionPattern(Frozen):
    """An operator-supplied pattern screened in addition to the built-in rules."""

    kind: str = Field(min_length=1, max_length=40)
    """Label recorded when it matches, for example `staff_number`."""
    pattern: str = Field(min_length=1)
    """Python regular expression."""

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"not a valid regular expression: {exc}") from None
        return value


class PrivacyPolicy(Frozen):
    """How personal information in questions is handled, and where text may go."""

    classification: Classification = "internal"
    """`confidential` bots may use on-device destinations only (models and MCP servers)."""
    on_personal_information: Literal["redact", "block"] = "redact"
    audit_question_text: Literal["redacted", "none"] = "none"
    """`redacted`: store the screened question in the audit log. `none`: store only its digest."""
    retention_days: int = Field(default=30, ge=1, le=3650)
    """Audit rows older than this are pruned by `kestrel audit prune`."""
    extra_patterns: tuple[RedactionPattern, ...] = ()


class VerificationPolicy(Frozen):
    """What happens when the evidence checks fail after the repair turn."""

    on_failure: Literal["withhold", "warn"] = "withhold"
    max_repairs: int = Field(default=1, ge=0, le=3)


class EvaluationConfig(Frozen):
    """Where the bot's evaluation material lives."""

    gold: Path | None = None
    """Absolute path to the gold file, if the bot has one."""


class BotConfig(Frozen):
    """One bot, fully loaded and validated. Bots are configuration, never code."""

    id: str = Field(pattern=BOT_ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    audience: frozenset[str] = Field(min_length=1)
    """Roles allowed to use this bot. Checked on every service method."""
    knowledge: KnowledgeConfig
    prompt_path: Path
    """Absolute path of the prompt file named by `prompt:` in bot.yaml."""
    prompt_text: str = Field(min_length=1)
    """Persona and domain guidance, read from `prompt_path`."""
    tools: tuple[str, ...] = BUILTIN_TOOLS
    """Built-in tool names; each must be one of `BUILTIN_TOOLS`."""
    mcp_servers: tuple[str, ...] = ()
    """Names of servers defined in `config/mcp_servers.yaml`."""
    skills: tuple[Path, ...] = ()
    """Absolute skill folders (each holding `<skill>/SKILL.md`), all inside `root`."""
    model: ModelPolicy
    privacy: PrivacyPolicy = PrivacyPolicy()
    verification: VerificationPolicy = VerificationPolicy()
    evaluation: EvaluationConfig = EvaluationConfig()
    examples: tuple[str, ...] = Field(default=(), max_length=MAX_EXAMPLES)
    """Up to six short example questions, shown as buttons and by `kestrel bots --examples`."""
    root: Path
    """Absolute path of the bot folder, `bots/<id>/`."""

    @field_validator("examples")
    @classmethod
    def _short_examples(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(" ".join(item.split()) for item in value)
        for item in cleaned:
            if not item:
                raise ValueError("an example question must not be empty")
            if len(item) > MAX_EXAMPLE_CHARS:
                raise ValueError(f"example {item[:40]!r}... is longer than {MAX_EXAMPLE_CHARS} characters")
        return cleaned

    @field_validator("tools")
    @classmethod
    def _known_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(BUILTIN_TOOLS))
        if unknown:
            raise ValueError(f"unknown tools {unknown}; available: {list(BUILTIN_TOOLS)}")
        return value

    @model_validator(mode="after")
    def _skills_inside_root(self) -> BotConfig:
        root = self.root.resolve()
        for folder in self.skills:
            if not folder.resolve().is_relative_to(root):
                raise ValueError(f"skills folder {folder} is outside the bot folder {root}")
        return self


class ReadOnlyAttestation(Frozen):
    """A reviewer's statement that every allow-listed tool of a server only reads."""

    reviewed_by: str = Field(min_length=1)
    reviewed_on: str = Field(min_length=1)
    """ISO date of the review."""
    statement: str = Field(min_length=1)


class McpServerSpec(Frozen):
    """An MCP server approved once in `config/mcp_servers.yaml`; bots opt in by name.

    An allow-list limits which tools can be called; it cannot make a tool harmless,
    which is why the read-only attestation is a human step. Tool output is treated as
    untrusted data, and tool arguments pass the privacy gate.
    """

    name: str = Field(pattern=BOT_ID_PATTERN)
    transport: Literal["stdio", "http"]
    command: str | None = None
    """Executable for stdio. `python` is resolved to the running interpreter by the loader."""
    args: tuple[str, ...] = ()
    url: str | None = None
    """Endpoint for streamable HTTP."""
    allowed_tools: tuple[str, ...] = Field(min_length=1)
    """The only tool names exposed to the agent; any other tool on the server is dropped."""
    locality: Locality
    read_only: ReadOnlyAttestation

    @model_validator(mode="after")
    def _transport_fields(self) -> McpServerSpec:
        if self.transport == "stdio" and (not self.command or self.url):
            raise ValueError("a stdio server needs `command` and no `url`")
        if self.transport == "http" and (not self.url or self.command):
            raise ValueError("an http server needs `url` and no `command`")
        return self
