"""Load one `bots/<id>/bot.yaml` into the `BotConfig` contract.

The loader expands `${ENV}` references, resolves `prompt`, `skills`,
`knowledge.manifest` and `evaluation.gold` relative to the bot folder (never
outside it), reads the prompt, and checks MCP server names against the approved
list. Every failure names the bot id and the exact field.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import ValidationError

from kestrel.bots.expand import expand
from kestrel.contracts import BUILTIN_TOOLS, BotConfig
from kestrel.errors import ConfigurationError, KestrelError
from kestrel.llm.catalog import describe_validation_error, read_yaml_mapping
from kestrel.llm.refs import parse_model_ref

BOT_FILE = "bot.yaml"
DEFAULT_MANIFEST = "manifest.yaml"
MAX_PROMPT_BYTES = 64 * 1024
_LOADER_OWNED = ("prompt_path", "prompt_text", "root")
_RENAMES = {"prompt_path": "prompt", "prompt_text": "prompt"}


class BotFileError(ConfigurationError):
    """A bot.yaml file is invalid. The message names the bot and the field."""


def _fail(bot_id: str, field: str, message: str) -> BotFileError:
    return BotFileError(f"bots/{bot_id}/{BOT_FILE}: field `{field}`: {message}")


def safe_relative_path(root: Path, value: object, bot_id: str, field: str) -> Path:
    """Resolve a relative path inside `root`. Rejects absolute paths, `..` and anything that escapes."""
    if not isinstance(value, str) or not value.strip():
        raise _fail(bot_id, field, "must be a relative path inside the bot folder.")
    text = value.strip()
    windows, posix = PureWindowsPath(text), PurePosixPath(text.replace("\\", "/"))
    if windows.is_absolute() or windows.drive or posix.is_absolute() or text.startswith(("/", "\\")):
        raise _fail(bot_id, field, f"{text!r} is absolute; use a path relative to bots/{bot_id}/.")
    if ".." in posix.parts:
        raise _fail(bot_id, field, f"{text!r} contains '..'; paths must stay inside bots/{bot_id}/.")
    resolved = (root / Path(*posix.parts)).resolve() if posix.parts else root.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise _fail(bot_id, field, f"{text!r} resolves outside bots/{bot_id}/.")
    return resolved


def _read_prompt(root: Path, value: object, bot_id: str) -> tuple[Path, str]:
    path = safe_relative_path(root, value, bot_id, "prompt")
    if not path.is_file():
        raise _fail(bot_id, "prompt", f"file {value!r} does not exist in bots/{bot_id}/. Create it or fix the name.")
    raw = path.read_bytes()
    if len(raw) > MAX_PROMPT_BYTES:
        raise _fail(bot_id, "prompt", f"{value!r} is larger than {MAX_PROMPT_BYTES // 1024} KB.")
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise _fail(bot_id, "prompt", f"{value!r} is not UTF-8 text.") from None
    if not text:
        raise _fail(bot_id, "prompt", f"{value!r} is empty.")
    return path, text


def _string_list(value: object, bot_id: str, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _fail(bot_id, field, "must be a list of names.")
    return value


def _translate(data: dict[str, Any], root: Path, bot_id: str, mcp_names: Collection[str]) -> dict[str, Any]:
    for key in _LOADER_OWNED:
        if key in data:
            raise _fail(bot_id, key, "is set by the loader and cannot appear in bot.yaml.")
    if "prompt" not in data:
        raise _fail(bot_id, "prompt", "is required: name the prompt file, for example `prompt: prompt.md`.")
    out = dict(data)
    out["prompt_path"], out["prompt_text"] = _read_prompt(root, out.pop("prompt"), bot_id)
    out["root"] = root.resolve()

    tools = _string_list(out.get("tools", list(BUILTIN_TOOLS)), bot_id, "tools")
    unknown_tools = sorted(set(tools) - set(BUILTIN_TOOLS))
    if unknown_tools:
        raise _fail(bot_id, "tools", f"unknown tools {unknown_tools}; available: {list(BUILTIN_TOOLS)}.")

    servers = _string_list(out.get("mcp_servers"), bot_id, "mcp_servers")
    unknown_servers = sorted(set(servers) - set(mcp_names))
    if unknown_servers:
        defined = sorted(mcp_names) or "none"
        raise _fail(bot_id, "mcp_servers", f"unknown MCP servers {unknown_servers}; defined in config/mcp_servers.yaml: {defined}.")

    skills = []
    for index, entry in enumerate(_string_list(out.get("skills"), bot_id, "skills")):
        folder = safe_relative_path(root, entry, bot_id, f"skills[{index}]")
        if not folder.is_dir():
            raise _fail(bot_id, f"skills[{index}]", f"folder {entry!r} does not exist in bots/{bot_id}/.")
        skills.append(folder)
    out["skills"] = skills

    knowledge = out.get("knowledge")
    if isinstance(knowledge, dict):
        knowledge = dict(knowledge)
        if knowledge.get("approval", "required") == "required":
            knowledge["manifest"] = safe_relative_path(root, knowledge.get("manifest", DEFAULT_MANIFEST), bot_id, "knowledge.manifest")
        elif "manifest" in knowledge:
            knowledge["manifest"] = safe_relative_path(root, knowledge["manifest"], bot_id, "knowledge.manifest")
        out["knowledge"] = knowledge

    evaluation = out.get("evaluation")
    if isinstance(evaluation, dict) and evaluation.get("gold") is not None:
        out["evaluation"] = {**evaluation, "gold": safe_relative_path(root, evaluation["gold"], bot_id, "evaluation.gold")}

    model = out.get("model")
    if isinstance(model, dict) and isinstance(model.get("default"), str):
        try:
            parse_model_ref(model["default"])
        except KestrelError as exc:
            raise _fail(bot_id, "model.default", exc.user_message) from None
    return out


def load_bot(folder: Path, env: Mapping[str, str], mcp_names: Collection[str]) -> BotConfig:
    """Load and validate `folder/bot.yaml`.

    Raises:
        BotFileError: the file is missing or invalid; the message names the bot and field.
    """
    bot_id = folder.name
    path = folder / BOT_FILE
    if not path.is_file():
        raise BotFileError(f"bots/{bot_id}/{BOT_FILE} does not exist.")
    data = read_yaml_mapping(path, f"bots/{bot_id}/{BOT_FILE}")
    where = f"bots/{bot_id}/{BOT_FILE}"
    data = expand(data, env, where)
    if data.get("id") != bot_id:
        raise _fail(bot_id, "id", f"must equal the folder name {bot_id!r} (found {data.get('id')!r}).")
    translated = _translate(data, folder, bot_id, mcp_names)
    try:
        return BotConfig.model_validate(translated)
    except ValidationError as exc:
        raise BotFileError(f"{where}: {describe_validation_error(exc, _RENAMES)}") from None
