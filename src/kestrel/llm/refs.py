"""`<provider>:<model>` references, as used in `KESTREL_MODEL` and bot.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import get_args

from kestrel.contracts import ProviderName
from kestrel.errors import ConfigurationError

PROVIDERS: tuple[str, ...] = get_args(ProviderName)
"""Accepted provider prefixes, in the order they are documented."""

_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
_EXAMPLES = "ollama:gpt-oss:20b, ollama-cloud:gpt-oss:120b, anthropic:claude-sonnet-5-5, openai:gpt-6.1-sol"


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A parsed model reference. The model part may itself contain colons (`gpt-oss:20b`)."""

    provider: ProviderName
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_model_ref(value: str | ModelRef) -> ModelRef:
    """Parse `<provider>:<model>`, splitting on the first colon only.

    Raises:
        ConfigurationError: the reference is empty, has no colon, names an unknown
            provider, or has an invalid model name. The message gives valid examples.
    """
    if isinstance(value, ModelRef):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"No model is configured. Set KESTREL_MODEL=<provider>:<model> in .env, for example {_EXAMPLES}.")
    text = value.strip()
    provider, sep, model = text.partition(":")
    if not sep or not provider or not model:
        raise ConfigurationError(
            f"Model reference {text!r} is not in the form <provider>:<model>. "
            f"Set KESTREL_MODEL in .env to one of, for example: {_EXAMPLES}."
        )
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        raise ConfigurationError(
            f"Unknown model provider {provider!r} in {text!r}. Use one of: {', '.join(PROVIDERS)} "
            f"(for example {_EXAMPLES})."
        )
    model = model.strip()
    if not _MODEL_NAME.fullmatch(model):
        raise ConfigurationError(
            f"Model name {model!r} in {text!r} is not valid. Use the provider's exact model id, for example {_EXAMPLES}."
        )
    return ModelRef(provider=provider, model=model)  # type: ignore[arg-type]
