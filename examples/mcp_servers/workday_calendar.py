"""A tiny read-only stdio MCP server: today's date and a working-day count.

Self-contained and side-effect free: it reads the system clock and computes from its
arguments; it stores, writes and sends nothing. Run as a child process by an MCP client:

    python examples/mcp_servers/workday_calendar.py

stdout carries only protocol messages; lifecycle lines go to stderr.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP

MAX_SPAN_DAYS = 366 * 200
"""Longest range accepted, to keep answers sensible."""

server = FastMCP("workday-calendar", log_level="WARNING")


@server.tool()
def today() -> str:
    """Return today's date on this machine as an ISO date, for example 2026-11-23."""
    return date.today().isoformat()


@server.tool()
def working_days_between(start: str, end: str) -> int:
    """Count working days (Monday to Friday) from `start` to `end`, both ISO dates, both included.

    Public holidays are NOT taken into account: every Monday to Friday counts. `end` must
    not be before `start`.
    """
    try:
        first, last = date.fromisoformat(start.strip()), date.fromisoformat(end.strip())
    except ValueError:
        raise ValueError("start and end must be ISO dates such as 2026-11-23") from None
    if last < first:
        raise ValueError("end must not be before start")
    span = (last - first).days + 1
    if span > MAX_SPAN_DAYS:
        raise ValueError("the range is too long")
    weeks, rest = divmod(span, 7)
    count = weeks * 5
    for offset in range(rest):
        if (first + timedelta(days=weeks * 7 + offset)).weekday() < 5:
            count += 1
    return count


if __name__ == "__main__":
    print("workday-calendar: started", file=sys.stderr, flush=True)
    server.run(transport="stdio")
    print("workday-calendar: stopped", file=sys.stderr, flush=True)
