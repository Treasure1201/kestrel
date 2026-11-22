"""A minimal stdio MCP server used by the MCP spike. Run as a child process, never imported.

It writes lifecycle lines to stderr (stdout is the protocol channel), so the test can
prove both that stderr is captured and that the server exited on its own.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

server = FastMCP("spike-echo")

GLOSSARY = {"waiting period": "Time after a claim event before benefits start."}


@server.tool()
def define(term: str) -> str:
    """Look up a term in a small read-only glossary."""
    return GLOSSARY.get(term.lower().strip(), "unknown term")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


if __name__ == "__main__":
    print("spike-echo: started", file=sys.stderr, flush=True)
    server.run(transport="stdio")
    print("spike-echo: stopped cleanly", file=sys.stderr, flush=True)
