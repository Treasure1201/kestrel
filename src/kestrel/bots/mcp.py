"""Load `config/mcp_servers.yaml` into `McpServerSpec` objects."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from kestrel.config import Settings
from kestrel.contracts import McpServerSpec
from kestrel.errors import ConfigurationError
from kestrel.llm.catalog import describe_validation_error, read_yaml_mapping

MCP_SERVERS_FILE = "mcp_servers.yaml"


def _resolve_arg(arg: object, root: Path) -> object:
    """A relative path argument naming an existing file under the repository root, made absolute.

    Anything else (flags, values, paths that do not exist there, paths escaping the root) is unchanged.
    """
    if not isinstance(arg, str) or not arg.strip() or arg.startswith("-"):
        return arg
    candidate = Path(arg)
    if candidate.is_absolute():
        return arg
    try:
        resolved = (root / candidate).resolve()
    except (OSError, ValueError):
        return arg
    if resolved.is_file() and resolved.is_relative_to(root):
        return str(resolved)
    return arg


def load_mcp_servers(config_dir: Path, settings: Settings | None = None) -> dict[str, McpServerSpec]:
    """Approved MCP servers by name. A missing file means no servers.

    `${ENV}` references are expanded when `settings` is given. A stdio `command` of
    `python` is replaced with the running interpreter. A relative path in a stdio
    server's `args` that names an existing file under the repository root (the parent
    of the config folder) becomes absolute, so the server starts from any working
    directory.

    Raises:
        ConfigurationError: invalid YAML, a duplicate name, or an invalid entry (with its field).
    """
    path = config_dir / MCP_SERVERS_FILE
    if not path.is_file():
        return {}
    data = read_yaml_mapping(path, "MCP server list")
    unknown = sorted(set(data) - {"servers"})
    if unknown:
        raise ConfigurationError(f"{path}: unknown top-level keys {unknown}; only `servers` is allowed.")
    entries = data.get("servers") or []
    if not isinstance(entries, list):
        raise ConfigurationError(f"{path}: field `servers` must be a list.")
    if settings is not None:
        from kestrel.bots.expand import environment, expand

        entries = expand(entries, environment(settings), str(path), "servers")
    specs: dict[str, McpServerSpec] = {}
    root = config_dir.resolve().parent
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.get("transport") == "stdio":
            if entry.get("command") == "python":
                entry = {**entry, "command": sys.executable}
            if isinstance(entry.get("args"), list):
                entry = {**entry, "args": [_resolve_arg(arg, root) for arg in entry["args"]]}
        try:
            spec = McpServerSpec.model_validate(entry)
        except ValidationError as exc:
            raise ConfigurationError(f"{path}: servers[{index}]: {describe_validation_error(exc)}") from None
        if spec.name in specs:
            raise ConfigurationError(f"{path}: servers[{index}]: server name {spec.name!r} is defined twice.")
        specs[spec.name] = spec
    return specs
