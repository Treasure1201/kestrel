"""The Sources tab: one card per citation, tool output cited as evidence, and the whole-section dialog."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from kestrel.contracts import AskResult, Outcome, ResolvedCitation

from kestrel_app import runtime
from kestrel_app.components import CHECK_LABELS, chips, esc, failed_checks, html_text, md, quote, status_badge

TOOL_NOTE = "Tool output, shown as data only"


def is_tool(citation: ResolvedCitation) -> bool:
    """Whether the result marks this citation as coming from a tool rather than a document."""
    return getattr(citation, "source", "document") == "tool"


def source_name(citation: ResolvedCitation) -> str:
    """The file name of a cited document, or `tool <name>` for cited tool output."""
    if is_tool(citation):
        return f"tool {getattr(citation, 'tool_name', None) or 'output'}"
    return citation.filename


def citation_check(result: AskResult, citation: ResolvedCitation) -> tuple[str, str] | None:
    """A short check result for one citation, with a colour; None when checks did not run."""
    report = result.evidence_report
    if report is None:
        return None
    issues = [i for i in failed_checks(report) if i.label == citation.label]
    if issues:
        return f"check failed: {CHECK_LABELS.get(issues[0].check, issues[0].check).lower()}", "red"
    return "quote matched", "green"


@st.dialog("Whole section", width="large")
def section_dialog(bot_id: str, section_id: str, cited: str = "", row: int | None = None) -> None:
    """Show one whole section, fetched through the service, with the cited text marked."""
    svc = runtime.attempt(runtime.service)
    if svc is None:
        return
    view = runtime.attempt(svc.get_section, bot_id, section_id, runtime.principal())
    if view is None:
        return
    st.markdown(f"### {md(view.title)}")
    with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
        status_badge(view.status)
        chips([view.filename, view.locator, f"{view.edition or 'undated'} edition"])
    if view.table is not None:
        width = max((len(r.cells) for r in view.table.rows), default=0)
        names = list(view.table.header) or [f"col{i + 1}" for i in range(width)]
        frame = pd.DataFrame([{"row": r.row_number, **dict(zip(names, r.cells, strict=False))} for r in view.table.rows])
        if row is not None:
            st.caption(f"The answer cites row {row}, marked below.")
        styled = frame.style.apply(
            lambda r: ["background-color:#FFE7A3;font-weight:600" if r["row"] == row else "" for _ in r], axis=1
        )
        st.dataframe(styled, hide_index=True)
    elif view.text:
        if cited and cited.lower().strip() in view.text.lower():
            st.caption("The quoted text is highlighted.")
        quote(view.text, highlight=cited)


def _tool_card(citation: ResolvedCitation, key: str) -> None:
    server = getattr(citation, "tool_server", None) or "unknown server"
    tool = getattr(citation, "tool_name", None) or "unknown tool"
    arguments = getattr(citation, "tool_arguments", None)
    with st.container(key=key, gap="xsmall"):
        with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
            st.badge(citation.label, color="blue")
            st.badge("Tool output", color="violet", icon=":material/build:")
        st.markdown(
            f'<div class="k-src-title">{esc(tool)}</div><div class="k-src-file">on server {esc(server)}</div>',
            unsafe_allow_html=True,
        )
        if arguments:
            st.markdown(f'<div class="k-src-file">Arguments: <span class="k-mono">{html_text(arguments)}</span></div>',
                        unsafe_allow_html=True)
        quote(citation.quote, as_data=True, note=TOOL_NOTE)
        st.markdown(f'<div class="k-supports"><b>Supports:</b> {html_text(citation.supports)}</div>', unsafe_allow_html=True)


def _document_card(result: AskResult, citation: ResolvedCitation, key: str, index: int) -> None:
    with st.container(key=key, gap="xsmall"):
        with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
            st.badge(citation.label, color="blue")
            status_badge(citation.status)
            st.badge(f"{citation.edition or 'undated'} edition", color="gray")
            check = citation_check(result, citation)
            if check is not None:
                st.badge(check[0], color=check[1])  # type: ignore[arg-type]
        st.markdown(
            f'<div class="k-src-title">{esc(citation.title)}</div><div class="k-src-file">{esc(citation.filename)}</div>',
            unsafe_allow_html=True,
        )
        where = citation.locator + (f" · row {citation.row_number}" if citation.row_number is not None else "")
        st.caption(where)
        quote(citation.quote)
        st.markdown(f'<div class="k-supports"><b>Supports:</b> {html_text(citation.supports)}</div>', unsafe_allow_html=True)
        if st.button("Open section", key=f"sec-{result.trace_id}-{index}", type="tertiary", icon=":material/open_in_full:"):
            section_dialog(result.bot_id, citation.section_id, citation.quote, citation.row_number)


def render(result: AskResult) -> None:
    """The Sources tab for one answer."""
    if not result.citations:
        if result.outcome in {Outcome.NOT_FOUND, Outcome.REFUSED}:
            reason = result.reason or result.clarification or "No reason was recorded."
            st.markdown(
                f'<div class="k-empty"><b>A decline cites no source.</b><br>{html_text(reason)}</div>',
                unsafe_allow_html=True,
            )
        elif result.outcome == Outcome.WITHHELD:
            st.markdown('<div class="k-empty">The answer was withheld, so its sources are not shown. '
                        "See <b>Checks</b> for what failed.</div>", unsafe_allow_html=True)
        else:
            st.markdown('<div class="k-empty">This answer cites no sources.</div>', unsafe_allow_html=True)
        return
    for index, citation in enumerate(result.citations):
        key = f"src-{index}"
        if is_tool(citation):
            _tool_card(citation, key)
        else:
            _document_card(result, citation, key, index)
