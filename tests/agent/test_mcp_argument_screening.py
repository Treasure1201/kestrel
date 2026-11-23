"""Every MCP tool argument is screened: numbers and dictionary keys as well as strings."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from kestrel.agent.mcp_tools import guard_tool
from kestrel.contracts import PrivacyPolicy
from kestrel.privacy import RuleBasedScreen

ID_NUMBER = 8001015009087  # a checksum-valid identity number shape, synthetic


def _tool(schema: dict[str, Any]) -> tuple[StructuredTool, list[dict[str, Any]]]:
    received: list[dict[str, Any]] = []

    async def run(**kwargs: Any) -> str:
        received.append(kwargs)
        return "ok"

    return StructuredTool(name="lookup", description="Look something up.", args_schema=schema, coroutine=run), received


def _call(arguments: dict[str, Any], policy: str, schema: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    tool, received = _tool(schema or {"type": "object", "properties": {key: {} for key in arguments}})
    guarded = guard_tool(tool, "srv", RuleBasedScreen(), PrivacyPolicy(on_personal_information=policy))  # type: ignore[arg-type]
    reply = asyncio.run(guarded.coroutine(**arguments))  # type: ignore[misc]
    return reply, received


ARGUMENTS = [
    {"id": ID_NUMBER},
    {"id": float(ID_NUMBER)},
    {"ids": [1, [2, ID_NUMBER]]},
    {"filter": {"nested": {"value": ID_NUMBER}}},
    {"filter": {str(ID_NUMBER): "x"}},
]


@pytest.mark.parametrize("arguments", ARGUMENTS)
def test_block_policy_refuses_numbers_and_keys_that_match(arguments: dict[str, Any]) -> None:
    reply, received = _call(arguments, "block")
    assert reply.startswith("Error:") and "personal information" in reply
    assert received == []


@pytest.mark.parametrize("arguments", ARGUMENTS)
def test_redact_policy_never_sends_the_number(arguments: dict[str, Any]) -> None:
    schema = {"type": "object", "properties": {key: {"type": ["string", "number", "array", "object"], "items": {"type": ["string", "number", "array"], "items": {"type": ["string", "number"]}}, "properties": {"nested": {"type": "object", "properties": {"value": {"type": ["string", "number"]}}}}} for key in arguments}}
    reply, received = _call(arguments, "redact", schema)
    assert reply.endswith("ok"), reply
    sent = repr(received)
    assert str(ID_NUMBER) not in sent and str(float(ID_NUMBER)) not in sent
    assert "[" in sent


def test_redacted_number_where_the_schema_needs_a_number_is_a_tool_error() -> None:
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
    reply, received = _call({"id": ID_NUMBER}, "redact", schema)
    assert reply.startswith("Error:") and "needs a number" in reply
    assert received == []


def test_ordinary_numbers_pass_unchanged() -> None:
    reply, received = _call({"days": 8, "rate": 0.5, "flag": True}, "block")
    assert reply.endswith("ok")
    assert received == [{"days": 8, "rate": 0.5, "flag": True}]
