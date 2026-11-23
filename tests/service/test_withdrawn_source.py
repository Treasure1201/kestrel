"""A document withdrawn while the model is running cannot be cited as current."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fakes import tool_call
from service.workspace import AGENT, OPERATOR, cite, make_workspace, scripted, service_with

from kestrel.contracts import DocumentStatus, Outcome
from kestrel.service import LocalAssistantService

QUOTE = "aims to make a decision within 10 working days of receiving all required documents"
ANSWER = "The team aims to decide within 10 working days of receiving all required documents."


def _then(action: Callable[[], None], reply: Callable[[Any], Any]) -> Callable[[Any], Any]:
    def run(messages: Any) -> Any:
        action()
        return reply(messages)

    return run


def _guide_2026(service: LocalAssistantService) -> str:
    return next(d.id for d in service.list_documents("demo", OPERATOR) if d.filename == "guide_2026.md")


def _ask(tmp_path: Path, change: Callable[[LocalAssistantService, str], None], on_failure: str = "withhold") -> Any:
    holder: dict[str, LocalAssistantService] = {}

    def act() -> None:
        service = holder["service"]
        change(service, _guide_2026(service))

    model, factory = scripted(tool_call("search_documents", {"query": "claim decision working days"}), _then(act, cite(ANSWER, [QUOTE])))
    service = holder["service"] = service_with(make_workspace(tmp_path, on_failure=on_failure), factory)
    return service.ask("demo", "How long does a claim decision take?", AGENT)


def test_document_blocked_during_the_run_withholds_the_answer(tmp_path: Path) -> None:
    result = _ask(tmp_path, lambda service, doc_id: service.block_document(doc_id, OPERATOR, "withdrawn for review"))
    assert result.outcome == Outcome.WITHHELD
    assert "withdrawn while the answer was being prepared" in (result.reason or "")
    assert result.evidence_report is not None and not result.evidence_report.passed
    assert any(i.check == "source_status" and "withdrawn" in i.detail for i in result.evidence_report.issues)


def test_document_superseded_during_the_run_is_no_longer_eligible(tmp_path: Path) -> None:
    def supersede(service: LocalAssistantService, doc_id: str) -> None:
        service._rt.store.documents.set_status(doc_id, DocumentStatus.SUPERSEDED, reason="test")

    result = _ask(tmp_path, supersede)
    assert result.outcome == Outcome.WITHHELD
    assert "withdrawn" in (result.reason or "")


def test_unchanged_sources_still_pass(tmp_path: Path) -> None:
    result = _ask(tmp_path, lambda service, doc_id: None)
    assert result.outcome == Outcome.ANSWERED, result.evidence_report
