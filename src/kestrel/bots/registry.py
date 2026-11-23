"""The bot registry: every `bots/<id>/bot.yaml`, loaded on first use and reloaded when its files change.

Each access makes a cheap stat check of the bot's `bot.yaml`, prompt and manifest and
of every `config/*.yaml`; a changed modification time or size reloads the bot (and,
for a configuration file, the MCP server definitions), so a long-running front end
sees an edited bot without a restart.
"""

from __future__ import annotations

import re
from pathlib import Path
from threading import Lock

from kestrel.bots.expand import environment
from kestrel.bots.loader import BOT_FILE, load_bot
from kestrel.bots.mcp import load_mcp_servers
from kestrel.config import Settings
from kestrel.contracts import BOT_ID_PATTERN, BotConfig, McpServerSpec, Principal
from kestrel.errors import ConfigurationError, InvalidInputError


Stamp = tuple[tuple[str, int, int] | tuple[str], ...]
"""Path, modification time and size of each watched file (path alone when it is missing)."""


def _stamp(paths: list[Path]) -> Stamp:
    found: list[tuple[str, int, int] | tuple[str]] = []
    for path in paths:
        try:
            info = path.stat()
        except OSError:
            found.append((str(path),))
            continue
        found.append((str(path), info.st_mtime_ns, info.st_size))
    return tuple(found)


class BotRegistry:
    """Bots defined under `settings.bots_dir`, with the MCP servers from `settings.config_dir`.

    Loading is lazy: `get` loads one bot; `list` loads all and fails on the first
    invalid file, so a broken bot is never silently skipped.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bots_dir = Path(settings.bots_dir)
        self._lock = Lock()
        self._cache: dict[str, tuple[Stamp, BotConfig]] = {}
        self._mcp: dict[str, McpServerSpec] | None = None
        self._env: dict[str, str] | None = None
        self._config_stamp: Stamp | None = None

    def _servers(self) -> dict[str, McpServerSpec]:
        if self._mcp is None:
            self._mcp = load_mcp_servers(Path(self._settings.config_dir), self._settings)
        return self._mcp

    def _refresh_config(self) -> None:
        """Forget every loaded bot and the MCP servers when a `config/*.yaml` file changed. Call with the lock held."""
        config_dir = Path(self._settings.config_dir)
        files = sorted(config_dir.glob("*.yaml")) if config_dir.is_dir() else []
        stamp = _stamp(files)
        if stamp != self._config_stamp:
            self._config_stamp = stamp
            self._cache.clear()
            self._mcp = None

    def _bot_stamp(self, bot_id: str, bot: BotConfig | None) -> Stamp:
        files = [self._bots_dir / bot_id / BOT_FILE]
        if bot is not None:
            files.append(bot.prompt_path)
            if bot.knowledge.manifest is not None:
                files.append(bot.knowledge.manifest)
        return _stamp(files)

    def ids(self) -> list[str]:
        """Ids of every folder under the bots directory that holds a bot.yaml, sorted."""
        if not self._bots_dir.is_dir():
            raise ConfigurationError(f"The bots folder {self._bots_dir} does not exist. Set KESTREL_BOTS_DIR in .env.")
        return sorted(
            entry.name
            for entry in self._bots_dir.iterdir()
            if entry.is_dir() and (entry / BOT_FILE).is_file() and re.match(BOT_ID_PATTERN, entry.name)
        )

    def get(self, bot_id: str) -> BotConfig:
        """One bot by id.

        Raises:
            InvalidInputError: no such bot (the message lists the available ones).
            ConfigurationError: the bot's file is invalid.
        """
        with self._lock:
            self._refresh_config()
            cached = self._cache.get(bot_id) if isinstance(bot_id, str) else None
            if cached is not None and cached[0] == self._bot_stamp(bot_id, cached[1]):
                return cached[1]
            available = self.ids()
            if not isinstance(bot_id, str) or bot_id not in available:
                listed = ", ".join(available) or "none"
                raise InvalidInputError(f"Unknown bot {bot_id!r}. Available bots: {listed}.")
            if self._env is None:
                self._env = environment(self._settings)
            bot = load_bot(self._bots_dir / bot_id, self._env, self._servers().keys())
            self._cache[bot_id] = (self._bot_stamp(bot_id, bot), bot)
            return bot

    def list(self) -> list[BotConfig]:
        """Every bot, sorted by id."""
        return [self.get(bot_id) for bot_id in self.ids()]

    def visible_to(self, principal: Principal) -> list[BotConfig]:
        """The bots whose audience includes one of the principal's roles."""
        return [bot for bot in self.list() if principal.may_use(bot.audience)]

    def mcp_specs(self, bot: BotConfig) -> tuple[McpServerSpec, ...]:
        """The approved server definitions a bot opted into, in the bot's order."""
        with self._lock:
            self._refresh_config()
            servers = self._servers()
        missing = [name for name in bot.mcp_servers if name not in servers]
        if missing:
            raise ConfigurationError(f"bots/{bot.id}/{BOT_FILE}: field `mcp_servers`: {missing} are not defined in config/mcp_servers.yaml.")
        return tuple(servers[name] for name in bot.mcp_servers)
