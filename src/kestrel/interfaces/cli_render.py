"""Plain-text rendering of answers and search results for the command line."""

from __future__ import annotations

import json

from kestrel.contracts import (
    AskResult,
    CheckIssue,
    DocumentStatus,
    EvidenceReport,
    Outcome,
    ResolvedCitation,
    SearchHit,
    SearchResult,
    repaired_detail,
)
from kestrel.interfaces.cli_runtime import out, safe

OUTCOME_LABELS: dict[Outcome, str] = {
    Outcome.ANSWERED: "Answered",
    Outcome.PARTIAL: "Partial",
    Outcome.NOT_FOUND: "Not in the documents",
    Outcome.REFUSED: "Refused",
    Outcome.WITHHELD: "Withheld",
    Outcome.ERROR: "Error",
}

EXIT_CODES: dict[Outcome, int] = {  # 2 is left to Typer, which uses it for a usage error
    Outcome.ANSWERED: 0,
    Outcome.PARTIAL: 0,
    Outcome.NOT_FOUND: 0,
    Outcome.REFUSED: 0,
    Outcome.WITHHELD: 3,
    Outcome.ERROR: 1,
}

SNIPPET_CHARS = 300


def exit_code(result: AskResult) -> int:
    """Process exit code for an answer: 0 for answered, partial, not found and refused; 3 when withheld; 1 on error."""
    return EXIT_CODES.get(result.outcome, 1)


def ask_json(result: AskResult) -> str:
    """The AskResult as indented JSON with non-ASCII characters escaped (exact, and console-safe)."""
    return json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=True)


def snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    """Collapse whitespace and cut `text` to about `limit` characters."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


def source_line(filename: str, locator: str) -> str:
    """`filename > locator`, without repeating the filename when the locator starts with it."""
    if not locator:
        return filename
    if locator == filename or locator.startswith(f"{filename} >") or locator.startswith(f"{filename}>"):
        return locator
    return f"{filename} > {locator}"


def _edition_note(status: DocumentStatus, edition: str | None) -> str:
    parts = []
    if edition:
        parts.append(f"edition {edition}")
    if status != DocumentStatus.CURRENT:
        parts.append(str(status.value))
    return f" ({', '.join(parts)})" if parts else ""


def outcome_line(result: AskResult) -> str:
    """`Answered - evidence checks passed`, or the outcome with the number of issues."""
    label = OUTCOME_LABELS.get(result.outcome, str(result.outcome))
    if result.outcome == Outcome.ERROR and result.error_category is not None:
        label = f"{label} ({result.error_category.value})"
    report = result.evidence_report
    if report is None:
        return label
    if report.passed:
        suffix = "evidence checks passed"
        if report.repaired:
            suffix += " after one repair"
        if report.fixed_in_repair:
            suffix += f" ({len(report.fixed_in_repair)} fixed during repair)"
        if report.warnings:
            suffix += f", {len(report.warnings)} warning(s)"
        return f"{label} - {suffix}"
    return f"{label} - evidence checks found {len(report.failures)} issue(s)"


def _issue_line(issue: CheckIssue, severity: bool = True) -> str:
    where = f" {issue.label}" if issue.label else ""
    tag = f"[{issue.severity}] " if severity else ""
    return f"  - {tag}{issue.check}{where}: {repaired_detail(issue)}"


def _issue_lines(report: EvidenceReport) -> list[str]:
    """Failed checks and warnings, then what the repair turn fixed. Pass or fail comes from `report.passed`."""
    lines = [_issue_line(i) for i in (*report.failures, *report.warnings)]
    if report.fixed_in_repair:
        lines.append("  Fixed during repair:")
        lines += [f"  {_issue_line(i, severity=False)}" for i in report.fixed_in_repair]
    return lines


def step_lines(result: AskResult) -> list[str]:
    """`How this was answered`: one numbered line per step."""
    lines = []
    for n, step in enumerate(result.steps, start=1):
        detail = f": {step.detail}" if step.detail else ""
        lines.append(f"  {n}. [{step.kind}] {step.label}{detail}")
    return lines


def redaction_notice(redactions: tuple[str, ...]) -> str | None:
    """`Personal information removed from the question: 2 items (email, id number)`."""
    if not redactions:
        return None
    kinds = ", ".join(sorted(set(redactions)))
    count = len(redactions)
    noun = "item" if count == 1 else "items"
    return f"Personal information removed from the question: {count} {noun} ({kinds})."


def footer(result: AskResult) -> str:
    """Model, calls, tokens, cost, latency and trace id on one line."""
    usage = result.usage
    cost = f"${usage.cost_usd:.4f}" if usage.cost_usd is not None else "cost unknown"
    calls = "call" if usage.model_calls == 1 else "calls"
    return (
        f"Model {result.model or 'none'} | {usage.model_calls} model {calls} | "
        f"{usage.input_tokens} in / {usage.output_tokens} out tokens | {cost} | "
        f"{result.latency_ms / 1000:.1f} s | trace {result.trace_id}"
    )


def _citation_lines(index: int, citation: ResolvedCitation) -> list[str]:
    if citation.is_tool:
        args = f" with arguments {citation.tool_arguments}" if citation.tool_arguments else ""
        lines = [f"[{index}] Tool result (not a document): {citation.tool_name} on server {citation.tool_server}{args}"]
    else:
        where = source_line(citation.filename, citation.locator)
        if citation.row_number is not None:
            where += f" > row {citation.row_number}"
        lines = [f"[{index}] {where}{_edition_note(citation.status, citation.edition)}"]
    lines.append(f'    "{" ".join(citation.quote.split())}"')
    if citation.supports:
        lines.append(f"    supports: {citation.supports}")
    return lines


def render_ask(result: AskResult, trace: bool = False) -> None:
    """Print an answer in the order a reader needs it."""
    out(outcome_line(result))
    if result.evidence_report is not None and result.evidence_report.issues:
        for line in _issue_lines(result.evidence_report):
            out(line)
    notice = redaction_notice(result.redactions)
    if notice:
        out(notice)
    if result.clarification:
        out()
        out(f"Please clarify: {result.clarification}")
    if result.answer:
        out()
        out(result.answer)
    if result.reason:
        out()
        out(f"Reason: {result.reason}")
    if result.caveats:
        out()
        out("Caveats:")
        for caveat in result.caveats:
            out(f"  - {caveat}")
    if result.calculations:
        out()
        out("Calculations:")
        for calc in result.calculations:
            out(f"  {calc.expression} = {calc.result}   (rule {calc.rule})")
    if result.citations:
        out()
        out("Sources:")
        for i, citation in enumerate(result.citations, start=1):
            for line in _citation_lines(i, citation):
                out(line)
    if trace:
        _render_trace(result)
    out()
    out(footer(result))


def _render_trace(result: AskResult) -> None:
    out()
    out("Trace:")
    if result.scope is not None:
        scope = result.scope
        statuses = ", ".join(sorted(s.value for s in scope.statuses))
        selector = f"edition {scope.edition}" if scope.edition else (
            f"effective on {scope.effective_on.isoformat()}" if scope.effective_on else f"status {statuses}"
        )
        out(f"  Scope: collections {', '.join(scope.collections)}; {selector}")
    usage = result.usage
    out(f"  Searches: {usage.searches}; repairs: {usage.repairs}")
    report = result.evidence_report
    if report is None:
        out("  Evidence checks: not run")
    else:
        state = "passed" if report.passed else "failed"
        repaired = " (after repair)" if report.repaired else ""
        fixed = f"; {len(report.fixed_in_repair)} fixed during repair" if report.fixed_in_repair else ""
        out(f"  Evidence checks: {state}{repaired}; {len(report.failures)} issue(s){fixed}")
        for line in _issue_lines(report):
            out(f"  {line}")
    if result.steps:
        out(f"  How this was answered ({len(result.steps)} steps):")
        for line in step_lines(result):
            out(f"  {line}")
    for tool in result.tool_results:
        out(f"  [{tool.label}] tool result: {tool.source_line}")
        out(f"      {snippet(tool.text)}")
    if not result.retrieved:
        out("  Retrieved evidence: none")
        return
    out(f"  Retrieved evidence ({len(result.retrieved)}):")
    for item in result.retrieved:
        hit = item.hit
        out(f"  [{item.label}] {source_line(hit.filename, hit.locator)} (score {hit.score:.3f})")
        out(f"      {snippet(item.text)}")


def _hit_lines(hit: SearchHit) -> list[str]:
    note = _edition_note(hit.status, hit.edition)
    return [
        f"[{hit.rank}] {source_line(hit.filename, hit.locator)}{note}  score {hit.score:.3f}",
        f"    section {hit.section_id}",
        f"    {snippet(hit.text)}",
    ]


def render_search(result: SearchResult) -> None:
    """Print search hits, or the clarification the service asked for."""
    for warning in result.warnings:
        out(f"Warning: {warning}")
    if result.clarification:
        out(f"Please clarify: {result.clarification}")
        return
    if not result.hits:
        out("No matching passages.")
        return
    for hit in result.hits:
        for line in _hit_lines(hit):
            out(line)


__all__ = [
    "EXIT_CODES",
    "OUTCOME_LABELS",
    "ask_json",
    "exit_code",
    "footer",
    "outcome_line",
    "redaction_notice",
    "render_ask",
    "render_search",
    "safe",
    "snippet",
    "source_line",
    "step_lines",
]
