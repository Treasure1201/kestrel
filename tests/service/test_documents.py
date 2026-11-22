"""Search, sections, findings, ingest, block, unblock, affected answers and doctor through the service."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import tool_call
from service.workspace import AGENT, OPERATOR, cite, make_workspace, scripted, service_with

from kestrel.contracts import DocumentStatus, Outcome
from kestrel.errors import InvalidInputError

CLAIMS_QUOTE = "aims to make a decision within 10 working days of receiving all required documents"


def test_ingest_is_idempotent_and_sets_statuses(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    statuses = {d.filename: d.status for d in service.list_documents("demo", AGENT)}
    assert statuses == {"guide_2025.md": DocumentStatus.SUPERSEDED, "guide_2026.md": DocumentStatus.CURRENT}
    again = service.ingest(OPERATOR, "demo")
    assert not again.changed


def test_search_is_screened_and_scoped(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    result = service.search("demo", "Option B waiting period", AGENT)
    assert result.hits and all(hit.status == DocumentStatus.CURRENT for hit in result.hits)
    old = service.search("demo", "Option B waiting period in the 2025 edition", AGENT)
    assert old.scope is not None and old.scope.edition == "2025"
    assert service.search("demo", "compare the 2025 and 2026 editions", AGENT).clarification
    with pytest.raises(InvalidInputError):
        service.search("demo", "x", AGENT, limit=0)


def test_get_section_and_findings_are_limited_to_the_bot(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    hit = service.search("demo", "free cover limit", AGENT).hits[0]
    section = service.get_section("demo", hit.section_id, AGENT)
    assert section.document_id == hit.document_id
    secret_doc = service.list_documents("secret", OPERATOR)[0]
    with pytest.raises(InvalidInputError):
        service.list_findings("demo", AGENT, secret_doc.id)
    with pytest.raises(InvalidInputError):
        service.get_section("demo", f"{secret_doc.id}:s001", AGENT)
    assert service.list_findings("demo", AGENT) == []


def test_blocked_document_disappears_and_affected_answers_lists_the_earlier_answer(tmp_path: Path) -> None:
    model, factory = scripted(
        tool_call("search_documents", {"query": "claim decision working days"}),
        cite("The team aims to decide within 10 working days of receiving all required documents.", [CLAIMS_QUOTE]),
    )
    service = service_with(make_workspace(tmp_path), factory)
    answer = service.ask("demo", "How long does a claim decision take?", AGENT)
    assert answer.outcome == Outcome.ANSWERED
    document_id = answer.citations[0].document_id

    blocked = service.block_document(document_id, OPERATOR, "withdrawn for review")
    assert blocked.status == DocumentStatus.BLOCKED
    hits = service.search("demo", "claim decision working days", AGENT).hits
    assert all(hit.document_id != document_id for hit in hits)
    affected = service.affected_answers(document_id, OPERATOR)
    assert [record.entry.trace_id for record in affected] == [answer.trace_id]
    with pytest.raises(InvalidInputError):
        service.get_section("demo", answer.citations[0].section_id, AGENT)

    restored = service.unblock_document(document_id, OPERATOR, "review done")
    assert restored.status == DocumentStatus.CURRENT
    kinds = [record.entry.kind for record in service.audit_tail(OPERATOR, bot_id="demo")]
    assert kinds[:2] == ["unblock", "block"]
    with pytest.raises(InvalidInputError):
        service.unblock_document(document_id, OPERATOR, "again")
    assert service.audit_verify(OPERATOR).ok


def test_unknown_document_cannot_be_probed(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    secret_doc = service.list_documents("secret", OPERATOR)[0]
    with pytest.raises(InvalidInputError):
        service.affected_answers(secret_doc.id, AGENT)
    with pytest.raises(InvalidInputError):
        service.affected_answers("0" * 16, OPERATOR)


def test_doctor_reports_without_secrets(tmp_path: Path) -> None:
    model, factory = scripted()
    settings = make_workspace(tmp_path).model_copy(update={"anthropic_api_key": "sk-test-secret-value"})
    service = service_with(settings, factory)
    report = service.doctor()
    names = [check.name for check in report.checks]
    assert {"database", "vector extension", "embedding model", "provider keys", "bot demo", "documents demo", "audit chain"} <= set(names)
    text = " ".join(f"{c.detail} {c.fix or ''}" for c in report.checks)
    assert "sk-test-secret-value" not in text
    assert "ANTHROPIC_API_KEY" in text
    by_name = {check.name: check for check in report.checks}
    assert by_name["egress secret"].status == "fail"
    assert by_name["documents demo"].status == "ok"
