"""One chat turn: the screened question and the answer card with its outcome and checks."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from kestrel.contracts import AskResult, Outcome
from kestrel.errors import ErrorCategory

from kestrel_app.components import (
    OUTCOME_TONE,
    esc,
    evidence_badge,
    failed_checks,
    fmt_cost,
    fmt_latency,
    html_text,
    issue_line,
    md,
    outcome_badge,
    plural,
    repaired_line,
)
from kestrel_app.sources import source_name

CATEGORY_TEXT: dict[ErrorCategory, str] = {
    ErrorCategory.CONFIGURATION: "The assistant is not configured correctly. An operator can run `kestrel doctor` for the fix.",
    ErrorCategory.ACCESS_DENIED: "Your roles do not allow this assistant or this action.",
    ErrorCategory.PRIVACY_BLOCKED: "This assistant blocks questions that contain personal information. Remove it and ask again.",
    ErrorCategory.EGRESS_DENIED: "This assistant's data classification does not allow the selected model. Choose an on-device model.",
    ErrorCategory.PROVIDER_UNAVAILABLE: "The model provider could not be reached. Try again shortly.",
    ErrorCategory.BUDGET_EXCEEDED: "The question used up its budget of model calls, searches or time before an answer was ready.",
    ErrorCategory.NOT_INGESTED: "No documents have been ingested for this assistant yet. An operator can re-ingest on the Documents page.",
    ErrorCategory.INVALID_INPUT: "The question could not be processed as entered.",
    ErrorCategory.INTERNAL: "Something unexpected went wrong. The request was stopped and recorded in the audit log.",
}

EXPLAIN: dict[Outcome, str] = {
    Outcome.NOT_FOUND: "The approved documents do not answer this, so the assistant declined rather than guess.",
    Outcome.REFUSED: "This question is outside what this assistant is for.",
    Outcome.WITHHELD: "The answer is not shown because its evidence checks still failed after one repair.",
    Outcome.ERROR: "The request stopped before an answer was ready.",
    Outcome.PARTIAL: "The documents answer only part of this question.",
}
"""A short line under the badge that says what a non-answer means."""


def user_turn(result: AskResult) -> None:
    """The question as it was sent (after screening), with what screening removed."""
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(md(result.question))
        if result.redactions:
            count = len(result.redactions)
            kinds = ", ".join(sorted(set(result.redactions)))
            verb = "was" if count == 1 else "were"
            st.caption(
                f"{plural(count, 'item')} of personal information ({kinds}) {verb} removed before the "
                "question was sent. The text above is exactly what was sent."
            )


def usage_line(result: AskResult) -> str:
    """Model, calls, searches, latency and cost, in words."""
    usage = result.usage
    parts = [plural(usage.model_calls, "model call"), plural(usage.searches, "search", "searches"), fmt_latency(result.latency_ms)]
    if usage.model_calls or usage.cost_usd is not None:
        parts.append(fmt_cost(usage.cost_usd))
    if result.model:
        parts.insert(0, result.model)
    return " · ".join(parts)


def _failed_list(result: AskResult) -> None:
    lines = [f"- {md(issue_line(issue))}" for issue in failed_checks(result.evidence_report)]
    if lines:
        st.markdown("\n".join(lines))


def _body(result: AskResult) -> None:
    outcome = result.outcome
    if outcome == Outcome.WITHHELD:
        st.markdown("**These evidence checks still failed:**")
        _failed_list(result)
    elif result.answer:
        st.markdown(f'<div class="k-answer">{html_text(result.answer)}</div>', unsafe_allow_html=True)
        if result.evidence_report is not None and not result.evidence_report.passed:
            st.warning("Shown with a warning: these evidence checks failed. Read the quotes before relying on it.")
            _failed_list(result)
    fixed = result.evidence_report.fixed_in_repair if result.evidence_report else ()
    if fixed:
        st.caption("Fixed during repair: " + "; ".join(repaired_line(issue) for issue in fixed))
    if result.clarification:
        st.info(result.clarification, icon=":material/help:")
    if result.reason and outcome in {Outcome.NOT_FOUND, Outcome.REFUSED, Outcome.ERROR, Outcome.WITHHELD, Outcome.PARTIAL}:
        st.markdown(f"**Why:** {md(result.reason)}")
    if result.error_category is not None:
        text = CATEGORY_TEXT.get(result.error_category, CATEGORY_TEXT[ErrorCategory.INTERNAL])
        (st.error if outcome == Outcome.ERROR else st.info)(text)
    if result.calculations:
        lines = "".join(
            f'<div><code>{esc(c.expression)} = {esc(c.result)}</code><span class="k-rule">rule {esc(c.rule)}</span></div>'
            for c in result.calculations
        )
        st.markdown(
            f'<div class="k-calc"><div class="k-lbl">Calculation, recomputed by the application</div>{lines}</div>',
            unsafe_allow_html=True,
        )
    if result.caveats:
        notes = "".join(f'<div class="k-caveat"><b>Note:</b> {html_text(c)}</div>' for c in result.caveats)
        st.markdown(notes, unsafe_allow_html=True)
    if result.citations:
        body = "".join(f'<span class="k-chip k-label">{esc(c.label)} · {esc(source_name(c))}</span>' for c in result.citations)
        st.markdown(f'<div class="k-chips">{body}</div>', unsafe_allow_html=True)


def answer_turn(
    result: AskResult,
    *,
    index: int,
    selected: bool,
    flagged: bool,
    on_select: Callable[[], None],
    on_flag: Callable[[], None],
) -> None:
    """The answer card: outcome and check result, the answer or decline, calculations, caveats and a footer."""
    tone = OUTCOME_TONE.get(result.outcome, "neutral")
    state = "sel" if selected else "idle"
    with st.chat_message("assistant", avatar=":material/verified:"), st.container(key=f"card-{tone}-{state}-{index}", gap="small"):
        with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
            outcome_badge(result.outcome)
            evidence_badge(result)
            st.space("stretch")
            if selected:
                st.markdown('<span class="k-selected">Evidence shown</span>', unsafe_allow_html=True)
        if result.outcome in EXPLAIN:
            st.markdown(f'<div class="k-explain">{esc(EXPLAIN[result.outcome])}</div>', unsafe_allow_html=True)
        _body(result)
        with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
            st.caption(usage_line(result))
            st.space("stretch")
            if not selected:
                st.button("Show evidence", key=f"sel-{result.trace_id}-{index}", on_click=on_select, type="tertiary",
                          icon=":material/arrow_forward:")
            label = "Report a content gap" if result.outcome == Outcome.NOT_FOUND else "Flag this answer"
            st.button(
                "Flagged for review" if flagged else label,
                key=f"flag-{result.trace_id}-{index}",
                on_click=on_flag,
                disabled=flagged,
                icon=":material/flag:",
            )
