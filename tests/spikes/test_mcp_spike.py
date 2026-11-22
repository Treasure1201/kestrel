"""Spike: a stdio MCP server runs as a real child process on this machine.

The server is started with the absolute interpreter path and an argument list (no
shell), its stderr goes to a file the test reads, it answers a tool call, and it exits
by itself when the client closes stdin. langchain-mcp-adapters then turns the same
session's tools into LangChain tools.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.spike

SERVER = Path(__file__).with_name("mcp_echo_server.py")


def _params() -> StdioServerParameters:
    interpreter = Path(sys.executable).resolve()
    assert interpreter.is_absolute()
    return StdioServerParameters(command=str(interpreter), args=[str(SERVER)], cwd=str(SERVER.parent))


def _run(coro_factory, stderr_path: Path):  # type: ignore[no-untyped-def]
    async def main():  # type: ignore[no-untyped-def]
        with stderr_path.open("w", encoding="utf-8") as errlog:
            async with stdio_client(_params(), errlog=errlog) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                return await coro_factory(session)

    return asyncio.run(asyncio.wait_for(main(), timeout=60))


def test_stdio_server_answers_a_call_and_shuts_down_cleanly(tmp_path: Path) -> None:
    stderr_path = tmp_path / "server.stderr"

    async def exercise(session: ClientSession):  # type: ignore[no-untyped-def]
        tools = await session.list_tools()
        reply = await session.call_tool("define", {"term": "Waiting period"})
        return [tool.name for tool in tools.tools], reply

    names, reply = _run(exercise, stderr_path)

    assert sorted(names) == ["add", "define"]
    assert not reply.isError
    assert reply.content[0].text == "Time after a claim event before benefits start."
    log = stderr_path.read_text(encoding="utf-8")
    assert "spike-echo: started" in log
    assert "spike-echo: stopped cleanly" in log


def test_adapters_load_session_tools_as_langchain_tools(tmp_path: Path) -> None:
    async def exercise(session: ClientSession):  # type: ignore[no-untyped-def]
        tools = {tool.name: tool for tool in await load_mcp_tools(session, server_name="spike-echo")}
        result = await tools["add"].ainvoke({"a": 2, "b": 3})
        return sorted(tools), tools["define"].description, result

    names, description, result = _run(exercise, tmp_path / "server.stderr")

    assert names == ["add", "define"]
    assert "glossary" in description
    text = result if isinstance(result, str) else " ".join(str(block.get("text", block)) for block in result)
    assert "5" in text
