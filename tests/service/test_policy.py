"""Privacy, access, egress, provider errors and concurrency through the service."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest
from fakes import tool_call
from service.workspace import AGENT, OPERATOR, OUTSIDER, cite, decline, make_workspace, scripted, service_with

from kestrel.contracts import Outcome, Principal
from kestrel.errors import AccessDeniedError

CLAIMS_QUOTE = "aims to make a decision within 10 working days of receiving all required documents"
# A syntactically valid, made-up South African ID number (date 1980-01-01, checksum valid).
ID_NUMBER = "8001015009087"


def _human_text(model) -> str:  # type: ignore[no-untyped-def]
    return " ".join(str(m.content) for call in model.calls for m in call if type(m).__name__ == "HumanMessage")


def test_redaction_happens_before_the_model_and_the_audit(tmp_path: Path) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "claim decision"}), decline(status="refused", reason="Ask the claims team."))
    service = service_with(make_workspace(tmp_path), factory)
    question = f"My name is Thandi Mokoena and my ID number is {ID_NUMBER}. Will my claim be paid?"
    result = service.ask("demo", question, AGENT)

    seen = _human_text(model)
    assert ID_NUMBER not in seen and "Thandi" not in seen
    assert "[" in seen  # placeholders reached the model instead
    assert "sa_id" in result.redactions
    assert ID_NUMBER not in result.question
    entry = service.audit_tail(AGENT)[0].entry
    assert entry.question_text is not None and ID_NUMBER not in entry.question_text and "Thandi" not in entry.question_text
    assert entry.redactions == result.redactions
    assert result.outcome == Outcome.REFUSED


def test_audit_keeps_only_a_digest_when_the_bot_says_so(tmp_path: Path) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "dental"}), decline())
    service = service_with(make_workspace(tmp_path, audit_text="none"), factory)
    service.ask("demo", "Is dental cover included?", AGENT)
    entry = service.audit_tail(AGENT)[0].entry
    assert entry.question_text is None and entry.question_digest and len(entry.question_digest) == 64


def test_privacy_block_policy_refuses_without_calling_the_model(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path, on_pi="block"), factory)
    result = service.ask("demo", f"ID {ID_NUMBER}: what is the waiting period?", AGENT)
    assert result.outcome == Outcome.REFUSED and result.error_category == "privacy_blocked"
    assert model.calls == []
    assert ID_NUMBER not in result.question
    assert service.audit_tail(AGENT)[0].entry.error_category == "privacy_blocked"


def test_screen_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)

    def broken(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("regex engine exploded")

    monkeypatch.setattr(service._rt.screen, "screen", broken)
    result = service.ask("demo", "What is the waiting period?", AGENT)
    assert result.outcome == Outcome.REFUSED and result.error_category == "privacy_blocked"
    assert model.calls == []


def test_role_refusal_on_ask_and_on_reads(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("secret", "What is the waiting period?", AGENT)
    assert result.outcome == Outcome.REFUSED and result.error_category == "access_denied"
    assert model.calls == []
    assert service.audit_tail(OPERATOR, bot_id="secret")[0].entry.error_category == "access_denied"
    with pytest.raises(AccessDeniedError):
        service.search("secret", "waiting period", AGENT)
    with pytest.raises(AccessDeniedError):
        service.list_documents("secret", AGENT)
    with pytest.raises(AccessDeniedError):
        service.audit_tail(AGENT, bot_id="secret")
    with pytest.raises(AccessDeniedError):
        service.audit_tail(OUTSIDER)
    assert [bot.id for bot in service.list_bots(AGENT)] == ["demo"]
    assert service.list_bots(OUTSIDER) == []
    assert all(record.entry.bot_id == "demo" for record in service.audit_tail(AGENT))


def test_operator_role_is_required(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    document = service.list_documents("demo", AGENT)[0]
    for call in (
        lambda: service.ingest(AGENT),
        lambda: service.block_document(document.id, AGENT, "test"),
        lambda: service.unblock_document(document.id, AGENT, "test"),
        lambda: service.audit_prune(AGENT),
        lambda: service.audit_verify(AGENT),
    ):
        with pytest.raises(AccessDeniedError):
            call()
    assert service.audit_verify(OPERATOR).ok
    assert len(service.audit_prune(OPERATOR)) == 2


def test_confidential_bot_refuses_an_external_model(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    hr = Principal(user="hr-person", roles=frozenset({"hr"}), channel="ui")
    result = service.ask("secret", "What is the waiting period?", hr)
    assert result.outcome == Outcome.REFUSED and result.error_category == "egress_denied"
    assert model.calls == []
    assert "on-device" in (result.reason or "")


def test_provider_error_becomes_a_user_safe_message(tmp_path: Path) -> None:
    def failing(_ref: str):  # type: ignore[no-untyped-def]
        from fakes import ScriptedChatModel

        class Failing(ScriptedChatModel):
            def _generate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                request = httpx.Request("POST", "https://api.example/v1/messages")
                raise httpx.HTTPStatusError("boom sk-secret", request=request, response=httpx.Response(401, request=request))

        return Failing(script=[])

    service = service_with(make_workspace(tmp_path), failing)
    result = service.ask("demo", "What is the waiting period?", AGENT)
    assert result.outcome == Outcome.ERROR and result.error_category == "provider_unavailable"
    assert "sk-secret" not in (result.reason or "")
    assert service.audit_tail(AGENT)[0].entry.outcome == Outcome.ERROR


def test_unexpected_failure_is_an_internal_error(tmp_path: Path) -> None:
    def broken(_ref: str):  # type: ignore[no-untyped-def]
        raise ZeroDivisionError("bug")

    service = service_with(make_workspace(tmp_path), broken)
    result = service.ask("demo", "What is the waiting period?", AGENT)
    assert result.outcome == Outcome.ERROR and result.error_category == "internal"


def _fresh_factory():  # type: ignore[no-untyped-def]
    def factory(_ref: str):  # type: ignore[no-untyped-def]
        model, _ = scripted(
            tool_call("search_documents", {"query": "claim decision working days"}),
            cite("The team aims to decide within 10 working days of receiving all required documents.", [CLAIMS_QUOTE]),
        )
        return model

    return factory


def test_ask_from_several_threads_at_once(tmp_path: Path) -> None:
    service = service_with(make_workspace(tmp_path), _fresh_factory())
    results = []
    lock = threading.Lock()

    def worker() -> None:
        result = service.ask("demo", "How long does a claim decision take?", AGENT)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert len(results) == 6
    assert all(r.outcome == Outcome.ANSWERED for r in results), [r.reason for r in results]
    assert len({r.trace_id for r in results}) == 6
    assert service.audit_verify(OPERATOR).ok


def test_ask_from_inside_a_running_event_loop(tmp_path: Path) -> None:
    service = service_with(make_workspace(tmp_path), _fresh_factory())

    async def handler() -> Outcome:
        return service.ask("demo", "How long does a claim decision take?", AGENT).outcome

    assert asyncio.run(handler()) == Outcome.ANSWERED
