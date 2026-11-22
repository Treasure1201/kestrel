"""Bots as configuration: bot.yaml loading, validation and the registry."""

from kestrel.bots.expand import environment, expand
from kestrel.bots.loader import BotFileError, load_bot, safe_relative_path
from kestrel.bots.mcp import load_mcp_servers
from kestrel.bots.registry import BotRegistry

__all__ = [
    "BotFileError",
    "BotRegistry",
    "environment",
    "expand",
    "load_bot",
    "load_mcp_servers",
    "safe_relative_path",
]
