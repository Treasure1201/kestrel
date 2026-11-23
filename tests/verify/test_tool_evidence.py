"""MCP tool results (`t1`, ...) as evidence: citable, number-grounding, never a document on their own."""

from __future__ import annotations

from verify.builders import SCOPE, answer, prose

from kestrel.agent.calculator import evaluate
from kestrel.contracts import ToolResult
from kestrel.verify import run_checks

NOTICE = prose("c1", "A request for five or more days must be submitted at least 10 working days before the first day of leave.")
EVIDENCE = {"c1": NOTICE}
DAYS = ToolResult(label="t1", server="calendar", tool="working_days_between",
                  arguments='{"end": "2026-03-20", "start": "2026-03-11"}', text="8")
TODAY = ToolResult(label="t2", server="calendar", tool="today", text="2026-11-23")
TOOLS = {"t1": DAYS, "t2": TODAY}
QUOTE = "must be submitted at least 10 working days before the first day of leave"
QUESTION = "How many working days are there from 11 March to 20 March, and is that enough notice?"


def checks(ans, *, tools=TOOLS, evidence=EVIDENCE, has_documents=True, tool_calculations=()):
    return run_checks(QUESTION, ans, evidence, SCOPE, tool_calculations, tools, has_documents=has_documents)


def names(issues) -> list[str]:
    return [issue.check for issue in issues]


def test_a_number_from_a_cited_tool_result_is_grounded() -> None:
    ans = answer(
        "There are 8 working days from 11 March to 20 March, which is less than the required at least 10 working days.",
        [("c1", QUOTE), ("t1", "8")],
    )
    assert checks(ans) == []


def test_a_number_from_an_uncited_tool_result_points_to_its_label() -> None:
    ans = answer("There are 8 working days, less than the required at least 10 working days.", [("c1", QUOTE)])
    issues = checks(ans)
    assert names(issues) == ["numbers_grounded"]
    assert "t1" in issues[0].detail and "not cited" in issues[0].detail


def test_without_tool_results_the_tool_number_is_ungrounded() -> None:
    ans = answer("There are 8 working days, less than the required at least 10 working days.", [("c1", QUOTE)])
    assert names(checks(ans, tools={})) == ["numbers_grounded"]


def test_a_tool_quote_must_be_verbatim() -> None:
    ans = answer("There are 8 working days, less than at least 10 working days.", [("c1", QUOTE), ("t1", "9")])
    issues = checks(ans)
    assert "quote_verbatim" in names(issues)
    assert any(i.label == "t1" and "tool result" in i.detail for i in issues)


def test_an_unknown_tool_label_is_not_retrieved() -> None:
    issues = checks(answer("At least 10 working days.", [("c1", QUOTE), ("t9", "8")]))
    assert names(issues) == ["label_retrieved"]
    assert "t1" in issues[0].detail  # the available labels include the tool results


def test_tool_numbers_are_calculation_operands() -> None:
    calc = evaluate("10 - 8")
    ans = answer(
        "The request is 2 working days short of the at least 10 working days required.",
        [("c1", QUOTE), ("t1", "8")],
        calculations=[("10 - 8", "2", "c1")],
    )
    assert checks(ans, tool_calculations=[calc]) == []
    uncited = answer("The request is 2 working days short of at least 10 working days.", [("c1", QUOTE)],
                     calculations=[("10 - 8", "2", "c1")])
    assert "calculation_recomputed" in names(checks(uncited, tool_calculations=[calc]))


def test_a_rule_in_words_needs_a_document_citation() -> None:
    ans = answer("Five days of leave need at least ten working days of notice, and you have 8.", [("t1", "8")])
    issues = checks(ans)
    assert "schema" in names(issues)
    assert any("document excerpt" in i.detail for i in issues)


def test_a_tool_result_never_stands_alone_for_a_bot_with_documents() -> None:
    fact = answer("There are 8 working days from 11 March to 20 March.", [("t1", "8")])
    assert names(checks(fact)) == ["schema"]
    invented = answer("All requests are approved within 8 hours without documents.", [("t1", "8")])
    issues = checks(invented)
    assert names(issues) == ["schema"]
    assert "document excerpt" in issues[0].detail
    with_document = answer("There are 8 working days from 11 March to 20 March, less than the required at least 10 working days.",
                           [("c1", QUOTE), ("t1", "8")])
    assert checks(with_document) == []


def test_tool_citations_alone_are_enough_for_a_bot_without_documents() -> None:
    ans = answer("Leave requests should be planned around the working-day count.", [("t1", "8")])
    assert checks(ans, evidence={}, has_documents=False) == []
    assert "schema" in names(checks(ans, evidence={}))  # no tool fact reported: a document is needed


def test_prompt_states_the_document_rule_only_for_a_bot_with_documents() -> None:
    from kestrel.agent.prompt import system_prompt

    assert "never stand alone" in system_prompt("guide", extra_tools=["working_days_between"])
    assert "never stand alone" not in system_prompt("guide", extra_tools=["working_days_between"], documents=False)
