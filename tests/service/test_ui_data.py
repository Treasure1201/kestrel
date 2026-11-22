"""Data the front ends show: answer steps, why a question was not answered, counts, editions and examples."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import tool_call
from service.workspace import AGENT, OPERATOR, OUTSIDER, cite, decline, make_workspace, scripted, service_with

from kestrel.contracts import Outcome
from kestrel.errors import AccessDeniedError, InvalidInputError

CLAIMS_QUOTE = "aims to make a decision within 10 working days of receiving all required documents"
BENEFIT_QUOTE = "The monthly benefit is 75% of monthly salary, up to a maximum of R50 000 per month."


def test_steps_record_privacy_scope_tools_and_checks_in_order(tmp_path: Path) -> None:
    model, factory = scripted(
        tool_call("search_documents", {"query": "monthly benefit salary maximum"}),
        tool_call("calculate", {"expression": "0.75 * 40000"}),
        cite("The benefit is R30 000 a month (75% of R40 000), below the R50 000 maximum.", [BENEFIT_QUOTE],
             calculations=[("0.75 * 40000", "30000", BENEFIT_QUOTE)]),
    )
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "What is the monthly benefit for a salary of R40 000? My email is a@b.co", AGENT)

    assert result.outcome == Outcome.ANSWERED, result.evidence_report
    # A search hit that is a whole table is shown as rows at once, which adds a `table` step.
    steps = [s for s in result.steps if s.kind != "table"]
    assert [s.kind for s in steps] == ["privacy", "scope", "search", "calculate", "checks"]
    privacy, scope, search, calculate, checks = steps
    assert "email" in privacy.detail and "a@b.co" not in privacy.detail
    assert scope.detail == "current documents only"
    assert '"monthly benefit salary maximum"' in search.detail and "hits" in search.detail
    assert calculate.detail == "0.75 * 40000 = 30000"
    assert checks.label == "Evidence checks passed"


def test_table_and_repair_steps(tmp_path: Path) -> None:
    from fakes import final_answer
    from service.workspace import label_for

    from kestrel.contracts import AgentAnswer, CitationOut

    def table_call(messages):  # type: ignore[no-untyped-def]
        return tool_call("get_table", {"label": label_for(messages, "100")})

    def bad(messages):  # type: ignore[no-untyped-def]
        citation = CitationOut(label=label_for(messages, "100"), quote="band three is about R3.5m", supports="limit")
        return final_answer(AgentAnswer(status="answered", answer="Band 3 has a limit of R3 500 000.", citations=[citation]), call_id="bad")

    row = "Members from: 100; Members to: 249; Free cover limit (R): R 3 500 000"
    model, factory = scripted(
        tool_call("search_documents", {"query": "free cover limit band members"}), table_call, bad,
        cite("A scheme of 120 members falls in band 3, with a free cover limit of R3 500 000.", [row]),
    )
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "What is the free cover limit for a scheme with 120 members?", AGENT)

    assert result.outcome == Outcome.ANSWERED, result.evidence_report
    kinds = [s.kind for s in result.steps]
    assert kinds[kinds.index("table")] == "table" and "rows" in result.steps[kinds.index("table")].detail
    assert kinds[-3:] == ["checks", "repair", "checks"]
    assert result.steps[-3].label == "Evidence checks failed" and "quote_verbatim" in result.steps[-3].detail
    assert result.steps[-1].label == "Evidence checks passed"
    report = result.evidence_report
    assert report is not None and report.passed and report.failures == ()
    assert report.fixed_in_repair and all(i.severity == "warning" for i in report.fixed_in_repair)


def test_mcp_tool_call_is_a_step(tmp_path: Path) -> None:
    model, factory = scripted(
        tool_call("define", {"term": "waiting period"}, "mcp"),
        tool_call("search_documents", {"query": "Option B waiting period"}),
        cite("Under Option B the waiting period is three months.", ["Option B: the waiting period is three months."]),
    )
    service = service_with(make_workspace(tmp_path, mcp_server=True), factory)
    result = service.ask("demo", "What is the Option B waiting period?", AGENT)
    step = next(s for s in result.steps if s.kind == "mcp_tool")
    assert step.label == "Called a tool" and step.detail.startswith("define on echo:") and "waiting period" in step.detail


def test_decline_audits_reason_and_closest_section(tmp_path: Path) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "claim decision"}), decline("The documents do not mention dental cover."))
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "Is dental cover included?", AGENT)
    entry = service.audit_tail(AGENT)[0].entry
    assert entry.outcome == Outcome.NOT_FOUND
    assert entry.reason == "The documents do not mention dental cover."
    hit = result.retrieved[0].hit
    assert entry.closest == f"{hit.filename} > {hit.locator}"
    assert entry.failed_checks == ()
    assert service.audit_verify(OPERATOR).ok


def test_withheld_audits_failed_checks(tmp_path: Path) -> None:
    unsupported = cite("The team decides within 5 working days.", [CLAIMS_QUOTE])
    model, factory = scripted(tool_call("search_documents", {"query": "claim decision working days"}), unsupported, unsupported)
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "How long does a claim decision take?", AGENT)
    assert result.outcome == Outcome.WITHHELD
    entry = service.audit_tail(AGENT)[0].entry
    assert "numbers_grounded" in entry.failed_checks
    assert entry.reason == result.reason
    assert entry.closest and entry.closest.startswith("guide_2026.md")


def test_reason_from_the_question_follows_the_audit_text_policy(tmp_path: Path) -> None:
    """With `audit_question_text: none`, a reason written from the question is not stored; the rest is."""
    model, factory = scripted(tool_call("search_documents", {"query": "dental"}), decline("Nothing on dental cover for a@b.co."))
    service = service_with(make_workspace(tmp_path, audit_text="none"), factory)
    service.ask("demo", "Is dental cover included?", AGENT)
    entry = service.audit_tail(AGENT)[0].entry
    assert entry.question_text is None and entry.reason is None
    assert entry.closest is not None

    clarified = service.ask("demo", "Compare the 2025 and 2026 editions", AGENT)
    assert clarified.clarification
    assert service.audit_tail(AGENT)[0].entry.reason is None  # a clarification quotes the question


def test_stored_model_reason_is_screened(tmp_path: Path) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "dental"}), decline("Nothing on dental cover; write to a@b.co."))
    service = service_with(make_workspace(tmp_path), factory)
    service.ask("demo", "Is dental cover included?", AGENT)
    reason = service.audit_tail(AGENT)[0].entry.reason or ""
    assert "a@b.co" not in reason and "Nothing on dental cover" in reason


def test_application_reasons_are_stored_whatever_the_policy(tmp_path: Path) -> None:
    service = service_with(make_workspace(tmp_path, audit_text="none", on_pi="block"), scripted()[1])
    result = service.ask("demo", "My email is a@b.co, what is covered?", AGENT)
    assert result.outcome == Outcome.REFUSED
    assert result.steps[0].kind == "privacy"
    entry = service.audit_tail(AGENT)[0].entry
    assert entry.reason == result.reason and "a@b.co" not in (entry.reason or "")


def test_documents_have_chunk_and_table_counts(tmp_path: Path) -> None:
    service = service_with(make_workspace(tmp_path), scripted()[1])
    docs = {d.filename: d for d in service.list_documents("demo", AGENT)}
    new = docs["guide_2026.md"]
    assert new.section_count >= 4 and new.table_count == 1
    assert new.chunk_count >= new.section_count + 3  # one chunk per table row as well
    assert docs["guide_2025.md"].table_count == 0


def test_compare_editions(tmp_path: Path) -> None:
    service = service_with(make_workspace(tmp_path), scripted()[1])
    docs = {d.filename: d for d in service.list_documents("demo", AGENT)}
    result = service.compare_editions("demo", docs["guide_2026.md"].id, AGENT)
    assert [d.edition for d in result.editions] == ["2025", "2026"]
    assert result.older is not None and result.older.edition == "2025"
    assert result.newer is not None and result.newer.edition == "2026"
    kinds = {c.heading.split(" > ")[-1]: c.change for c in result.changes}
    assert kinds["Waiting periods"] == "changed"
    assert kinds["Claims"] == "added" and kinds["Free cover limits"] == "added"
    same = service.compare_editions("demo", docs["guide_2025.md"].id, AGENT)
    assert same.older == result.older and same.newer == result.newer  # asked from either edition


def test_compare_editions_access_rules(tmp_path: Path) -> None:
    service = service_with(make_workspace(tmp_path), scripted()[1])
    doc = service.list_documents("demo", AGENT)[0]
    with pytest.raises(AccessDeniedError):
        service.compare_editions("demo", doc.id, OUTSIDER)
    with pytest.raises(InvalidInputError):
        service.compare_editions("demo", "0" * 16, AGENT)
    secret = service.list_documents("secret", OPERATOR)[0]
    with pytest.raises(InvalidInputError):
        service.compare_editions("demo", secret.id, AGENT)  # another bot's document is unknown here
