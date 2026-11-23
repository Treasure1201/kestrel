"""A cited tool result is shown as a tool result by the CLI and the MCP server, never as a document passage."""

from __future__ import annotations

from kestrel.contracts import AskResult, DocumentStatus, Outcome, ResolvedCitation
from kestrel.interfaces import cli_render
from kestrel.interfaces.mcp_models import ask_output

TOOL = ResolvedCitation(
    label="t1", quote="8", supports="the count", filename="Tool result", title="calendar / working_days_between",
    locator='{"end": "2026-03-20"}', document_id="", section_id="", status=DocumentStatus.CURRENT, source="tool",
    tool_server="calendar", tool_name="working_days_between", tool_arguments='{"end": "2026-03-20"}',
)
RESULT = AskResult(trace_id="t", bot_id="b", question="q", outcome=Outcome.ANSWERED, answer="8 days.", citations=(TOOL,))


def test_cli_labels_a_tool_citation_as_not_a_document(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    printed: list[str] = []
    monkeypatch.setattr(cli_render, "out", lambda *a: printed.append(" ".join(map(str, a))))
    cli_render.render_ask(RESULT)
    line = next(p for p in printed if p.startswith("[1]"))
    assert "Tool result (not a document): working_days_between on server calendar" in line


def test_mcp_output_marks_the_source_as_a_tool() -> None:
    citation = ask_output(RESULT).citations[0]
    assert citation.source == "tool" and citation.tool_name == "working_days_between" and citation.section_id == ""
