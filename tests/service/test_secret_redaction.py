"""Provider exception text never reaches a log, an audit row, an AskResult or an MCP error result."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fakes import tool_call
from pydantic import SecretStr
from service.workspace import AGENT, make_workspace, scripted, service_with

from kestrel.contracts import Outcome
from kestrel.errors import ProviderUnavailableError
from kestrel.interfaces.mcp_models import error_from_exception
from kestrel.llm import explain_provider_error
from kestrel.redaction import REDACTED, describe_exception, redact_secrets

TOKEN = "sk-ant-test-0123456789abcdef"
CONFIGURED = "plainconfiguredkeyvalue42"


class _ProviderFailure(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"401 Unauthorized; request headers: {{'Authorization': 'Bearer {TOKEN}'}} api_key={CONFIGURED}")
        self.status_code = status_code


def _raise(exc: Exception):  # type: ignore[no-untyped-def]
    def reply(messages):  # type: ignore[no-untyped-def]
        raise exc

    return reply


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Bearer {TOKEN}",
        f"authorization={TOKEN}",
        f"bearer {TOKEN}",
        f"failed with key {TOKEN} attached",
        f"url?api_key={CONFIGURED}&x=1",
        f"x-api-key: {CONFIGURED}",
        f'{{"Authorization": "Bearer {TOKEN}"}}',
    ],
)
def test_redact_secrets_removes_credentials(text: str) -> None:
    cleaned = redact_secrets(text)
    assert TOKEN not in cleaned and CONFIGURED not in cleaned
    assert REDACTED in cleaned


def test_redact_secrets_removes_configured_key_values(monkeypatch: pytest.MonkeyPatch, settings) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "envkeyvalue-without-prefix")
    keyed = settings.model_copy(update={"anthropic_api_key": SecretStr(CONFIGURED)})
    cleaned = redact_secrets(f"echo {CONFIGURED} and envkeyvalue-without-prefix", keyed)
    assert CONFIGURED not in cleaned and "envkeyvalue" not in cleaned
    assert redact_secrets("nothing secret here") == "nothing secret here"


def test_provider_error_detail_has_only_class_and_status() -> None:
    error = explain_provider_error(_ProviderFailure(401), "anthropic:claude-haiku-5-5")
    assert error.detail == "_ProviderFailure HTTP 401"
    assert TOKEN not in error.user_message and TOKEN not in (error.detail or "")
    assert describe_exception(RuntimeError(TOKEN), "internal") == "RuntimeError category=internal"


def test_mcp_error_result_is_redacted() -> None:
    safe = error_from_exception(ProviderUnavailableError(f"provider said Authorization: Bearer {TOKEN}"))
    assert TOKEN not in safe.message


@pytest.mark.parametrize("exc", [_ProviderFailure(401), RuntimeError(f"boom Authorization: Bearer {TOKEN} api_key={CONFIGURED}")])
def test_ask_never_logs_or_stores_exception_text(tmp_path: Path, caplog: pytest.LogCaptureFixture, exc: Exception) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "claim decision"}), _raise(exc))
    service = service_with(make_workspace(tmp_path), factory)
    with caplog.at_level(logging.DEBUG, logger="kestrel"):
        result = service.ask("demo", "How long does a claim decision take?", AGENT)
    assert result.outcome == Outcome.ERROR
    stored = result.model_dump_json() + "".join(r.entry.model_dump_json() for r in service.audit_tail(AGENT))
    logged = caplog.text + "".join(str(r.exc_text or "") for r in caplog.records)
    for secret in (TOKEN, CONFIGURED):
        assert secret not in stored
        assert secret not in logged
    assert all(record.exc_info is None for record in caplog.records)
    assert type(exc).__name__ in caplog.text
