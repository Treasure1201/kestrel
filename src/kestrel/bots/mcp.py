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


def load_mcp_servers(config_dir: Path, settings: Settings | None = None) -> dict[str, McpServerSpec]:
    """Approved MCP servers by name. A missing file means no servers.

    `${ENV}` references are expanded when `settings` is given. A stdio `command` of
    `python` is replaced with the running interpreter.

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
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.get("transport") == "stdio" and entry.get("command") == "python":
            entry = {**entry, "command": sys.executable}
        try:
            spec = McpServerSpec.model_validate(entry)
        except ValidationError as exc:
            raise ConfigurationError(f"{path}: servers[{index}]: {describe_validation_error(exc)}") from None
        if spec.name in specs:
            raise ConfigurationError(f"{path}: servers[{index}]: server name {spec.name!r} is defined twice.")
        specs[spec.name] = spec
    return specs
