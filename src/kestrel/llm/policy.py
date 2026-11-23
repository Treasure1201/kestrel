"""Egress policy: where text may be sent, and in-process tracing switches.

Every destination that can receive text is tagged `on-device` or `external`. Local
Ollama counts as on-device only when its base URL host is a loopback address and the
model is not one Ollama passes through to its hosted service (names ending in
`ollama.hosted_model_suffixes` of `config/models.yaml`, by default `-cloud` and
`:cloud`); the hosted providers are always external; an MCP server carries its own
locality in `config/mcp_servers.yaml`. A `confidential` bot may use on-device
destinations only.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from kestrel.config import Settings
from kestrel.contracts import BotConfig, Locality, McpServerSpec
from kestrel.errors import ConfigurationError, EgressDeniedError
from kestrel.llm.catalog import load_model_catalog
from kestrel.llm.refs import ModelRef, parse_model_ref

LOCAL_OLLAMA_EXAMPLE = "KESTREL_OLLAMA_BASE_URL=http://127.0.0.1:11434"

_TRACING_SWITCHES = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


@dataclass(frozen=True, slots=True)
class Destination:
    """One place a request's text may go."""

    kind: str
    """`model` or `mcp_server`."""
    name: str
    """`<provider>:<model>` or the MCP server name."""
    locality: Locality


def url_host(url: str) -> str:
    """The host of an http(s) URL, lower-cased. Raises ConfigurationError for anything else."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        host = None
        parts = None
    if parts is None or parts.scheme not in {"http", "https"} or not host:
        raise ConfigurationError(f"{url!r} is not a valid http(s) URL. Use for example {LOCAL_OLLAMA_EXAMPLE}.")
    return host.lower()


def is_loopback_url(url: str) -> bool:
    """True when the URL's host is `localhost` or a loopback IP address (127.0.0.0/8, ::1)."""
    host = url_host(url)
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def model_locality(ref: str | ModelRef, settings: Settings) -> Locality:
    """Locality of a model destination."""
    parsed = parse_model_ref(ref)
    if parsed.provider != "ollama" or not is_loopback_url(settings.ollama_base_url):
        return "external"
    if load_model_catalog(settings.config_dir).ollama.is_hosted(parsed.model):
        return "external"
    return "on-device"


def destinations(model_ref: str | ModelRef, mcp_specs: Iterable[McpServerSpec], settings: Settings) -> tuple[Destination, ...]:
    """Every destination a request would use: the model, then each MCP server."""
    parsed = parse_model_ref(model_ref)
    found = [Destination("model", str(parsed), model_locality(parsed, settings))]
    found.extend(Destination("mcp_server", spec.name, spec.locality) for spec in mcp_specs)
    return tuple(found)


def check_egress(
    bot: BotConfig,
    model_ref: str | ModelRef,
    mcp_specs: Iterable[McpServerSpec],
    settings: Settings,
) -> tuple[Destination, ...]:
    """Raise EgressDeniedError unless the bot's policy allows every destination.

    Denied when the bot's `model.allowed_providers` excludes the model's provider, or
    when a `confidential` bot would send text to any external destination (model or
    MCP server). Returns the destinations checked, for audit and `doctor`.
    """
    parsed = parse_model_ref(model_ref)
    if parsed.provider not in bot.model.allowed_providers:
        allowed = ", ".join(bot.model.allowed_providers)
        raise EgressDeniedError(
            f"Bot {bot.id!r} does not allow the {parsed.provider!r} provider (model {parsed}). "
            f"Choose a model from an allowed provider ({allowed}) with --model or KESTREL_MODEL, "
            f"or add the provider to model.allowed_providers in bots/{bot.id}/bot.yaml."
        )
    checked = destinations(parsed, mcp_specs, settings)
    if bot.privacy.classification != "confidential":
        return checked
    for destination in checked:
        if destination.locality == "on-device":
            continue
        if destination.kind == "model":
            raise EgressDeniedError(
                f"Bot {bot.id!r} is confidential and may use on-device models only, but {destination.name} is external. "
                f"Use local Ollama on this machine: set KESTREL_MODEL=ollama:<model> and {LOCAL_OLLAMA_EXAMPLE}."
            )
        raise EgressDeniedError(
            f"Bot {bot.id!r} is confidential, but MCP server {destination.name!r} is external. "
            f"Remove it from mcp_servers in bots/{bot.id}/bot.yaml or use an on-device server."
        )
    return checked


def disable_third_party_tracing() -> None:
    """Switch off framework tracing to third-party services for this process.

    Sets the LangSmith/LangChain tracing environment switches to `false` and turns
    off LangSmith's global tracing flag, which takes precedence over the environment.
    Safe to call more than once.
    """
    for name in _TRACING_SWITCHES:
        os.environ[name] = "false"
    try:
        import langsmith
    except ImportError:  # pragma: no cover - langsmith ships with langchain-core
        return
    langsmith.configure(enabled=False)
