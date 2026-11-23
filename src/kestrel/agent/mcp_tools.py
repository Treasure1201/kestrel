"""Tools from a bot's approved MCP servers, through langchain-mcp-adapters.

Only each server's `allowed_tools` are exposed. Every argument string passes the
bot's privacy screen before it leaves the process, and every result is wrapped with
the untrusted-data notice. A server that cannot be started or listed becomes a
warning, not a crash: the question is answered without its tools.

Stdio servers are launched with an absolute executable path and an argument list,
never through a shell. Each tool call opens its own short session (the adapters'
default), so no server process outlives a call.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from kestrel.agent.tools import TOOL_NOTICE
from kestrel.contracts import McpServerSpec, PrivacyPolicy, PrivacyScreen
from kestrel.errors import KestrelError

LIST_TIMEOUT_S = 30.0
CALL_TIMEOUT_S = 60.0
MAX_RESULT_CHARS = 8000
MAX_STEP_ARGUMENT_CHARS = 200


@dataclass
class McpTools:
    """The tools loaded for one request, and a warning per server that could not be used."""

    tools: list[BaseTool] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def connection_for(spec: McpServerSpec) -> dict[str, Any]:
    """The adapters' connection dict for a spec. Raises ValueError when a stdio command cannot be found."""
    if spec.transport == "http":
        return {"transport": "streamable_http", "url": spec.url}
    command = Path(spec.command or "")
    if not command.is_absolute():
        found = shutil.which(str(command))
        if found is None:
            raise ValueError(f"command {spec.command!r} was not found")
        command = Path(found)
    return {"transport": "stdio", "command": str(command.resolve()), "args": list(spec.args)}


def _text(result: Any) -> str:
    if isinstance(result, tuple) and len(result) == 2:
        result = result[0]
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(str(block.get("text", block)) if isinstance(block, dict) else str(block) for block in result)
    return str(result)


ArgPath = tuple[str | int, ...]


def _screen_value(
    value: Any, screen: PrivacyScreen, policy: PrivacyPolicy, found: list[str], numbers: list[ArgPath] | None = None, path: ArgPath = (),
) -> Any:
    """Screen every string, the string form of every number, and every dictionary key.

    A number that matches is replaced by the placeholder string; its path is added
    to `numbers` so the caller can refuse a tool whose schema needs a number there.
    """
    if isinstance(value, str):
        screened = screen.screen(value, policy)
        found.extend(screened.matched)
        return screened.text
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        screened = screen.screen(str(value), policy)
        if not screened.matched:
            return value
        found.extend(screened.matched)
        if numbers is not None:
            numbers.append(path)
        return screened.text
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            name = key
            if isinstance(key, (str, int, float)) and not isinstance(key, bool):
                screened_key = screen.screen(str(key), policy)
                if screened_key.matched:
                    found.extend(screened_key.matched)
                    name = screened_key.text
            result[name] = _screen_value(item, screen, policy, found, numbers, (*path, str(name)))
        return result
    if isinstance(value, (list, tuple)):
        return [_screen_value(item, screen, policy, found, numbers, (*path, index)) for index, item in enumerate(value)]
    return value


def _schema_type_at(schema: Any, path: ArgPath) -> set[str]:
    """The JSON-schema types allowed at `path`, or an empty set when the schema does not say."""
    node = schema
    for step in path:
        if not isinstance(node, dict):
            return set()
        node = node.get("items") if isinstance(step, int) else (node.get("properties") or {}).get(step)
    if not isinstance(node, dict):
        return set()
    kind = node.get("type")
    kinds = {kind} if isinstance(kind, str) else set(kind) if isinstance(kind, list) else set()
    for option in node.get("anyOf") or node.get("oneOf") or ():
        if isinstance(option, dict) and isinstance(option.get("type"), str):
            kinds.add(option["type"])
    return kinds


def _needs_number(tool: BaseTool, path: ArgPath) -> bool:
    schema = tool.args_schema
    if schema is not None and not isinstance(schema, dict):
        try:
            schema = schema.model_json_schema()  # type: ignore[union-attr]
        except Exception:  # an unreadable schema: assume the number is needed
            return True
    kinds = _schema_type_at(schema, path)
    return not kinds or "string" not in kinds


OnCall = Callable[[str, str], None]
"""Told `(label, detail)` after each MCP tool call; detail holds only screened arguments."""

Register = Callable[[str, str, str, str], str]
"""Given `(server, tool, screened arguments as JSON, result text)`, returns the result's label (`t1`)."""


def _arguments_text(arguments: Any) -> str:
    text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= MAX_STEP_ARGUMENT_CHARS else text[: MAX_STEP_ARGUMENT_CHARS - 3] + "..."


def guard_tool(
    tool: BaseTool,
    server: str,
    screen: PrivacyScreen,
    policy: PrivacyPolicy,
    on_call: OnCall | None = None,
    register: Register | None = None,
) -> BaseTool:
    """Wrap an MCP tool: screen its arguments, bound its run time and mark its output as untrusted data.

    With `register`, each successful result is labelled (`t1`, `t2`, ...) as citable
    evidence and shown to the model with that label; errors are never labelled.
    """

    def note(label: str, detail: str) -> None:
        if on_call is not None:
            on_call(label, f"{tool.name} on {server}: {detail}")

    async def call(**kwargs: Any) -> str:
        found: list[str] = []
        numbers: list[ArgPath] = []
        try:
            arguments = _screen_value(kwargs, screen, policy, found, numbers)
        except KestrelError:
            note("Tool call refused", "the arguments could not be screened")
            return "Error: the arguments could not be screened for personal information, so the tool was not called."
        if found and policy.on_personal_information == "block":
            note("Tool call refused", "the arguments held personal information")
            return "Error: the arguments contain personal information, so the tool was not called. Do not send personal details."
        if any(_needs_number(tool, path) for path in numbers):
            note("Tool call refused", "a number in the arguments held personal information")
            return (
                "Error: a number in the arguments looks like personal information and was redacted, but the tool needs a "
                "number there, so it was not called. Do not send personal details."
            )
        try:
            result = await asyncio.wait_for(tool.ainvoke(arguments), timeout=CALL_TIMEOUT_S)
        except TimeoutError:
            note("Tool call failed", "no answer in time")
            return f"Error: tool {tool.name} on server {server} did not answer in time."
        except Exception as exc:  # a failing server must not end the question
            note("Tool call failed", type(exc).__name__)
            return f"Error: tool {tool.name} on server {server} failed ({type(exc).__name__})."
        shown = _arguments_text(arguments)
        text = _text(result)[:MAX_RESULT_CHARS]
        if register is None:
            note("Called a tool", shown)
            return f"{TOOL_NOTICE}\n\nResult of {tool.name} (server {server}):\n{text}"
        label = register(server, tool.name, shown, text)
        note("Called a tool", f"{shown} -> {label}")
        return (
            f"{TOOL_NOTICE}\n\n[{label}] Result of tool {tool.name} (server {server}, arguments {shown}):\n{text}\n\n"
            f"This is a tool result, not a document. Cite it as {label}, quoting the result exactly, for the facts it gives."
        )

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        coroutine=call,
    )


async def load_mcp_tools(
    specs: Sequence[McpServerSpec],
    screen: PrivacyScreen,
    policy: PrivacyPolicy,
    *,
    reserved: Sequence[str] = (),
    on_call: OnCall | None = None,
    register: Register | None = None,
) -> McpTools:
    """Load the allow-listed tools of each server. Never raises for a server problem."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    loaded = McpTools()
    taken = set(reserved)
    for spec in specs:
        try:
            client = MultiServerMCPClient({spec.name: connection_for(spec)})  # type: ignore[dict-item]
            tools = await asyncio.wait_for(client.get_tools(server_name=spec.name), timeout=LIST_TIMEOUT_S)
        except Exception as exc:  # start-up failures come in many types: OS, protocol, timeout
            loaded.warnings.append(f"The tool server {spec.name!r} could not be started ({type(exc).__name__}); its tools were not used.")
            continue
        allowed = set(spec.allowed_tools)
        for tool in tools:
            if tool.name not in allowed:
                continue
            if tool.name in taken:
                loaded.warnings.append(f"Tool {tool.name!r} from server {spec.name!r} clashes with another tool name and was not used.")
                continue
            taken.add(tool.name)
            loaded.tools.append(guard_tool(tool, spec.name, screen, policy, on_call, register))
        missing = sorted(allowed - {tool.name for tool in tools})
        if missing:
            loaded.warnings.append(f"The tool server {spec.name!r} does not offer the approved tool(s) {missing}.")
    return loaded
