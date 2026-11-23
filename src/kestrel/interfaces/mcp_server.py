"""The MCP server: governed answers for other agents, over stdio.

Tools (all read the approved document library; none changes it):

- `list_bots()`: the bots the caller may use.
- `ask(question, bot_id=None)`: the governed answer, with citations, calculations,
  caveats, the evidence-check verdict, redactions, model, latency and trace id.
- `search(query, bot_id=None, limit=5)`: ranked passages, no model call.
- `get_section(section_id, bot_id=None)`: one whole section.

Resource `kestrel://status`: the doctor summary (check names and states, no details)
plus in-flight, queued and rejected counts.

Operator actions (ingest, block, unblock, prune) are deliberately not exposed: they
change what every user sees and stay on the command line, behind the operator role.

Controls: input lengths are checked against the contract before any work; calls pass
admission control (`mcp_admission`: bounded concurrency, a small queue with a wait
timeout, a per-minute rate limit); synchronous service calls run in worker threads;
every failure returns the tool's typed result with `error` set (a category and a
user-safe message) and the MCP error flag raised, never a traceback or a path.

Identity: the principal is built from settings (KESTREL_USER, KESTREL_ROLES) on the
`mcp` channel. In production the server runs on streamable HTTP and the principal is
derived from the caller's OAuth token instead.

Protocol hygiene: stdout carries only protocol messages. `main()` points `sys.stdout`
at stderr for everything else, and logs go to stderr.

`KESTREL_MCP_FAKE=1` (test only) serves a tiny built-in fake service instead of the
real one, so tests can run the server as a child process with no data or model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable
from io import TextIOWrapper
from typing import Annotated, Any, BinaryIO

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from kestrel.config import Settings
from kestrel.contracts import BOT_ID_PATTERN, MAX_QUESTION_CHARS, AssistantService
from kestrel.errors import ConfigurationError, KestrelError
from kestrel.interfaces.mcp_admission import Admission, AdmissionRejected, McpLimits
from kestrel.interfaces.mcp_models import (
    AskOutput,
    BotListOutput,
    SafeError,
    SearchOutput,
    SectionOutput,
    ask_output,
    bots_output,
    error_from_exception,
    generic_error,
    search_output,
    section_output,
)
from kestrel.redaction import describe_exception, redact_secrets

log = logging.getLogger("kestrel.mcp")

SERVER_NAME = "kestrel"
MAX_QUERY_CHARS = MAX_QUESTION_CHARS
MAX_SECTION_ID_CHARS = 200
MAX_SEARCH_LIMIT = 20
_BOT_ID = re.compile(BOT_ID_PATTERN)

INSTRUCTIONS = (
    "Governed question answering over an operator-approved document library. "
    "Answers come only from approved documents and carry citations; pass answers, "
    "citations and conditions on to the user unchanged."
)

ASK_DESCRIPTION = (
    "Ask a question of one bot and get a governed answer. The answer comes only from the "
    "bot's approved documents, never from general knowledge. Pass the answer, its citations "
    "(filename, locator, quote, edition, status), its conditions and its caveats on to the "
    "user unchanged; do not paraphrase away conditions or add facts. outcome 'not_found' or "
    "'refused' means the approved documents do not cover the question: say so and do not "
    "answer it from elsewhere. 'withheld' means the answer failed its evidence checks. "
    f"Questions over {MAX_QUESTION_CHARS} characters are rejected. Each call is audited."
)

ServiceSource = AssistantService | Callable[[], AssistantService]


class _InvalidRequest(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _reply(model: BaseModel) -> CallToolResult:
    data = model.model_dump(mode="json")
    is_error = getattr(model, "error", None) is not None
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
        structuredContent=data,
        isError=is_error,
    )


def _check_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _InvalidRequest(f"The {field} is empty.")
    if len(value) > limit:
        raise _InvalidRequest(f"The {field} is longer than {limit} characters.")
    return value


class _ServiceHolder:
    """Resolves the service once, in a worker thread, so a slow build never blocks the loop."""

    def __init__(self, source: ServiceSource) -> None:
        self._source = source
        self._service: AssistantService | None = source if _is_service(source) else None  # type: ignore[assignment]
        self._lock = threading.Lock()

    def get(self) -> AssistantService:
        if self._service is not None:
            return self._service
        with self._lock:
            if self._service is None:
                self._service = self._source()  # type: ignore[operator]
            return self._service


def _is_service(obj: object) -> bool:
    return all(callable(getattr(obj, name, None)) for name in ("ask", "search", "get_section", "list_bots", "doctor"))


def create_server(
    service: ServiceSource,
    settings: Settings | None = None,
    limits: McpLimits | None = None,
) -> FastMCP:
    """Build the MCP server over `service` (an `AssistantService`, or a zero-argument factory).

    A factory is called on first use, in a worker thread; if it fails, each call returns a
    `configuration` error and the next call tries again.
    """
    settings = settings if settings is not None else Settings()
    principal = settings.principal("mcp")
    holder = _ServiceHolder(service)
    admission = Admission(limits if limits is not None else McpLimits.from_env())
    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS, log_level="WARNING")
    server.admission = admission  # type: ignore[attr-defined] - exposed for tests and the status resource

    def bot_or_default(bot_id: str | None) -> str:
        if bot_id is None or bot_id == "":
            return settings.default_bot
        if not isinstance(bot_id, str) or not _BOT_ID.match(bot_id):
            raise _InvalidRequest("The bot id is not valid. Use list_bots to see the bots you may use.")
        return bot_id

    async def call(method: str, *args: Any) -> Any:
        """Run one service method through admission control; failures come back as `SafeError`."""

        def work() -> Any:
            return getattr(holder.get(), method)(*args)

        try:
            return await admission.run(work)
        except AdmissionRejected as exc:
            return generic_error(exc.reason)
        except KestrelError as exc:
            log.info("%s failed: %s (%s)", method, exc.category.value, redact_secrets(exc.detail or exc.user_message))
            return error_from_exception(exc)
        except Exception as exc:  # noqa: BLE001 - fail closed with a safe message
            log.error("%s failed unexpectedly: %s", method, describe_exception(exc, "internal"))
            return generic_error("internal")

    def invalid(exc: _InvalidRequest) -> SafeError:
        admission.note_invalid()
        return SafeError(category="invalid_input", message=exc.message)

    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(name="list_bots", title="List bots", annotations=read_only)
    async def list_bots() -> Annotated[CallToolResult, BotListOutput]:
        """List the bots you may use: id, name and description. Pass an id as `bot_id` to the other tools."""
        result = await call("list_bots", principal)
        return _reply(BotListOutput(error=result) if isinstance(result, SafeError) else bots_output(result))

    @server.tool(
        name="ask",
        title="Ask approved documents",
        description=ASK_DESCRIPTION,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def ask(
        question: Annotated[str, Field(description=f"The question, at most {MAX_QUESTION_CHARS} characters.")],
        bot_id: Annotated[str | None, Field(description="Bot id from list_bots; omit for the default bot.")] = None,
    ) -> Annotated[CallToolResult, AskOutput]:
        try:
            text = _check_text(question, "question", MAX_QUESTION_CHARS)
            bot = bot_or_default(bot_id)
        except _InvalidRequest as exc:
            return _reply(AskOutput(outcome="error", error=invalid(exc)))
        result = await call("ask", bot, text, principal)
        if isinstance(result, SafeError):
            return _reply(AskOutput(outcome="error", bot_id=bot, error=result))
        return _reply(ask_output(result))

    @server.tool(name="search", title="Search approved documents", annotations=read_only)
    async def search(
        query: Annotated[str, Field(description=f"Search words, at most {MAX_QUERY_CHARS} characters.")],
        bot_id: Annotated[str | None, Field(description="Bot id from list_bots; omit for the default bot.")] = None,
        limit: Annotated[int, Field(description=f"Number of passages, 1 to {MAX_SEARCH_LIMIT}.")] = 5,
    ) -> Annotated[CallToolResult, SearchOutput]:
        """Search one bot's approved, current documents and return ranked passages with filename,
        locator, status and text. No model is called; passages are evidence, not answers."""
        try:
            text = _check_text(query, "query", MAX_QUERY_CHARS)
            bot = bot_or_default(bot_id)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
                raise _InvalidRequest(f"The limit must be between 1 and {MAX_SEARCH_LIMIT}.")
        except _InvalidRequest as exc:
            return _reply(SearchOutput(error=invalid(exc)))
        result = await call("search", bot, text, principal, limit)
        return _reply(SearchOutput(error=result) if isinstance(result, SafeError) else search_output(result))

    @server.tool(name="get_section", title="Read one section", annotations=read_only)
    async def get_section(
        section_id: Annotated[str, Field(description="A section_id from search results or citations.")],
        bot_id: Annotated[str | None, Field(description="Bot id from list_bots; omit for the default bot.")] = None,
    ) -> Annotated[CallToolResult, SectionOutput]:
        """Return one whole section of an approved document, for reading a passage in context."""
        try:
            sid = _check_text(section_id, "section id", MAX_SECTION_ID_CHARS).strip()
            bot = bot_or_default(bot_id)
        except _InvalidRequest as exc:
            return _reply(SectionOutput(error=invalid(exc)))
        result = await call("get_section", bot, sid, principal)
        return _reply(SectionOutput(error=result) if isinstance(result, SafeError) else section_output(result))

    doctor_task: asyncio.Future[Any] | None = None

    @server.resource("kestrel://status", name="status", mime_type="application/json")
    async def status() -> str:
        """Health summary and current load of this server."""
        nonlocal doctor_task
        if doctor_task is None or doctor_task.done():
            doctor_task = asyncio.ensure_future(asyncio.to_thread(lambda: holder.get().doctor()))
        try:
            report = await asyncio.shield(doctor_task)
            doctor: dict[str, Any] = {
                "ok": report.ok,
                "checks": [{"name": c.name, "status": c.status} for c in report.checks],
            }
        except Exception as exc:  # noqa: BLE001 - status must always answer
            log.error("doctor failed: %s", describe_exception(exc))
            doctor = {"ok": False, "checks": [], "error": generic_error("configuration").message}
        limits = admission.limits
        return json.dumps(
            {
                "doctor": doctor,
                **admission.metrics(),
                "limits": {
                    "concurrency": limits.concurrency,
                    "queue_size": limits.queue_size,
                    "queue_timeout_s": limits.queue_timeout_s,
                    "rate_per_minute": limits.rate_per_minute,
                },
            }
        )

    return server


def _service_factory(settings: Settings) -> Callable[[], AssistantService]:
    if os.environ.get("KESTREL_MCP_FAKE", "").strip() == "1":
        from kestrel.interfaces.mcp_fake import FakeAssistantService  # noqa: PLC0415 - test only

        log.warning("KESTREL_MCP_FAKE=1: serving the built-in test service, not real documents.")
        return lambda: FakeAssistantService(bot_id=settings.default_bot)

    def build() -> AssistantService:
        try:
            from kestrel.service import build_service  # noqa: PLC0415 - lazy by design
        except ImportError as exc:
            raise ConfigurationError("The assistant service is not available.", detail=repr(exc)) from exc
        return build_service(settings)

    return build


async def serve_stdio(server: FastMCP, protocol_out: BinaryIO) -> None:
    """Serve `server` over stdin and `protocol_out` (the real stdout)."""
    import anyio  # noqa: PLC0415
    from mcp.server.stdio import stdio_server  # noqa: PLC0415

    stdout = anyio.wrap_file(TextIOWrapper(protocol_out, encoding="utf-8"))
    async with stdio_server(stdout=stdout) as (read_stream, write_stream):
        lowlevel = server._mcp_server  # noqa: SLF001 - FastMCP's own run_stdio_async does the same
        await lowlevel.run(read_stream, write_stream, lowlevel.create_initialization_options())


def main() -> None:
    """Run the server on stdio until the client closes stdin."""
    import anyio  # noqa: PLC0415

    level = os.environ.get("KESTREL_MCP_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        stream=sys.stderr,
        level=level if level in {"DEBUG", "INFO", "WARNING", "ERROR"} else "WARNING",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # anything else that prints must not corrupt the protocol stream
    try:
        settings = Settings()
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError; report fields only
        print(f"kestrel-mcp: invalid settings ({type(exc).__name__}); check KESTREL_* variables.", file=sys.stderr)
        raise SystemExit(1) from None
    server = create_server(_service_factory(settings), settings)
    print("kestrel-mcp: started", file=sys.stderr, flush=True)
    try:
        anyio.run(serve_stdio, server, protocol_out)
    except KeyboardInterrupt:
        pass
    print("kestrel-mcp: stopped", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
