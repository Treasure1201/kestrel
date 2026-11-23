"""Model defaults and the tested-models list, read from `config/models.yaml`.

The file is optional: without it, built-in defaults apply and no model counts as
tested. A file that exists but is invalid fails with the field name.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError

from kestrel.contracts import Frozen, ProviderName
from kestrel.errors import ConfigurationError

MODELS_FILE = "models.yaml"
MAX_CONFIG_BYTES = 256 * 1024


class RequestDefaults(Frozen):
    """Limits applied to every hosted and local model request."""

    request_timeout_s: float = Field(default=120.0, gt=0, le=3600)
    max_retries: int = Field(default=2, ge=0, le=10)
    max_output_tokens: int = Field(default=8192, ge=256, le=128_000)


class OllamaDefaults(Frozen):
    """Ollama options. `num_ctx` is always sent: Ollama silently truncates prompts otherwise."""

    num_ctx: int = Field(default=32768, ge=2048, le=1_048_576)
    num_predict: int = Field(default=8192, ge=256, le=131_072)
    keep_alive: str = "30m"
    hosted_model_suffixes: tuple[str, ...] = ("-cloud", ":cloud")
    """Model-name endings that Ollama passes through to its hosted service: external even on a loopback endpoint."""

    def is_hosted(self, model: str) -> bool:
        """Whether a local Ollama would forward `model` to a hosted service."""
        name = model.strip().lower()
        return any(name.endswith(suffix.lower()) for suffix in self.hosted_model_suffixes if suffix)


class ModelCatalog(Frozen):
    """Contents of `config/models.yaml`."""

    defaults: RequestDefaults = RequestDefaults()
    ollama: OllamaDefaults = OllamaDefaults()
    fixed_temperature: dict[ProviderName, tuple[str, ...]] = Field(default_factory=dict)
    """Model-name prefixes that reject a non-default temperature (`*` matches every model)."""
    tested: dict[ProviderName, tuple[str, ...]] = Field(default_factory=dict)
    """Models the repository was evaluated with, per provider."""

    def is_tested(self, provider: str, model: str) -> bool:
        """Whether `provider:model` is on the tested list."""
        return model in self.tested.get(provider, ())  # type: ignore[call-overload]

    def accepts_temperature(self, provider: str, model: str) -> bool:
        """False when the model only accepts its default temperature."""
        prefixes = self.fixed_temperature.get(provider, ())  # type: ignore[call-overload]
        return not any(prefix == "*" or model.startswith(prefix) for prefix in prefixes)


def read_yaml_mapping(path: Path, what: str) -> dict[str, Any]:
    """Read a small YAML file that must hold a mapping. Errors name the file and the fix."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"Cannot read {what} at {path}: {exc.strerror}.") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigurationError(f"{what} at {path} is larger than {MAX_CONFIG_BYTES // 1024} KB.")
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"{what} at {path} is not valid YAML: {exc}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"{what} at {path} must be a mapping of keys to values.")
    return data


def describe_validation_error(exc: ValidationError, rename: dict[str, str] | None = None) -> str:
    """One line per problem: `field.path: message`. `rename` maps loaded field names back to file keys."""
    rename = rename or {}
    lines = []
    for error in exc.errors():
        parts = [str(part) for part in error["loc"]]
        if parts and parts[0] in rename:
            parts[0] = rename[parts[0]]
        field = ".".join(parts) or "(file)"
        message = error["msg"].removeprefix("Value error, ")
        lines.append(f"field `{field}`: {message}")
    return "; ".join(lines)


@lru_cache(maxsize=8)
def _load(path: Path, mtime_ns: int) -> ModelCatalog:
    data = read_yaml_mapping(path, "model catalogue")
    try:
        return ModelCatalog.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(f"{path}: {describe_validation_error(exc)}") from None


def load_model_catalog(config_dir: Path) -> ModelCatalog:
    """Load `config_dir/models.yaml`, or built-in defaults when the file does not exist."""
    path = (config_dir / MODELS_FILE).resolve()
    if not path.is_file():
        return ModelCatalog()
    return _load(path, path.stat().st_mtime_ns)
