"""Admission control for the MCP server: bounded concurrency, a small queue, a rate limit.

Limits come from environment variables with safe defaults (an unset, malformed or
out-of-range value falls back to the default and is logged to stderr):

| Variable | Default | Meaning |
|---|---|---|
| `KESTREL_MCP_CONCURRENCY` | 2 | service calls running at once |
| `KESTREL_MCP_QUEUE_SIZE` | 4 | calls allowed to wait for a free slot |
| `KESTREL_MCP_QUEUE_TIMEOUT_S` | 10 | longest wait for a slot, in seconds |
| `KESTREL_MCP_RATE_PER_MINUTE` | 30 | calls admitted in any rolling minute |

Service methods are synchronous, so each admitted call runs in a worker thread and the
event loop is never blocked. A call the client abandons keeps its slot until the
worker thread finishes, so the limit always reflects real work in progress.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal, TypeVar

log = logging.getLogger("kestrel.mcp")

T = TypeVar("T")

RejectReason = Literal["busy", "rate_limited"]


@dataclass(frozen=True)
class McpLimits:
    """Admission limits for one server process."""

    concurrency: int = 2
    queue_size: int = 4
    queue_timeout_s: float = 10.0
    rate_per_minute: int = 30

    def __post_init__(self) -> None:
        if self.concurrency < 1 or self.queue_size < 0 or self.rate_per_minute < 1:
            raise ValueError("MCP limits must be positive (queue size may be zero)")
        if not math.isfinite(self.queue_timeout_s) or self.queue_timeout_s <= 0:
            raise ValueError("MCP queue timeout must be a positive number of seconds")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> McpLimits:
        """Read `KESTREL_MCP_*` variables, falling back to defaults for bad values."""
        env = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            concurrency=_read(env, "KESTREL_MCP_CONCURRENCY", int, defaults.concurrency, 1, 64),
            queue_size=_read(env, "KESTREL_MCP_QUEUE_SIZE", int, defaults.queue_size, 0, 256),
            queue_timeout_s=_read(env, "KESTREL_MCP_QUEUE_TIMEOUT_S", float, defaults.queue_timeout_s, 0.01, 600.0),
            rate_per_minute=_read(env, "KESTREL_MCP_RATE_PER_MINUTE", int, defaults.rate_per_minute, 1, 10_000),
        )


def _read(env: Any, name: str, kind: type, default: Any, low: float, high: float) -> Any:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = kind(str(raw).strip())
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or not low <= value <= high:
        log.warning("Ignoring %s=%r; using the default %s.", name, raw, default)
        return default
    return value


class AdmissionRejected(Exception):
    """A call was refused before it reached the service."""

    def __init__(self, reason: RejectReason) -> None:
        super().__init__(reason)
        self.reason: RejectReason = reason


class Admission:
    """Bounds running, queued and per-minute work for one server process."""

    def __init__(self, limits: McpLimits) -> None:
        self.limits = limits
        self._slots = asyncio.Semaphore(limits.concurrency)
        self._starts: deque[float] = deque()
        self._queued = 0
        self._in_flight = 0
        self.rejected: dict[str, int] = {"busy": 0, "rate_limited": 0, "invalid_input": 0}

    def note_invalid(self) -> None:
        """Count a request rejected by input validation."""
        self.rejected["invalid_input"] += 1

    def metrics(self) -> dict[str, Any]:
        """Current load and rejection counters."""
        return {
            "in_flight": self._in_flight,
            "queued": self._queued,
            "rejected": sum(self.rejected.values()),
            "rejected_by_reason": dict(self.rejected),
        }

    def _reject(self, reason: RejectReason) -> AdmissionRejected:
        self.rejected[reason] += 1
        return AdmissionRejected(reason)

    async def run(self, func: Callable[..., T], *args: Any) -> T:
        """Run `func(*args)` in a worker thread once admitted.

        Raises:
            AdmissionRejected: rate limit reached, queue full, or no slot within the wait timeout.
        """
        now = monotonic()
        while self._starts and now - self._starts[0] >= 60.0:
            self._starts.popleft()
        if len(self._starts) >= self.limits.rate_per_minute:
            raise self._reject("rate_limited")
        if self._slots.locked() and self._queued >= self.limits.queue_size:
            raise self._reject("busy")
        self._starts.append(now)
        self._queued += 1
        try:
            async with asyncio.timeout(self.limits.queue_timeout_s):
                await self._slots.acquire()
        except TimeoutError:
            raise self._reject("busy") from None
        finally:
            self._queued -= 1
        self._in_flight += 1
        task = asyncio.ensure_future(asyncio.to_thread(func, *args))

        def finished(done: asyncio.Future[T]) -> None:
            self._in_flight -= 1
            self._slots.release()
            if not done.cancelled():
                done.exception()  # mark retrieved; the awaiting caller handles it

        task.add_done_callback(finished)
        return await asyncio.shield(task)
