"""`${ENV_VAR}` and `${ENV_VAR:-default}` expansion for configuration files."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

from kestrel.config import Settings
from kestrel.errors import ConfigurationError

_REFERENCE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")
_SECRET_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


def environment(settings: Settings) -> dict[str, str]:
    """Variables available to `${...}`: the process environment, then `KESTREL_*` values from settings.

    Settings win for `KESTREL_*` names because they already combine the environment,
    `.env` and defaults. Secret settings are never offered.
    """
    values = dict(os.environ)
    for name, value in settings.model_dump().items():
        if isinstance(value, SecretStr) or value is None or name.endswith("_api_key"):
            continue
        if isinstance(value, (tuple, list)):
            value = ",".join(str(item) for item in value)
        values[f"KESTREL_{name.upper()}"] = str(value)
    return values


def expand(value: Any, env: Mapping[str, str], where: str, field: str = "") -> Any:
    """Expand references in every string inside `value` (dicts, lists and scalars).

    Raises:
        ConfigurationError: a variable is undefined and has no default, or names a secret.
    """
    if isinstance(value, dict):
        return {key: expand(item, env, where, f"{field}.{key}" if field else str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, env, where, f"{field}[{index}]") for index, item in enumerate(value)]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if any(word in name.upper() for word in _SECRET_WORDS):
            raise ConfigurationError(f"{where}: field `{field}` refers to ${{{name}}}; secrets cannot be used in bot files.")
        if name in env and env[name] != "":
            return env[name]
        default = match.group("default")
        if default is not None:
            return default
        raise ConfigurationError(f"{where}: field `{field}` uses ${{{name}}}, which is not set. Set {name} in .env or give a default as ${{{name}:-value}}.")

    return _REFERENCE.sub(replace, value)
