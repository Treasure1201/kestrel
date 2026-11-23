"""The evidence pane beside the chat: a compact header, then Sources, How it was answered, Checks and Other passages."""

from __future__ import annotations

import streamlit as st

from kestrel.contracts import AskResult, CheckIssue, Evidence, Outcome

from kestrel_app import sources
from kestrel_app.components import (
    ATTESTATION,
    CHECK_LABELS,
    checks_passed_text,
    esc,
    evidence_badge,
    failed_checks,
    html_text,
    issue_line,
    outcome_badge,
    plural,
    quote,
    repaired_line,
)

KIND_WORD: dict[str, str] = {
    "privacy": "Privacy",
    "scope": "Scope",
    "search": "Search",
    "table": "Table read",
    "calculate": "Calculation",
    "mcp_tool": "Tool call",
    "checks": "Checks",
    "repair": "Repair",
}

CHECK_EXPLAIN: dict[str, str] = {
    "schema": "The model's reply was not in the required answer format.",
    "label_retrieved": "A citation points at an excerpt that was not retrieved for this question.",
    "quote_verbatim": "A quote could not be found word for word in the excerpt it cites.",
    "table_row_cited": "A fact from a table cites the whole table instead of the row it comes from.",
    "numbers_grounded": "A number in the answer does not appear in any cited source.",
    "calculation_recomputed": "The application recomputed a calculation and got a different result.",
    "decline_reason": "The answer declined without saying why.",
    "source_status": "A cited source is not approved, not current, or outside this assistant's documents.",
}
"""What each evidence check looks for, in plain English."""

Step = tuple[str, str, str]
"""(kind word, label, detail) for one row of the timeline."""


def timeline(result: AskResult) -> list[Step]:
    """'How it was answered': the recorded steps in order, then the model calls, the outcome and the audit record."""
    steps: list[Step] = [(KIND_WORD.get(s.kind, s.kind), s.label, s.detail) for s in result.steps]
    if not steps:
        steps.append(("Stopped", "The request stopped before any step was taken", ""))
    usage = result.usage
    if usage.model_calls:
        repairs = f", including {plural(usage.repairs, 'repair turn')}" if usage.repairs else ""
        steps.append(("Model", f"{plural(usage.model_calls, 'call')} to {result.model or 'the model'}{repairs}", ""))
    if result.outcome == Outcome.WITHHELD:
        steps.append(("Outcome", "Answer withheld", checks_passed_text(result.evidence_report)))
    if result.outcome == Outcome.ERROR:
        category = result.error_category.value.replace("_", " ") if result.error_category else "unknown"
        steps.append(("Outcome", "Request failed", category))
    steps.append(("Audit", "Audit record written", f"trace {result.trace_id}"))
    return steps


def answer_steps(result: AskResult) -> list[str]:
    """The timeline as plain lines, `label: detail`."""
    return [f"{label}: {detail}" if detail else label for _, label, detail in timeline(result)]


def uncited(result: AskResult) -> list[Evidence]:
    """Excerpts the model was shown but the answer did not cite."""
    labels = {c.label for c in result.citations}
    bases = {label.split(".")[0] for label in labels}
    return [e for e in result.retrieved if e.label not in labels and not (e.row is None and e.label in bases)]


def _header(result: AskResult) -> None:
    st.markdown(
        f'<div class="k-evhead"><div class="k-lbl">Evidence for</div>'
        f'<div class="k-evq" title="{esc(result.question)}">{html_text(result.question)}</div></div>',
        unsafe_allow_html=True,
    )
    with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
        outcome_badge(result.outcome)
        evidence_badge(result)


def _steps_tab(result: AskResult) -> None:
    items = []
    for kind, label, detail in timeline(result):
        tone = " k-bad" if kind in {"Outcome", "Stopped"} or label.startswith("Tool call failed") else ""
        detail_html = f'<div class="k-detail">{html_text(detail)}</div>' if detail else ""
        items.append(
            f'<li class="{tone.strip()}"><div class="k-kind">{esc(kind)}</div>'
            f'<div class="k-step">{html_text(label)}</div>{detail_html}</li>'
        )
    st.markdown(f'<ol class="k-tl">{"".join(items)}</ol>', unsafe_allow_html=True)


def _issue_items(issues: list[CheckIssue], line: str) -> str:
    out = []
    for issue in issues:
        text = issue_line(issue) if line == "failed" else repaired_line(issue)
        explain = CHECK_EXPLAIN.get(issue.check, "")
        head = "Failed" if line == "failed" else "Fixed during repair"
        out.append(f"<li><b>{esc(head)}: {esc(CHECK_LABELS.get(issue.check, issue.check))}</b>"
                   f"{html_text(text)}<div class='k-src-file'>{esc(explain)}</div></li>")
    return f'<ul class="k-checklist">{"".join(out)}</ul>'


def _checks_tab(result: AskResult) -> None:
    report = result.evidence_report
    if report is None:
        st.markdown('<div class="k-empty"><b>Evidence checks were not run</b> for this request.</div>',
                    unsafe_allow_html=True)
    else:
        verdict = "Passed" if report.passed else "Failed"
        colour = "green" if report.passed else "red"
        with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
            st.badge(f"Evidence checks {verdict.lower()}", color=colour,  # type: ignore[arg-type]
                     icon=":material/check:" if report.passed else ":material/close:")
            st.caption(checks_passed_text(report))
        failures = failed_checks(report)
        if failures:
            st.markdown('<div class="k-lbl">Still failing</div>' + _issue_items(failures, "failed"), unsafe_allow_html=True)
        fixed = list(report.fixed_in_repair)
        if fixed:
            st.markdown('<div class="k-lbl">Fixed during repair</div>' + _issue_items(fixed, "fixed"), unsafe_allow_html=True)
        if report.warnings:
            st.markdown('<div class="k-lbl">Warnings (not failures)</div>', unsafe_allow_html=True)
            st.markdown("\n".join(f"- {esc(issue_line(w))}" for w in report.warnings))
    st.markdown(f'<div class="k-note">{html_text(ATTESTATION)}</div>', unsafe_allow_html=True)


def _others_tab(others: list[Evidence]) -> None:
    if not others:
        st.markdown('<div class="k-empty">Every passage retrieved for this question was cited.</div>', unsafe_allow_html=True)
        return
    st.caption("Retrieved for this question but not cited by the answer.")
    for evidence in others:
        with st.expander(f"{evidence.label} · {evidence.hit.title}, {evidence.hit.locator}"):
            quote(evidence.text)


def render(result: AskResult | None) -> None:
    """Draw the evidence pane for `result` (the selected answer)."""
    if result is None:
        st.markdown('<div class="k-lbl">Evidence</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="k-empty"><b>Ask a question to see its evidence here:</b> every quote, where it comes from, '
            "how the answer was produced and how it was checked.</div>",
            unsafe_allow_html=True,
        )
        st.markdown(f'<div class="k-note">{html_text(ATTESTATION)}</div>', unsafe_allow_html=True)
        return
    _header(result)
    others = uncited(result)
    tab_sources, tab_steps, tab_checks, tab_others = st.tabs(
        [f"Sources ({len(result.citations)})", "How it was answered", "Checks", f"Other passages ({len(others)})"]
    )
    with tab_sources:
        sources.render(result)
    with tab_steps:
        _steps_tab(result)
    with tab_checks:
        _checks_tab(result)
    with tab_others:
        _others_tab(others)
