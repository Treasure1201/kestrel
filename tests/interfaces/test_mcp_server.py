"""The MCP server in process, over the real MCP protocol (in-memory streams), with a fake service."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from kestrel.config import Settings
from kestrel.contracts import MAX_QUESTION_CHARS, Principal
from kestrel.errors import ConfigurationError, NotIngestedError
from kestrel.interfaces.mcp_admission import McpLimits
from kestrel.interfaces.mcp_fake import FAKE_SECTION_ID, RESTRICTED_BOT, FakeAssistantService
from kestrel.interfaces.mcp_server import create_server

BOT = "example-bot"


class ControlledService(FakeAssistantService):
    """The fake, plus a slow `ask` gated by an event and injectable failures."""

    def __init__(self) -> None:
        super().__init__(bot_id=BOT)
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.slow = False
        self.search_error: Exception | None = None
        self.principals: list[Principal] = []

    def ask(self, bot_id: str, question: str, principal: Principal, model: str | None = None):  # type: ignore[no-untyped-def]
        self.principals.append(principal)
        if self.slow:
            self.entered.set()
            self.gate.wait(10)
        return super().ask(bot_id, question, principal, model)

    def search(self, bot_id: str, query: str, principal: Principal, limit: int = 8):  # type: ignore[no-untyped-def]
        if self.search_error is not None:
            raise self.search_error
        return super().search(bot_id, query, principal, limit)


@pytest.fixture
def mcp_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"default_bot": BOT})


def run(
    service: Any,
    settings: Settings,
    body: Callable[[ClientSession, Any], Awaitable[Any]],
    limits: McpLimits | None = None,
) -> Any:
    server = create_server(service, settings, limits or McpLimits())

    async def main() -> Any:
        async with create_connected_server_and_client_session(server) as session:
            return await body(session, server)

    return asyncio.run(asyncio.wait_for(main(), timeout=30))


def test_tools_and_resource_are_listed(mcp_settings: Settings) -> None:
    async def body(session: ClientSession, _: Any) -> Any:
        tools = (await session.list_tools()).tools
        resources = (await session.list_resources()).resources
        return {t.name: t for t in tools}, [str(r.uri) for r in resources]

    tools, resources = run(FakeAssistantService(BOT), mcp_settings, body)
    assert sorted(tools) == ["ask", "get_section", "list_bots", "search"]
    assert all(tool.outputSchema for tool in tools.values())
    description = tools["ask"].description or ""
    assert "approved documents" in description and "unchanged" in description and "not_found" in description
    assert tools["search"].annotations.readOnlyHint is True
    assert resources == ["kestrel://status"]


def test_list_bots_shape(mcp_settings: Settings) -> None:
    async def body(session: ClientSession, _: Any) -> Any:
        return await session.call_tool("list_bots", {})

    reply = run(FakeAssistantService(BOT), mcp_settings, body)
    assert not reply.isError
    assert reply.structuredContent == {
        "bots": [{"id": BOT, "name": "Example bot", "description": "Synthetic bot for tests."}],
        "error": None,
    }
    assert json.loads(reply.content[0].text) == reply.structuredContent


def test_ask_shape_and_principal(mcp_settings: Settings) -> None:
    service = ControlledService()

    async def body(session: ClientSession, _: Any) -> Any:
        return await session.call_tool("ask", {"question": "When is the help desk open?"})

    reply = run(service, mcp_settings, body)
    data = reply.structuredContent
    assert not reply.isError and data["outcome"] == "answered" and data["error"] is None
    assert set(data) >= {
        "answer", "reason", "citations", "calculations", "caveats", "evidence_checks_passed",
        "evidence_issues", "redactions", "model", "latency_ms", "trace_id",
    }
    citation = data["citations"][0]
    assert set(citation) >= {"filename", "locator", "quote", "edition", "status"}
    assert data["evidence_checks_passed"] is True and data["bot_id"] == BOT
    assert service.principals[0].channel == "mcp" and service.principals[0].user == mcp_settings.user


def test_search_and_get_section_shapes(mcp_settings: Settings) -> None:
    async def body(session: ClientSession, _: Any) -> Any:
        found = await session.call_tool("search", {"query": "opening hours", "limit": 3})
        section = await session.call_tool("get_section", {"section_id": FAKE_SECTION_ID, "bot_id": BOT})
        return found, section

    found, section = run(FakeAssistantService(BOT), mcp_settings, body)
    passage = found.structuredContent["passages"][0]
    assert set(passage) >= {"filename", "locator", "status", "text", "section_id"}
    assert not section.isError and section.structuredContent["text"].startswith("The help desk")


def test_unknown_and_restricted_bots(mcp_settings: Settings) -> None:
    async def body(session: ClientSession, _: Any) -> Any:
        return (
            await session.call_tool("ask", {"question": "hello", "bot_id": "no-such-bot"}),
            await session.call_tool("search", {"query": "hello", "bot_id": "no-such-bot"}),
            await session.call_tool("get_section", {"section_id": "x", "bot_id": RESTRICTED_BOT}),
            await session.call_tool("ask", {"question": "hello", "bot_id": "Not A Valid Id!"}),
        )

    ask, search, section, malformed = run(FakeAssistantService(BOT), mcp_settings, body)
    assert ask.isError and ask.structuredContent["error"]["category"] == "invalid_input"
    assert search.isError and "Unknown bot" in search.structuredContent["error"]["message"]
    assert section.isError and section.structuredContent["error"]["category"] == "access_denied"
    assert malformed.isError and malformed.structuredContent["error"]["category"] == "invalid_input"


def test_input_limits(mcp_settings: Settings) -> None:
    async def body(session: ClientSession, server: Any) -> Any:
        replies = [
            await session.call_tool("ask", {"question": "x" * (MAX_QUESTION_CHARS + 1)}),
            await session.call_tool("ask", {"question": "   "}),
            await session.call_tool("search", {"query": "q", "limit": 500}),
            await session.call_tool("get_section", {"section_id": "s" * 500}),
        ]
        return replies, server.admission.metrics()

    replies, metrics = run(ControlledService(), mcp_settings, body)
    assert all(r.isError and r.structuredContent["error"]["category"] == "invalid_input" for r in replies)
    assert "longer than 2000" in replies[0].structuredContent["error"]["message"]
    assert metrics["rejected_by_reason"]["invalid_input"] == 4


def test_errors_are_safe_and_typed(mcp_settings: Settings) -> None:
    service = ControlledService()

    async def body(session: ClientSession, _: Any) -> Any:
        out = []
        for error in (
            RuntimeError("boom at C:\\secret\\internal.py"),
            ConfigurationError("Bad file C:\\Users\\someone\\config\\x.yaml"),
            NotIngestedError("No documents yet; run `kestrel ingest`."),
        ):
            service.search_error = error
            out.append(await session.call_tool("search", {"query": "q"}))
        return out

    internal, config, not_ingested = run(service, mcp_settings, body)
    for reply in (internal, config, not_ingested):
        assert reply.isError
        text = reply.content[0].text
        assert "Traceback" not in text and "C:\\" not in text and "secret" not in text
    assert internal.structuredContent["error"]["category"] == "internal"
    assert config.structuredContent["error"]["category"] == "configuration"
    assert not_ingested.structuredContent["error"] == {
        "category": "not_ingested",
        "message": "No documents yet; run `kestrel ingest`.",
    }


def test_failing_service_factory_gives_configuration_error(mcp_settings: Settings) -> None:
    def factory() -> Any:
        raise ConfigurationError("Database missing at C:\\data\\x.sqlite")

    async def body(session: ClientSession, _: Any) -> Any:
        return await session.call_tool("list_bots", {})

    reply = run(factory, mcp_settings, body)
    assert reply.isError and reply.structuredContent["error"]["category"] == "configuration"
    assert "C:\\" not in reply.content[0].text


async def _wait_for(event: threading.Event) -> None:
    for _ in range(500):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("event never set")


def test_slow_call_does_not_block_another_request(mcp_settings: Settings) -> None:
    service = ControlledService()
    service.slow = True

    async def body(session: ClientSession, _: Any) -> Any:
        slow = asyncio.ensure_future(session.call_tool("ask", {"question": "slow one"}))
        await _wait_for(service.entered)
        started = time.monotonic()
        quick = await session.call_tool("list_bots", {})
        elapsed = time.monotonic() - started
        service.gate.set()
        return quick, elapsed, await slow

    quick, elapsed, slow = run(service, mcp_settings, body, McpLimits(concurrency=2, queue_size=0))
    assert not quick.isError and elapsed < 2.0
    assert slow.structuredContent["outcome"] == "answered"


def test_full_queue_is_rejected_as_busy(mcp_settings: Settings) -> None:
    service = ControlledService()
    service.slow = True

    async def body(session: ClientSession, server: Any) -> Any:
        slow = asyncio.ensure_future(session.call_tool("ask", {"question": "slow one"}))
        await _wait_for(service.entered)
        rejected = await session.call_tool("list_bots", {})
        metrics = server.admission.metrics()
        service.gate.set()
        await slow
        return rejected, metrics

    rejected, metrics = run(service, mcp_settings, body, McpLimits(concurrency=1, queue_size=0))
    assert rejected.isError and rejected.structuredContent["error"]["category"] == "busy"
    assert metrics["in_flight"] == 1 and metrics["rejected_by_reason"]["busy"] == 1


def test_queue_wait_times_out_as_busy(mcp_settings: Settings) -> None:
    service = ControlledService()
    service.slow = True

    async def body(session: ClientSession, _: Any) -> Any:
        slow = asyncio.ensure_future(session.call_tool("ask", {"question": "slow one"}))
        await _wait_for(service.entered)
        started = time.monotonic()
        waited = await session.call_tool("list_bots", {})
        elapsed = time.monotonic() - started
        service.gate.set()
        await slow
        return waited, elapsed

    waited, elapsed = run(service, mcp_settings, body, McpLimits(concurrency=1, queue_size=1, queue_timeout_s=0.3))
    assert waited.isError and waited.structuredContent["error"]["category"] == "busy"
    assert 0.25 <= elapsed < 3.0


def test_rate_limit(mcp_settings: Settings) -> None:
    async def body(session: ClientSession, server: Any) -> Any:
        replies = [await session.call_tool("list_bots", {}) for _ in range(3)]
        status = await session.read_resource("kestrel://status")
        return replies, json.loads(status.contents[0].text)

    replies, status = run(FakeAssistantService(BOT), mcp_settings, body, McpLimits(rate_per_minute=2))
    assert [r.isError for r in replies] == [False, False, True]
    assert replies[2].structuredContent["error"]["category"] == "rate_limited"
    assert status["rejected_by_reason"]["rate_limited"] == 1 and status["rejected"] == 1


def test_status_resource(mcp_settings: Settings) -> None:
    async def body(session: ClientSession, _: Any) -> Any:
        return json.loads((await session.read_resource("kestrel://status")).contents[0].text)

    status = run(FakeAssistantService(BOT), mcp_settings, body)
    assert status["doctor"] == {"ok": True, "checks": [{"name": "fake service", "status": "ok"}]}
    assert status["in_flight"] == 0 and status["queued"] == 0 and status["rejected"] == 0
    assert status["limits"]["concurrency"] == 2


def test_limits_from_environment_fall_back_on_bad_values() -> None:
    limits = McpLimits.from_env(
        {"KESTREL_MCP_CONCURRENCY": "3", "KESTREL_MCP_QUEUE_SIZE": "-1", "KESTREL_MCP_RATE_PER_MINUTE": "lots"}
    )
    assert limits == McpLimits(concurrency=3)
