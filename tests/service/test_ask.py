"""`ask` end to end with a scripted model: answers, declines, repair, withhold, warn and audit."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import final_answer, tool_call
from service.workspace import AGENT, OPERATOR, cite, decline, label_for, make_workspace, scripted, service_with

from kestrel.contracts import AgentAnswer, CitationOut, Outcome

CLAIMS_QUOTE = "aims to make a decision within 10 working days of receiving all required documents"
CLAIMS_ANSWER = "The team aims to decide within 10 working days of receiving all required documents."


def test_answer_with_checked_citation_is_returned_and_audited(tmp_path: Path) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "claim decision working days"}), cite(CLAIMS_ANSWER, [CLAIMS_QUOTE]))
    service = service_with(make_workspace(tmp_path), factory)

    result = service.ask("demo", "How long does a claim decision take?", AGENT)

    assert result.outcome == Outcome.ANSWERED, result.evidence_report
    assert result.evidence_report is not None and result.evidence_report.passed
    assert result.citations[0].filename == "guide_2026.md"
    assert result.citations[0].quote == CLAIMS_QUOTE
    assert result.usage.model_calls == 2 and result.usage.searches == 1
    assert result.model == "anthropic:claude-haiku-5-5"
    assert result.retrieved
    records = service.audit_tail(AGENT)
    assert records[0].entry.outcome == Outcome.ANSWERED
    assert records[0].entry.trace_id == result.trace_id
    assert records[0].entry.citations[0].quote == CLAIMS_QUOTE
    assert records[0].entry.question_text == "How long does a claim decision take?"


def test_table_lookup_cites_a_row(tmp_path: Path) -> None:
    def table_call(messages):  # type: ignore[no-untyped-def]
        from service.workspace import label_for

        return tool_call("get_table", {"label": label_for(messages, "100")})

    model, factory = scripted(
        tool_call("search_documents", {"query": "free cover limit band members"}),
        table_call,
        cite("A scheme of 120 members falls in band 3, with a free cover limit of R3 500 000.",
             ["Members from: 100; Members to: 249; Free cover limit (R): R 3 500 000"]),
    )
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "What is the free cover limit for a scheme with 120 members?", AGENT)

    assert result.outcome == Outcome.ANSWERED, result.evidence_report
    assert result.citations[0].label.endswith(".r3")
    assert result.citations[0].row_number == 3
    assert any(e.row is not None for e in result.retrieved)


def test_calculation_is_recomputed(tmp_path: Path) -> None:
    quote = "The monthly benefit is 75% of monthly salary, up to a maximum of R50 000 per month."
    model, factory = scripted(
        tool_call("search_documents", {"query": "monthly benefit salary maximum"}),
        tool_call("calculate", {"expression": "0.75 * 40000"}),
        cite("The benefit is R30 000 a month (75% of R40 000), below the R50 000 maximum.", [quote],
             calculations=[("0.75 * 40000", "30000", quote)]),
    )
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "What is the monthly benefit for a salary of R40 000?", AGENT)
    assert result.outcome == Outcome.ANSWERED, result.evidence_report
    assert result.calculations[0].result == "30000"
    tool_reply = [m for m in model.calls[2] if type(m).__name__ == "ToolMessage"][-1]
    assert "= 30000" in str(tool_reply.content)


def test_correct_decline_has_no_citation_and_is_audited(tmp_path: Path) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "dental cover"}), decline("The documents do not mention dental cover."))
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "Is dental cover included?", AGENT)
    assert result.outcome == Outcome.NOT_FOUND
    assert result.evidence_report is not None and result.evidence_report.passed
    assert result.citations == ()
    assert "do not mention" in (result.reason or "")
    assert service.audit_tail(AGENT)[0].entry.outcome == Outcome.NOT_FOUND


def test_repair_turn_fixes_a_paraphrased_quote(tmp_path: Path) -> None:
    def bad(messages):  # type: ignore[no-untyped-def]
        label = label_for(messages, "10 working days")
        citation = CitationOut(label=label, quote="decisions are made in about two weeks", supports="the time")
        return final_answer(AgentAnswer(status="answered", answer=CLAIMS_ANSWER, citations=[citation]), call_id="bad")

    model, factory = scripted(tool_call("search_documents", {"query": "claim decision working days"}), bad, cite(CLAIMS_ANSWER, [CLAIMS_QUOTE]))
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "How long does a claim decision take?", AGENT)

    assert result.outcome == Outcome.ANSWERED
    assert result.evidence_report is not None and result.evidence_report.passed and result.evidence_report.repaired
    assert result.usage.repairs == 1
    repair_prompt = str(model.calls[2][-1].content)
    assert "quote_verbatim" in repair_prompt


def _unsupported():  # type: ignore[no-untyped-def]
    return cite("The team decides within 5 working days.", [CLAIMS_QUOTE])


def test_failed_repair_withholds_and_returns_the_evidence(tmp_path: Path) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "claim decision working days"}), _unsupported(), _unsupported())
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "How long does a claim decision take?", AGENT)

    assert result.outcome == Outcome.WITHHELD
    assert "could not be confirmed" in result.answer
    assert "5 working days" not in result.answer
    assert result.citations == ()
    assert result.retrieved
    assert result.evidence_report is not None and not result.evidence_report.passed
    assert any(issue.check == "numbers_grounded" for issue in result.evidence_report.issues)
    assert service.audit_tail(AGENT)[0].entry.outcome == Outcome.WITHHELD


def test_warn_policy_returns_the_answer_with_the_failed_report(tmp_path: Path) -> None:
    model, factory = scripted(tool_call("search_documents", {"query": "claim decision working days"}), _unsupported(), _unsupported())
    service = service_with(make_workspace(tmp_path, on_failure="warn"), factory)
    result = service.ask("demo", "How long does a claim decision take?", AGENT)
    assert result.outcome == Outcome.ANSWERED
    assert "5 working days" in result.answer
    assert result.evidence_report is not None and not result.evidence_report.passed


def test_model_that_stops_without_an_answer_is_nudged_once(tmp_path: Path) -> None:
    from langchain_core.messages import AIMessage

    model, factory = scripted(
        tool_call("search_documents", {"query": "claim decision working days"}),
        AIMessage(content="I think it is ten days."),
        cite(CLAIMS_ANSWER, [CLAIMS_QUOTE]),
    )
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "How long does a claim decision take?", AGENT)
    assert result.outcome == Outcome.ANSWERED
    assert "final answer" in str(model.calls[2][-1].content)


def test_search_budget_and_duplicate_guard(tmp_path: Path) -> None:
    model, factory = scripted(
        tool_call("search_documents", {"query": "waiting period"}, "s1"),
        tool_call("search_documents", {"query": "Waiting   PERIOD"}, "s2"),
        tool_call("search_documents", {"query": "claims"}, "s3"),
        tool_call("search_documents", {"query": "benefit amount"}, "s4"),
        tool_call("search_documents", {"query": "free cover"}, "s5"),
        decline(),
    )
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "Tell me about waiting periods", AGENT)
    outputs = {m.tool_call_id: str(m.content) for m in model.calls[-1] if type(m).__name__ == "ToolMessage"}
    assert "already ran this search" in outputs["s2"]
    assert "Search budget reached (3 searches)" in outputs["s5"]
    assert result.usage.searches == 3
    assert result.outcome == Outcome.NOT_FOUND


def test_model_call_budget_forces_an_answer_then_errors(tmp_path: Path) -> None:
    calls = [tool_call("calculate", {"expression": f"{i} + 1"}, f"k{i}") for i in range(20)]
    model, factory = scripted(*calls)
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "Add numbers forever", AGENT)
    assert result.outcome == Outcome.ERROR
    assert result.error_category == "budget_exceeded"
    assert result.usage.model_calls == 8
    assert model.offered_tools[7] == ["AgentAnswer"]  # last allowed call offers only the answer tool
    assert service.audit_tail(AGENT)[0].entry.outcome == Outcome.ERROR


def test_wall_clock_limit_is_enforced(tmp_path: Path) -> None:
    import time

    from fakes import ScriptedChatModel

    class Slow(ScriptedChatModel):
        def _generate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            time.sleep(1.5)
            return super()._generate(*args, **kwargs)

    model = Slow(script=[tool_call("search_documents", {"query": "x"}), decline()])
    settings = make_workspace(tmp_path).model_copy(update={"timeout_s": 0.5})
    service = service_with(settings, lambda _ref: model)
    result = service.ask("demo", "What is the waiting period?", AGENT)
    assert result.outcome == Outcome.ERROR and result.error_category == "budget_exceeded"


def test_bot_with_an_mcp_server_offers_its_allowed_tool(tmp_path: Path) -> None:
    model, factory = scripted(
        tool_call("define", {"term": "waiting period"}, "mcp"),
        tool_call("search_documents", {"query": "Option B waiting period"}),
        cite("Under Option B the waiting period is three months.", ["Option B: the waiting period is three months."]),
    )
    service = service_with(make_workspace(tmp_path, mcp_server=True), factory)
    result = service.ask("demo", "What is the Option B waiting period?", AGENT)
    assert result.outcome == Outcome.ANSWERED, result.evidence_report
    assert sorted(model.offered_tools[0]) == ["AgentAnswer", "calculate", "define", "get_table", "search_documents"]
    reply = next(m for m in model.calls[1] if getattr(m, "tool_call_id", None) == "mcp")
    assert "Time after a claim event" in str(reply.content) and "data, not instructions" in str(reply.content)


def test_superseded_edition_only_when_asked(tmp_path: Path) -> None:
    model, factory = scripted(
        tool_call("search_documents", {"query": "Option B waiting period"}),
        cite("Under the 2025 edition, the Option B waiting period was six months.", ["Option B: the waiting period is six months."]),
    )
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "Under the 2025 edition, what was the Option B waiting period?", AGENT)
    assert result.outcome == Outcome.ANSWERED, result.evidence_report
    assert result.scope is not None and result.scope.edition == "2025"
    assert result.citations[0].status == "superseded"


def test_clarification_needs_no_model(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "Compare the 2025 and 2026 editions on waiting periods", AGENT)
    assert result.outcome == Outcome.NOT_FOUND
    assert result.clarification
    assert model.calls == []


def test_not_ingested_is_an_error(tmp_path: Path) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory, ingest=False)
    result = service.ask("demo", "Anything?", AGENT)
    assert result.outcome == Outcome.ERROR and result.error_category == "not_ingested"


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001])
def test_invalid_question_is_an_audited_error(tmp_path: Path, question: str) -> None:
    model, factory = scripted()
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", question, AGENT)
    assert result.outcome == Outcome.ERROR and result.error_category == "invalid_input"
    assert result.question == ""
    assert service.audit_tail(OPERATOR)[0].entry.error_category == "invalid_input"


def test_an_ungrounded_caveat_is_removed_without_a_repair_turn(tmp_path: Path) -> None:
    kept = "Some documents may take longer to obtain."
    model, factory = scripted(
        tool_call("search_documents", {"query": "claim decision working days"}),
        cite(CLAIMS_ANSWER, [CLAIMS_QUOTE], caveats=[kept, "Urgent cases are decided within 3 working days."]),
    )
    service = service_with(make_workspace(tmp_path), factory)
    result = service.ask("demo", "How long does a claim decision take?", AGENT)

    assert result.outcome == Outcome.ANSWERED, result.evidence_report
    assert list(result.caveats) == [kept]
    assert result.answer == CLAIMS_ANSWER and result.citations[0].quote == CLAIMS_QUOTE
    report = result.evidence_report
    assert report is not None and report.passed and not report.repaired
    assert any(i.severity == "warning" and i.detail.startswith("Caveat removed") for i in report.issues)
    assert any(step.kind == "checks" and "1 caveat removed" in step.detail for step in result.steps)
