"""The evidence panel beside the chat: citations, how the answer was produced, uncited excerpts."""

from __future__ import annotations

import streamlit as st

from kestrel.contracts import AskResult, Evidence, Outcome, ResolvedCitation

from kestrel_app import runtime
from kestrel_app.components import (
    ATTESTATION,
    CHECK_LABELS,
    checks_passed_text,
    failed_checks,
    md,
    plural,
    quote,
    repaired_line,
    section_label,
    status_badge,
)


def answer_steps(result: AskResult) -> list[str]:
    """'How this was answered': the steps the service recorded, in order, then the outcome and the audit record."""
    steps = [f"{step.label}: {step.detail}" if step.detail else step.label for step in result.steps]
    if not steps:
        steps.append("The request stopped before any step was taken")
    usage = result.usage
    if usage.model_calls:
        repairs = f", including {plural(usage.repairs, 'repair turn')}" if usage.repairs else ""
        steps.append(f"Model: {plural(usage.model_calls, 'call')} to {result.model or 'the model'}{repairs}")
    if result.outcome == Outcome.WITHHELD:
        steps.append(f"Answer withheld: {checks_passed_text(result.evidence_report)}")
    if result.outcome == Outcome.ERROR:
        category = result.error_category.value.replace("_", " ") if result.error_category else "unknown"
        steps.append(f"Request failed: {category}")
    steps.append(f"Audit record written (trace {result.trace_id})")
    return steps


def citation_check(result: AskResult, citation: ResolvedCitation) -> tuple[str, str] | None:
    """A short check result for one citation, with a colour; None when checks did not run."""
    report = result.evidence_report
    if report is None:
        return None
    issues = [i for i in failed_checks(report) if i.label == citation.label]
    if issues:
        return f"check failed: {CHECK_LABELS.get(issues[0].check, issues[0].check).lower()}", "red"
    return "quote matched", "blue"


def uncited(result: AskResult) -> list[Evidence]:
    """Excerpts the model was shown but the answer did not cite."""
    labels = {c.label for c in result.citations}
    bases = {label.split(".")[0] for label in labels}
    return [
        e for e in result.retrieved if e.label not in labels and not (e.row is None and e.label in bases)
    ]


@st.dialog("Whole section", width="large")
def section_dialog(bot_id: str, section_id: str) -> None:
    """Show one whole section, fetched through the service."""
    svc = runtime.attempt(runtime.service)
    if svc is None:
        return
    view = runtime.attempt(svc.get_section, bot_id, section_id, runtime.principal())
    if view is None:
        return
    st.markdown(f"**{md(view.title)}**  \n{md(view.filename)}")
    st.caption(f"{view.locator} · {view.edition or 'undated'} edition")
    status_badge(view.status)
    if view.table is not None:
        rows = []
        for row in view.table.rows:
            names = list(view.table.header) or [f"col{i + 1}" for i in range(len(row.cells))]
            rows.append({"row": row.row_number, **dict(zip(names, row.cells, strict=False))})
        st.dataframe(rows, hide_index=True)
    if view.text:
        quote(view.text)


def _citation(result: AskResult, citation: ResolvedCitation, index: int) -> None:
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"`{citation.label}`")
            check = citation_check(result, citation)
            if check is not None:
                st.badge(check[0], color=check[1])  # type: ignore[arg-type]
        st.markdown(f"**{md(citation.filename)}**")
        details = [citation.locator]
        if citation.row_number is not None:
            details.append(f"row {citation.row_number}")
        details += [f"{citation.edition or 'undated'} edition", citation.status.value]
        st.caption(" · ".join(details))
        quote(citation.quote)
        st.caption(f"Supports: {citation.supports}")
        if st.button("Open the whole section", key=f"sec-{result.trace_id}-{index}", type="tertiary"):
            section_dialog(result.bot_id, citation.section_id)


def render(result: AskResult | None) -> None:
    """Draw the evidence panel for `result` (the selected answer)."""
    st.subheader("Evidence for the selected answer")
    if result is None:
        st.caption("Ask a question to see its evidence here: every quote, where it comes from and how it was checked.")
        st.caption(ATTESTATION)
        return
    if result.citations:
        for index, citation in enumerate(result.citations):
            _citation(result, citation, index)
    else:
        st.caption("This answer cites no sources.")
    section_label("How this was answered")
    st.markdown("\n".join(f"{n}. {md(step)}" for n, step in enumerate(answer_steps(result), start=1)))
    fixed = result.evidence_report.fixed_in_repair if result.evidence_report else ()
    if fixed:
        section_label("Fixed during repair")
        st.markdown("\n".join(f"- {md(repaired_line(issue))}" for issue in fixed))
    others = uncited(result)
    if others:
        section_label("Also retrieved, not cited")
        for evidence in others:
            st.caption(f"{evidence.label} · {evidence.hit.title}, {evidence.hit.locator}")
    st.divider()
    st.caption(ATTESTATION)
