"""The MCP servers as real child processes: absolute interpreter, argument list, stderr captured."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[2]
CALENDAR = REPO_ROOT / "examples" / "mcp_servers" / "workday_calendar.py"


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KESTREL_")}
    env.update({"PYTHONUNBUFFERED": "1", **extra})
    return env


def _run(params: StdioServerParameters, stderr_path: Path, body: Callable[[ClientSession], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        with stderr_path.open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                return await body(session)

    return asyncio.run(asyncio.wait_for(main(), timeout=90))


def test_kestrel_mcp_server_round_trip_in_a_child_process(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=str(Path(sys.executable).resolve()),
        args=["-m", "kestrel.interfaces.mcp_server"],
        cwd=str(tmp_path),
        env=_clean_env(KESTREL_MCP_FAKE="1", KESTREL_DEFAULT_BOT="example-bot", KESTREL_USER="process-test"),
    )
    stderr_path = tmp_path / "server.stderr"

    async def body(session: ClientSession) -> Any:
        tools = sorted(t.name for t in (await session.list_tools()).tools)
        answer = await session.call_tool("ask", {"question": "When is the help desk open?"})
        status = json.loads((await session.read_resource("kestrel://status")).contents[0].text)
        return tools, answer, status

    tools, answer, status = _run(params, stderr_path, body)

    assert tools == ["ask", "get_section", "list_bots", "search"]
    assert not answer.isError and answer.structuredContent["outcome"] == "answered"
    assert answer.structuredContent["citations"][0]["filename"] == "example-notes.txt"
    assert status["doctor"]["ok"] is True and status["in_flight"] == 0
    log = stderr_path.read_text(encoding="utf-8")
    assert "kestrel-mcp: started" in log and "kestrel-mcp: stopped" in log
    assert "Traceback" not in log


def test_server_writes_only_protocol_messages_to_stdout(tmp_path: Path) -> None:
    """Drive the server with raw JSON-RPC lines and check every stdout line is a protocol message."""
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ask", "arguments": {"question": "hours?"}}},
    ]

    async def main() -> tuple[bytes, bytes, int]:
        process = await anyio.open_process(
            [str(Path(sys.executable).resolve()), "-m", "kestrel.interfaces.mcp_server"],
            cwd=str(tmp_path),
            env=_clean_env(KESTREL_MCP_FAKE="1", KESTREL_DEFAULT_BOT="example-bot"),
        )
        async with process:
            assert process.stdin and process.stdout and process.stderr
            out = b""
            for request in requests:
                await process.stdin.send((json.dumps(request) + "\n").encode())
            with anyio.fail_after(60):
                while out.count(b"\n") < 2:
                    out += await process.stdout.receive()
            await process.stdin.aclose()
            with anyio.fail_after(30):
                async for chunk in process.stdout:
                    out += chunk
                err = b"".join([chunk async for chunk in process.stderr])
                code = await process.wait()
        return out, err, code

    out, err, code = asyncio.run(main())
    lines = [line for line in out.decode("utf-8").splitlines() if line.strip()]
    messages = [json.loads(line) for line in lines]
    assert all(m.get("jsonrpc") == "2.0" for m in messages)
    assert [m.get("id") for m in messages] == [1, 2]
    assert messages[1]["result"]["structuredContent"]["outcome"] == "answered"
    assert code == 0, err.decode("utf-8", "replace")


def test_workday_calendar_round_trip(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=str(Path(sys.executable).resolve()), args=[str(CALENDAR)], cwd=str(tmp_path), env=_clean_env()
    )
    stderr_path = tmp_path / "calendar.stderr"

    async def body(session: ClientSession) -> Any:
        tools = sorted(t.name for t in (await session.list_tools()).tools)
        today = await session.call_tool("today", {})
        week = await session.call_tool("working_days_between", {"start": "2026-11-23", "end": "2026-11-29"})
        span = await session.call_tool("working_days_between", {"start": "2026-11-27", "end": "2026-12-07"})
        bad = await session.call_tool("working_days_between", {"start": "2026-12-07", "end": "2026-11-27"})
        return tools, today, week, span, bad

    tools, today, week, span, bad = _run(params, stderr_path, body)

    from datetime import date

    assert tools == ["today", "working_days_between"]
    assert date.fromisoformat(today.content[0].text)
    assert week.structuredContent == {"result": 5}  # Monday to Sunday
    assert span.structuredContent == {"result": 7}  # Friday to the Monday a week later
    assert bad.isError
    log = stderr_path.read_text(encoding="utf-8")
    assert "workday-calendar: started" in log and "workday-calendar: stopped" in log
